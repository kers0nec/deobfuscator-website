"""Engine dispatch: which pipeline runs, in what order, and what comes back.

A job is a *plan* of steps. Most inputs need one; Luraph v14.x needs two,
because the engine that can lift it also needs a runtime that may not be
installed, and the generic sandbox trace still recovers genuinely useful
behaviour (requested URLs, ``loadstring`` payloads, built strings) whether or
not the lift succeeds.

Each step runs as a child process in its own process group. The engines spawn
grandchildren - ``luau`` for the harness, ``luau-ast`` for the lifters, Lune for
the capture sandbox - so a timeout has to kill the whole group or a runaway VM
outlives the job that started it.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import runtime

REPO_ROOT = runtime.REPO_ROOT
CADMIO_WORKER = os.path.join(REPO_ROOT, "server", "worker_cadmio.py")
LUAUVMP_WORKER = os.path.join(REPO_ROOT, "server", "worker_luauvmp.py")

EVENT_TYPES = {"log", "detect", "stage", "result", "artifact", "done", "error"}

# Defaults for a job that did not ask for specific limits. These are
# environment-tunable because the right value depends on the CPU the service
# actually has: a 0.1-CPU hosted instance needs several times the wall-clock
# budget of a laptop to walk the same VM, and without that it "fails" on work it
# would have finished given the time.
DEFAULT_TIMEOUT = int(os.environ.get("KERS_DEFAULT_TIMEOUT", 240))
DEFAULT_BUDGET = int(os.environ.get("KERS_DEFAULT_BUDGET", 30))
DEFAULT_DEVIRT_TIMEOUT = int(os.environ.get("KERS_DEFAULT_DEVIRT_TIMEOUT", 600))


@dataclass
class Step:
    engine: str
    label: str
    script: str
    args: List[str]
    timeout: int
    optional: bool = False


@dataclass
class JobOutcome:
    detection: Optional[dict] = None
    engine: str = ""
    kind: str = "none"          # devirtualized | trace | unpack | none
    result_path: Optional[str] = None
    result_name: str = "result.lua"
    result_bytes: int = 0
    artifacts: List[dict] = field(default_factory=list)
    log: List[str] = field(default_factory=list)
    steps: List[dict] = field(default_factory=list)
    error: Optional[str] = None
    seconds: float = 0.0
    runtime: Dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "detection": self.detection,
            "engine": self.engine,
            "kind": self.kind,
            "result_name": self.result_name if self.result_path else None,
            "result_bytes": self.result_bytes,
            "artifacts": [{"name": a["name"], "bytes": a["bytes"]} for a in self.artifacts],
            "log": self.log[-2000:],
            "steps": self.steps,
            "error": self.error,
            "seconds": self.seconds,
            "runtime": self.runtime,
        }


# Rank of a result kind: higher wins when several steps produce output.
KIND_RANK = {"none": 0, "unpack": 1, "trace": 2, "devirtualized": 3}


def plan_for(detection_name: str, opts: dict) -> List[Step]:
    """The ordered steps for a detected family."""
    timeout = int(opts.get("timeout", DEFAULT_TIMEOUT))
    budget = int(opts.get("budget", DEFAULT_BUDGET))
    devirt_timeout = int(opts.get("devirt_timeout", DEFAULT_DEVIRT_TIMEOUT))
    lune = runtime.find_lune()

    cadmio = lambda extra, t: Step(  # noqa: E731 - tiny local factory
        engine="cadmio", label="sandbox pipeline", script=CADMIO_WORKER,
        args=["--engine-dir", runtime.CADMIO_DEOBF, "--timeout", str(timeout),
              "--budget", str(budget)] + extra,
        timeout=t + 90,
    )
    luauvmp = lambda t: Step(  # noqa: E731
        engine="luauvmp", label="Luraph v14.x loader pipeline", script=LUAUVMP_WORKER,
        args=["--engine-dir", runtime.LUAUVMP_DIR, "--timeout", str(t)]
             + (["--runtime", lune] if lune else []),
        timeout=t + 180,
    )

    if detection_name == "luraph_v14":
        # Unpack + (maybe) devirtualize first: it is the only engine that
        # understands the v14 two-stream loader. Then always run the generic
        # sandbox trace, which reports what the payload actually does.
        return [luauvmp(devirt_timeout if lune else 300),
                cadmio(["--no-devirt"] if not opts.get("devirt", True) else [], timeout)]
    if detection_name == "luraph_v15":
        extra = [] if opts.get("devirt", True) else ["--no-devirt"]
        return [cadmio(extra + ["--obfuscator", "luraph_v15"], timeout)]
    if detection_name == "ironbrew1":
        extra = [] if opts.get("devirt", True) else ["--no-devirt"]
        return [cadmio(extra + ["--obfuscator", "ironbrew1"], timeout)]
    if detection_name == "plaintext":
        return []
    # everything else: behaviour trace through the generic plugin
    return [cadmio(["--no-devirt"], timeout)]


def _kill_group(proc: "subprocess.Popen") -> None:
    """Kill the child and everything it spawned."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            pass


def run_step(step: Step, input_path: str, workdir: str, opts: dict,
             emit: Callable[[dict], None]) -> dict:
    """Run one engine step, streaming its NDJSON events through ``emit``."""
    os.makedirs(workdir, exist_ok=True)
    argv = [sys.executable, "-u", step.script,
            "--input", input_path, "--workdir", workdir] + step.args
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if REPO_ROOT not in env.get("PYTHONPATH", ""):
        env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    # the sandbox never needs the caller's proxy/credentials
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "GITHUB_TOKEN", "GH_TOKEN"):
        env.pop(key, None)

    record = {"engine": step.engine, "label": step.label, "status": "running",
              "started": time.time()}
    emit({"t": "step", "engine": step.engine, "label": step.label, "status": "running"})

    result = {"kind": "none", "path": None, "bytes": 0}
    artifacts: List[dict] = []
    detection = None
    summary = None
    error = None
    timed_out = False

    try:
        popen_kwargs: dict = {"cwd": REPO_ROOT, "env": env, "stdout": subprocess.PIPE,
                              "stderr": subprocess.PIPE, "text": True, "errors": "replace",
                              "bufsize": 1}
        if os.name != "nt":
            popen_kwargs["preexec_fn"] = os.setsid   # own process group
        proc = subprocess.Popen(argv, **popen_kwargs)
    except OSError as exc:
        record.update(status="failed", error=str(exc))
        emit({"t": "log", "msg": "[%s] failed to start: %s" % (step.engine, exc)})
        return {"record": record, "result": result, "artifacts": artifacts,
                "detection": detection, "summary": summary, "error": str(exc)}

    deadline = time.time() + step.timeout
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if time.time() > deadline:
                timed_out = True
                emit({"t": "log", "msg": "[%s] time limit %ds reached - killing the process group"
                      % (step.engine, step.timeout)})
                _kill_group(proc)
                break
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                emit({"t": "log", "msg": "[%s] %s" % (step.engine, line[:2000])})
                continue
            kind = ev.get("t")
            if kind == "log":
                emit({"t": "log", "msg": "[%s] %s" % (step.engine, ev.get("msg", ""))})
            elif kind == "detect":
                detection = ev.get("detection")
                emit({"t": "detect", "detection": detection})
            elif kind == "stage":
                emit({"t": "stage", "engine": step.engine, **{k: v for k, v in ev.items()
                                                              if k != "t"}})
            elif kind == "result":
                result = {"kind": ev.get("kind", "none"), "path": ev.get("path"),
                          "bytes": int(ev.get("bytes") or 0)}
                emit({"t": "result", "engine": step.engine, "kind": result["kind"],
                      "bytes": result["bytes"]})
            elif kind == "artifact":
                artifacts.append({"name": ev.get("name", "artifact"),
                                  "path": ev.get("path"), "bytes": int(ev.get("bytes") or 0)})
                emit({"t": "artifact", "engine": step.engine, "name": ev.get("name"),
                      "bytes": int(ev.get("bytes") or 0)})
            elif kind == "done":
                summary = ev.get("summary")
            elif kind == "error":
                error = ev.get("msg")
                emit({"t": "log", "msg": "[%s] error: %s" % (step.engine, error)})
            else:
                emit({"t": "log", "msg": "[%s] %s" % (step.engine, line[:2000])})
    except (OSError, ValueError) as exc:
        error = error or "stream error: %s" % exc

    # drain stderr so a chatty child cannot deadlock on a full pipe
    try:
        err = proc.stderr.read() if proc.stderr else ""
    except (OSError, ValueError):
        err = ""
    for line in (err or "").splitlines()[-40:]:
        if line.strip():
            emit({"t": "log", "msg": "[%s!] %s" % (step.engine, line.strip()[:2000])})

    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
    rc = proc.returncode

    if timed_out:
        record.update(status="timeout", seconds=round(time.time() - record["started"], 2))
        error = error or "step exceeded %ds" % step.timeout
    elif rc == 0 and result["path"]:
        record.update(status="ok", seconds=round(time.time() - record["started"], 2))
    else:
        record.update(status="failed", returncode=rc,
                      seconds=round(time.time() - record["started"], 2))
        if error is None and rc != 0:
            error = "step exited %s" % rc
    if summary:
        record["summary"] = summary
    emit({"t": "step", "engine": step.engine, "label": step.label,
          "status": record["status"]})
    return {"record": record, "result": result, "artifacts": artifacts,
            "detection": detection, "summary": summary, "error": error}


def run(input_path: str, jobdir: str, detection_name: str, opts: dict,
        emit: Callable[[dict], None]) -> JobOutcome:
    """Execute the whole plan for one job."""
    started = time.time()
    outcome = JobOutcome(runtime=runtime.health())
    plan = plan_for(detection_name, opts)
    if not plan:
        outcome.kind = "none"
        outcome.error = "nothing to do for this input"
        outcome.seconds = round(time.time() - started, 2)
        return outcome

    best_rank = -1
    seen_artifacts = set()
    step_errors: List[str] = []
    for index, step in enumerate(plan, start=1):
        stepdir = os.path.join(jobdir, "step%d-%s" % (index, step.engine))
        emit({"t": "log", "msg": "step %d/%d: %s (%s)" % (index, len(plan), step.label, step.engine)})
        out = run_step(step, input_path, stepdir, opts, emit)
        outcome.steps.append(out["record"])
        if out["detection"] and not outcome.detection:
            outcome.detection = out["detection"]
        for art in out["artifacts"]:
            key = (art["name"], art["bytes"])
            if key in seen_artifacts or not art["path"] or not os.path.isfile(art["path"]):
                continue
            seen_artifacts.add(key)
            outcome.artifacts.append(art)
        res = out["result"]
        rank = KIND_RANK.get(res["kind"], 0)
        if res["path"] and os.path.isfile(res["path"]) and rank > best_rank:
            best_rank = rank
            outcome.kind = res["kind"]
            outcome.result_path = res["path"]
            outcome.result_bytes = res["bytes"]
            outcome.engine = step.engine
        if out["error"]:
            step_errors.append("%s: %s" % (step.engine, out["error"]))
        # stop early only if a later step could not possibly improve things
        if outcome.kind == "devirtualized" and index < len(plan) and step.engine == "cadmio":
            emit({"t": "log", "msg": "full devirtualization succeeded; skipping remaining steps"})
            break

    if not outcome.result_path:
        # Every engine that ran had something to say; show all of it. A single
        # step's message on its own is usually misleading - "not recognized as a
        # Luraph loader" from one engine does not mean the file is not Luraph,
        # only that *that* engine could not handle it.
        detail = "; ".join(step_errors) if step_errors else "no engine produced a result"
        joined = " ".join(step_errors).lower()
        # Only suggest truncation when something actually failed to parse;
        # guessing that at a user whose file is fine sends them looking in the
        # wrong place.
        if "expected identifier" in joined or "malformed" in joined or "parse error" in joined:
            hint = (" The script could not be parsed. If it was copied out of a message or a "
                    "web page it may be truncated - a protected Luau file that stops "
                    "mid-expression cannot be read by anything.")
        elif "timed out" in joined or "stuck in a loop" in joined:
            hint = (" The sandbox run hit its time limit. A larger `budget` sometimes gets "
                    "further, but a VM whose dispatch loop the tracer cannot see will not "
                    "finish at any budget.")
        else:
            hint = (" This obfuscator build is recognized but not supported by the bundled "
                    "engines - see the capability note in the report.")
        outcome.error = "nothing could be recovered from this input. %s.%s" % (detail, hint)
    elif step_errors:
        # one engine succeeded and another failed: worth recording, not fatal
        outcome.error = "partial: " + "; ".join(step_errors)
    outcome.seconds = round(time.time() - started, 2)
    return outcome


def stage_result(outcome: JobOutcome, jobdir: str) -> Optional[str]:
    """Copy the winning result into the job directory under a stable name."""
    if not outcome.result_path or not os.path.isfile(outcome.result_path):
        return None
    dest = os.path.join(jobdir, outcome.result_name)
    if os.path.abspath(dest) == os.path.abspath(outcome.result_path):
        return dest
    try:
        shutil.copyfile(outcome.result_path, dest)
    except OSError:
        return outcome.result_path
    outcome.result_path = dest
    # artifacts are downloadable by name; re-point any that lived in a step dir
    staged = []
    for art in outcome.artifacts:
        src = art.get("path")
        if not src or not os.path.isfile(src):
            continue
        name = art["name"].replace(os.sep, "_").replace("/", "_")
        target = os.path.join(jobdir, "artifacts", name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            shutil.copyfile(src, target)
        except OSError:
            continue
        staged.append({"name": name, "path": target, "bytes": art["bytes"]})
    outcome.artifacts = staged
    return dest
