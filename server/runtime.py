"""Native runtime provisioning and health.

Two external binaries decide how much this site can do:

``luau`` / ``luau-ast``
    Roblox's Luau CLI, built with one patch: the vector type's metatable is
    left writable so the sandbox environment can install Roblox's ``Vector3``
    members (``v:Dot(w)``, ``v.Magnitude``). Stock Luau freezes that metatable,
    and protected scripts use those members constantly. Required by the Cadmio
    pipeline for *every* job, including plain behaviour traces.

``lune``
    The Luau runtime luau-vmp-deobf uses for its restricted prototype-capture
    sandbox. Only needed for Luraph v14.x full devirtualization. Without it the
    v14 path still diagnoses and statically unpacks the loader.

Nothing here runs at import time. ``ensure_luau()`` is called by setup and by
the health endpoint; it reports what is missing rather than guessing.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from typing import List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CADMIO_DEOBF = os.path.join(REPO_ROOT, "engine", "cadmio", "deobf")
CADMIO_BIN = os.path.join(CADMIO_DEOBF, "bin")
LUAUVMP_DIR = os.path.join(REPO_ROOT, "engine", "luauvmp")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")

LUAU_TAG = os.environ.get("KERS_LUAU_TAG", "0.739")
LUNE_VERSION = os.environ.get("KERS_LUNE_VERSION", "0.10.5")


@dataclass
class ToolStatus:
    name: str
    found: bool
    path: Optional[str] = None
    version: Optional[str] = None
    required_for: str = ""
    how_to_install: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _exe(name: str) -> str:
    return name + ".exe" if os.name == "nt" else name


def find_luau() -> Optional[str]:
    """The patched Luau REPL the Cadmio harness runs scripts in."""
    local = os.path.join(CADMIO_BIN, _exe("luau"))
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return local
    override = os.environ.get("LUAU_PATH")
    if override and os.path.isfile(override):
        return override
    found = shutil.which(_exe("luau"))
    return found


def find_luau_ast() -> Optional[str]:
    """luau-ast prints a file's AST as JSON; the lifters are built on it.

    Unlike ``luau`` the vendored engine looks for this one *only* next to
    itself, so a PATH copy is not enough - ``ensure_luau`` links it into place.
    """
    local = os.path.join(CADMIO_BIN, _exe("luau-ast"))
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return local
    override = os.environ.get("LUAU_AST_PATH")
    if override and os.path.isfile(override):
        return override
    return shutil.which(_exe("luau-ast"))


def find_lune() -> Optional[str]:
    override = os.environ.get("LUNE_PATH")
    if override and os.path.isfile(override):
        return override
    # tools/install_lune.sh puts it here
    local = os.path.join(REPO_ROOT, "bin", _exe("lune"))
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return local
    for cand in ("lune", "lune.exe"):
        found = shutil.which(cand)
        if found:
            return found
    return None


def find_python_for_luauvmp() -> Optional[str]:
    """luau-vmp-deobf needs ``zstandard``; report whether this interpreter has it."""
    try:
        import zstandard  # noqa: F401
        return sys.executable
    except ImportError:
        return None


def _stage_into_engine_bin() -> List[str]:
    """Make sure both binaries sit where the vendored engine looks for them."""
    notes = []
    os.makedirs(CADMIO_BIN, exist_ok=True)
    pairs = [(find_luau, "luau"), (find_luau_ast, "luau-ast")]
    for finder, name in pairs:
        src = finder()
        target = os.path.join(CADMIO_BIN, _exe(name))
        if not src or os.path.abspath(src) == os.path.abspath(target):
            continue
        if os.path.exists(target) or os.path.islink(target):
            continue
        try:
            if os.name != "nt":
                os.symlink(os.path.abspath(src), target)
            else:
                shutil.copy2(src, target)
            notes.append("linked %s -> %s" % (target, src))
        except OSError as exc:
            try:
                shutil.copy2(src, target)
                notes.append("copied %s -> %s" % (target, src))
            except OSError as exc2:
                notes.append("could not stage %s: %s / %s" % (name, exc, exc2))
    return notes


def build_luau(tag: str = LUAU_TAG, timeout: int = 2400) -> dict:
    """Build the patched Luau binaries from source.

    Needs git, cmake and a C++ compiler. Returns a report; never raises, so the
    health endpoint can show what went wrong instead of 500-ing.
    """
    script = os.path.join(TOOLS_DIR, "build_luau.py")
    if not os.path.isfile(script):
        return {"ok": False, "error": "tools/build_luau.py missing"}
    missing = [t for t in ("git", "cmake") if not shutil.which(t)]
    if not (shutil.which("g++") or shutil.which("c++") or shutil.which("cl")):
        missing.append("c++ compiler")
    if missing:
        return {"ok": False, "error": "missing build tools: " + ", ".join(missing)}
    try:
        proc = subprocess.run([sys.executable, script, "--tag", tag],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "build timed out after %ds" % timeout}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    ok = proc.returncode == 0 and bool(find_luau()) and bool(find_luau_ast())
    return {"ok": ok, "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}


def install_lune(timeout: int = 600) -> dict:
    """Download the pinned Lune release. Returns a report; never raises."""
    script = os.path.join(TOOLS_DIR, "install_lune.sh")
    if os.name == "nt" or not os.path.isfile(script):
        return {"ok": False,
                "error": "automatic Lune install is Linux/macOS only; download it from "
                         "https://github.com/lune-org/lune/releases and set LUNE_PATH"}
    try:
        proc = subprocess.run(["bash", script, LUNE_VERSION], capture_output=True,
                              text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": proc.returncode == 0 and bool(find_lune()),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-3000:], "stderr": proc.stderr[-3000:]}


def health() -> dict:
    """Everything the UI needs to explain what will and will not work here."""
    _stage_into_engine_bin()
    luau, luau_ast, lune = find_luau(), find_luau_ast(), find_lune()
    py = find_python_for_luauvmp()
    tools = [
        ToolStatus(
            name="luau", found=bool(luau), path=luau, required_for="every job (sandbox VM)",
            how_to_install="python tools/build_luau.py  (needs git, cmake, a C++ compiler)",
        ),
        ToolStatus(
            name="luau-ast", found=bool(luau_ast), path=luau_ast,
            required_for="devirtualization (AST lifting)",
            how_to_install="python tools/build_luau.py",
        ),
        ToolStatus(
            name="lune", found=bool(lune), path=lune, version=LUNE_VERSION if lune else None,
            required_for="Luraph v14.x full devirtualization",
            how_to_install="bash tools/install_lune.sh %s" % LUNE_VERSION,
        ),
        ToolStatus(
            name="zstandard", found=bool(py), path=py,
            required_for="Luraph v14.x loader unpacking",
            how_to_install="pip install zstandard",
        ),
    ]
    return {
        "tools": [t.as_dict() for t in tools],
        "core_ready": bool(luau and luau_ast),
        "luraph_v14_full_ready": bool(lune and py),
        "luraph_v14_unpack_ready": bool(py),
        "engines": {
            "cadmio": {"dir": CADMIO_DEOBF, "present": os.path.isdir(CADMIO_DEOBF)},
            "luauvmp": {"dir": LUAUVMP_DIR, "present": os.path.isdir(LUAUVMP_DIR)},
        },
        "site_name": os.environ.get("KERS_SITE_NAME", "kers0ne website"),
        "luau_tag": LUAU_TAG,
        "lune_version": LUNE_VERSION,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(health(), indent=2))
