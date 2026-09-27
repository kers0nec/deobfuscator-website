"""Subprocess driver for the vendored luau-vmp-deobf pipeline (MIT).

This engine handles the staged **Luraph v14.x** loaders, which the Cadmio
pipeline does not: v14 packs the VM interpreter and its custom bytecode into
two hex/base85 streams inside the loader itself.

It degrades in three steps so a job always yields something useful:

1. ``luraph-diagnose --json`` - static classification. Pure string work, no
   execution, always available.
2. ``luraph`` - static unpack. Extracts the VM interpreter source and the VM
   bytecode image. No sandbox needed, so this works everywhere.
3. ``luraph-full`` - the real devirtualization: capture the prototype tree in a
   restricted Lune sandbox, recover the sample-local dispatcher, lift every
   prototype. This needs the **Lune** runtime. When Lune is missing the job
   reports steps 1-2 and says plainly why it stopped.

``lua.expert`` (the optional remote readability post-pass) is always disabled
here: it uploads compiled bytecode to a third party, which a user of this site
never agreed to.

Same NDJSON-on-stdout protocol as ``worker_cadmio``.

Usage:
    python -m server.worker_luauvmp --engine-dir DIR --input FILE --workdir DIR \
        [--runtime lune] [--timeout S]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def emit(event_type: str, **fields) -> None:
    rec = {"t": event_type}
    rec.update(fields)
    try:
        sys.stdout.write(json.dumps(rec, default=str) + "\n")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass


def log(msg: str) -> None:
    emit("log", msg=str(msg)[:4000])


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--engine-dir", required=True, help="vendored engine/luauvmp directory")
    ap.add_argument("--input", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--runtime", default=None, help="Lune executable (default: search PATH)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--split-protos", action="store_true")
    return ap.parse_args(argv)


def run_cli(engine_dir: str, args: list, timeout: int, label: str) -> tuple:
    """Run `python -m luauvmp <args>`, streaming its output as log events."""
    env = dict(os.environ)
    env["PYTHONPATH"] = engine_dir + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    cmd = [sys.executable, "-m", "luauvmp"] + args
    log("[%s] %s" % (label, " ".join(args)))
    try:
        proc = subprocess.Popen(cmd, cwd=engine_dir, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
    except OSError as exc:
        log("[%s] failed to start: %s" % (label, exc))
        return 127, ""
    out = []
    assert proc.stdout is not None
    deadline = time.time() + timeout
    for line in proc.stdout:
        out.append(line)
        stripped = line.rstrip()
        if stripped:
            log("[%s] %s" % (label, stripped))
        if time.time() > deadline:
            proc.kill()
            log("[%s] killed: exceeded %ds" % (label, timeout))
            break
    proc.wait(timeout=30)
    return proc.returncode, "".join(out)


def find_lune(explicit: str | None) -> str | None:
    if explicit:
        return explicit if (os.path.sep in explicit or shutil.which(explicit)) else None
    for cand in ("lune", "lune.exe"):
        found = shutil.which(cand)
        if found:
            return found
    env_path = os.environ.get("LUNE_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path
    return None


def main(argv=None) -> int:
    opts = parse_args(argv)
    engine_dir = os.path.abspath(opts.engine_dir)
    workdir = os.path.abspath(opts.workdir)
    os.makedirs(workdir, exist_ok=True)
    started = time.time()

    from server import branding, detectors  # noqa: PLC0415

    with open(opts.input, "rb") as fh:
        raw = fh.read()
    detection = detectors.identify(raw)
    emit("detect", detection=detection.as_dict())

    summary = {"engine": "luauvmp", "detection": detection.as_dict(),
               "stages": {}, "kind": "unpack", "seconds": 0.0}

    # ---- stage 1: static diagnosis (never executes the input)
    rc, out = run_cli(engine_dir, ["luraph-diagnose", opts.input, "--json"],
                      timeout=120, label="diagnose")
    diag = None
    try:
        diag = json.loads(out[out.index("{"):out.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        pass
    summary["stages"]["diagnose"] = {"ok": rc == 0 and bool(diag), "report": diag}
    if not diag or not diag.get("detected"):
        emit("error", msg="luau-vmp did not recognize this as a Luraph v14.x loader "
                          "(layout: %s)" % ((diag or {}).get("layout", "unknown")))
        summary["kind"] = "none"
        summary["seconds"] = round(time.time() - started, 2)
        emit("done", summary=summary)
        return 1

    # ---- stage 2: static unpack (VM interpreter source + bytecode image)
    unpack_base = os.path.join(workdir, "unpacked")
    rc, _ = run_cli(engine_dir, ["luraph", opts.input, "-o", unpack_base],
                    timeout=300, label="unpack")
    vm_path, bc_path = unpack_base + ".vm.lua", unpack_base + ".bytecode.bin"
    unpacked = os.path.isfile(vm_path) and os.path.isfile(bc_path)
    summary["stages"]["unpack"] = {
        "ok": unpacked,
        "vm_bytes": os.path.getsize(vm_path) if os.path.isfile(vm_path) else 0,
        "bytecode_bytes": os.path.getsize(bc_path) if os.path.isfile(bc_path) else 0,
    }
    if unpacked:
        emit("artifact", name="vm.lua", path=vm_path, bytes=os.path.getsize(vm_path))
        emit("artifact", name="bytecode.bin", path=bc_path, bytes=os.path.getsize(bc_path))
        # The recovered interpreter is the most readable thing stage 2 produces,
        # so it stands in as the result until stage 3 beats it.
        emit("result", path=vm_path, kind="unpack", bytes=os.path.getsize(vm_path))
    else:
        log("static unpack produced no VM/bytecode pair")

    # ---- stage 3: full devirtualization (needs Lune)
    lune = find_lune(opts.runtime)
    summary["stages"]["devirt"] = {"ok": False, "runtime": lune or None}
    if not lune:
        log("Lune runtime not found: skipping the capture + devirtualization stage.")
        log("Install it with tools/install_lune.sh, or set LUNE_PATH=/path/to/lune.")
        summary["stages"]["devirt"]["reason"] = "lune-missing"
        emit("stage", name="devirt", status="skipped", reason="lune-missing")
    else:
        emit("stage", name="devirt", status="running", runtime=lune)
        recdir = os.path.join(workdir, "recovered")
        cmd = ["luraph-full", opts.input, "-o", recdir, "--runtime", lune,
               "--timeout", str(opts.timeout), "--no-lua-expert", "--force", "--keep-failed"]
        if opts.split_protos:
            cmd.append("--split-protos")
        rc, _ = run_cli(engine_dir, cmd, timeout=opts.timeout + 120, label="devirt")
        best = None
        for name in ("program.pseudo.lua", "program.decompiled.luau", "program.luaexpert.luau"):
            cand = os.path.join(recdir, name)
            if os.path.isfile(cand) and os.path.getsize(cand) > 0:
                best = cand
                break
        if best:
            size = os.path.getsize(best)
            # stamp the site header onto the recovered program
            try:
                body = open(best, "r", encoding="utf-8", errors="replace").read()
                stamped = os.path.join(workdir, "result.lua")
                with open(stamped, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(branding.credit_lines(detection.label, kind="devirtualized") + "\n" + body)
                best = stamped
                size = os.path.getsize(stamped)
            except OSError as exc:
                log("could not re-stamp result: %s" % exc)
            emit("result", path=best, kind="devirtualized", bytes=size)
            summary["stages"]["devirt"].update({"ok": True, "bytes": size})
            summary["kind"] = "devirtualized"
            for root, _dirs, files in os.walk(recdir):
                for fname in sorted(files):
                    p = os.path.join(root, fname)
                    rel = os.path.relpath(p, recdir)
                    try:
                        emit("artifact", name=rel, path=p, bytes=os.path.getsize(p))
                    except OSError:
                        continue
        else:
            log("devirtualization finished with no recovered program")
            summary["stages"]["devirt"]["reason"] = "no-output"
            emit("stage", name="devirt", status="failed", reason="no-output")

    summary["seconds"] = round(time.time() - started, 2)
    emit("done", summary=summary)
    return 0 if summary["kind"] != "none" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
