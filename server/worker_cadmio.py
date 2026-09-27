"""Subprocess driver for the vendored Cadmio pipeline (Apache-2.0).

The web server never imports the engine itself. Every analysis runs as a fresh
child process executing this file, because the work involves running untrusted,
deliberately hostile Luau code: a crash, a hang, a memory blow-up or a
``sys.exit`` inside the pipeline takes the child down and leaves the server
standing. The child is killed on timeout and its output directory is discarded.

Protocol: newline-delimited JSON on stdout.

    {"t":"log",      "msg": "..."}
    {"t":"detect",   "detection": {...}}
    {"t":"stage",    "name": "trace", "status": "running"}
    {"t":"result",   "path": "...", "kind": "devirtualized|trace", "bytes": 123}
    {"t":"artifact", "name": "...", "path": "...", "bytes": 123}
    {"t":"done",     "summary": {...}}
    {"t":"error",    "msg": "..."}

The engine's own progress lines (``[*] devirt round 1: ...``) are written to
stderr; ``sys.stderr`` is rebound below so they arrive on the same ordered
stream as ``log`` events instead of racing it.

Usage:
    python -m server.worker_cadmio --engine-dir DIR --input FILE --workdir DIR \
        [--obfuscator NAME] [--timeout S] [--budget S] [--no-devirt] ...
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def emit(event_type: str, **fields) -> None:
    rec = {"t": event_type}
    rec.update(fields)
    try:
        sys.__stdout__.write(json.dumps(rec, default=str) + "\n")
        sys.__stdout__.flush()
    except Exception:  # noqa: BLE001 - a broken pipe must not mask the real error
        pass


class StreamBridge(io.TextIOBase):
    """Turn the engine's stderr chatter into ordered ``log`` events."""

    def __init__(self):
        self._buf = ""

    def write(self, text):
        if not text:
            return 0
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip()
            if line:
                emit("log", msg=line[:4000])
        return len(text)

    def flush(self):
        if self._buf.strip():
            emit("log", msg=self._buf.strip()[:4000])
            self._buf = ""


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--engine-dir", required=True, help="vendored engine/cadmio/deobf directory")
    ap.add_argument("--input", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--obfuscator", default=None, help="force a vendored plugin name")
    ap.add_argument("--timeout", type=int, default=240, help="hard timeout per harness run (s)")
    ap.add_argument("--budget", type=int, default=30, help="soft time budget for the traced script (s)")
    ap.add_argument("--executor", default="Wave", help="name returned by identifyexecutor()")
    ap.add_argument("--no-devirt", action="store_true", help="only produce the behaviour trace")
    ap.add_argument("--strings", action="store_true", help="also dump recovered strings")
    ap.add_argument("--input-text", default=None)
    ap.add_argument("--cfg", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--max-runs", type=int, default=12)
    ap.add_argument("--devirt-rounds", type=int, default=200)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    opts = parse_args(argv)
    engine_dir = os.path.abspath(opts.engine_dir)
    workdir = os.path.abspath(opts.workdir)
    os.makedirs(workdir, exist_ok=True)

    started = time.time()
    sys.stderr = StreamBridge()

    try:
        from server import branding, detectors  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        sys.stderr = sys.__stderr__
        emit("error", msg="server package not importable")
        return 2

    # 1. identify (all fifteen families, not just the two the engine can lift)
    try:
        with open(opts.input, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        emit("error", msg="cannot read input: %s" % exc)
        return 2

    detection = detectors.identify(raw)
    emit("detect", detection=detection.as_dict(),
         all=[r for r in detectors.all_detections(raw) if r["confidence"] > 0])

    # 2. load the vendored engine and apply site branding
    obfuscators = branding.install(engine_dir)
    if obfuscators is None:
        emit("error", msg="vendored engine failed to import from %s" % engine_dir)
        return 2

    try:
        import deob as engine_cli  # noqa: PLC0415 - provided by engine_dir on sys.path
        from obfuscators.base import Job  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        emit("error", msg="engine import failed: %s" % exc)
        return 2

    # 3. pick the plugin. Our detector is the authority on *what* it is; the
    #    vendored registry only knows luraph_v15, ironbrew1 and generic.
    plugin_name = opts.obfuscator
    if not plugin_name:
        plugin_name = {
            "luraph_v15": "luraph_v15",
            "ironbrew1": "ironbrew1",
        }.get(detection.name, "generic")

    try:
        plugin = obfuscators.by_name(plugin_name)
    except KeyError:
        emit("log", msg="plugin %r unavailable; falling back to the generic trace" % plugin_name)
        plugin = obfuscators.by_name("generic")
        plugin_name = "generic"

    # Build the args namespace from the engine's own parser so every attribute a
    # plugin might read exists with the upstream default.
    argv = [opts.input, "-o", os.path.join(workdir, "result.lua"), "--debug",
            "--timeout", str(opts.timeout), "--budget", str(opts.budget),
            "--executor", opts.executor, "--max-runs", str(opts.max_runs),
            "--devirt-rounds", str(opts.devirt_rounds), "--no-pypy"]
    if opts.no_devirt:
        argv.append("--no-devirt")
    if opts.strings:
        argv.append("--strings")
    if opts.input_text is not None:
        argv += ["--input-text", opts.input_text]
    for kv in opts.cfg:
        argv += ["--cfg", kv]
    try:
        args = engine_cli.parser().parse_args(argv)
    except SystemExit as exc:  # argparse rejected something
        emit("error", msg="engine argument error (exit %s)" % exc.code)
        return 2

    emit("stage", name="detect", status="done", plugin=plugin_name, label=plugin.label)
    emit("log", msg="engine plugin: %s (%s)" % (plugin.label, plugin_name))

    # 4. run
    source = raw.decode("latin-1")
    trace_path = os.path.join(workdir, "result.deobf.luau")
    job = Job(opts.input, source, args, trace_path, True, plugin.label)

    emit("stage", name="pipeline", status="running")
    result_path = None
    try:
        result_path = plugin.deobfuscate(job)
    except SystemExit as exc:
        emit("log", msg="engine exited early: %s" % exc)
    except Exception as exc:  # noqa: BLE001
        emit("log", msg="engine raised: %s: %s" % (type(exc).__name__, exc))
        emit("log", msg=traceback.format_exc(limit=6))

    if not result_path or not os.path.isfile(result_path):
        for cand in (trace_path, os.path.join(workdir, "result.lua")):
            if os.path.isfile(cand):
                result_path = cand
                break

    if not result_path or not os.path.isfile(result_path):
        emit("stage", name="pipeline", status="failed")
        emit("error", msg="the pipeline produced no result file")
        return 1

    # 5. decide what kind of result this is. A devirtualizer that fails falls
    #    back to the trace and says so on stderr; the reliable signal is whether
    #    the file carries the trace NOTE line.
    try:
        with open(result_path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        head = ""
    kind = "trace" if "reconstructed from observed behaviour" in head else "devirtualized"
    if detection.level == "trace" or plugin_name == "generic":
        kind = "trace"
    size = os.path.getsize(result_path)
    emit("result", path=result_path, kind=kind, bytes=size)

    # 6. every intermediate in the work dir is an artifact the user can download
    skip = {os.path.abspath(result_path)}
    for name in sorted(os.listdir(workdir)):
        p = os.path.join(workdir, name)
        if not os.path.isfile(p) or os.path.abspath(p) in skip:
            continue
        try:
            emit("artifact", name=name, path=p, bytes=os.path.getsize(p))
        except OSError:
            continue

    emit("done", summary={
        "engine": "cadmio",
        "plugin": plugin_name,
        "label": plugin.label,
        "detection": detection.as_dict(),
        "kind": kind,
        "result_bytes": size,
        "seconds": round(time.time() - started, 2),
    })
    sys.stderr = sys.__stderr__
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
