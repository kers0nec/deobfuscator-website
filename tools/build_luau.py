#!/usr/bin/env python3
"""Build the patched ``luau`` and ``luau-ast`` binaries this site runs on.

Both come from https://github.com/luau-lang/luau (MIT) with one patch: in
``VM/src/lveclib.cpp`` the vector type's metatable is normally frozen with
``lua_setreadonly``. Roblox does not freeze it - ``Vector3`` *is* the native
vector type there, and the engine hangs ``Magnitude``, ``Unit``, ``Dot``,
``Cross``, ``Lerp`` and friends off its metatable. The sandbox environment
(``engine/cadmio/deobf/envlog.luau``) installs those same members, which is
impossible against a frozen metatable: on a stock build, ``v:Dot(w)`` dies with
"attempt to index vector with 'Dot'". Leaving it writable is the whole patch.

The build approach follows Cadmio's ``deobf/build_luau.py`` (Apache-2.0, see
CREDITS.md); building ``luau-ast`` alongside ``luau`` is this site's addition,
since the vendored engine looks for both next to itself and only ``luau`` is
downloadable upstream for Windows.

Needs: git, cmake, a C++ compiler. Ninja is used when present.

    python tools/build_luau.py                 # build into engine/cadmio/deobf/bin
    python tools/build_luau.py --tag 0.739
    python tools/build_luau.py --portable      # no -march=native: copyable binary
    python tools/build_luau.py --src /path/to/luau   # reuse an existing checkout
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.path.join(REPO_ROOT, "engine", "cadmio", "deobf", "bin")
REPO = "https://github.com/luau-lang/luau.git"
TAG = "0.739"
TARGETS = ["Luau.Repl.CLI", "Luau.Ast.CLI"]
OUTPUTS = {"Luau.Repl.CLI": "luau", "Luau.Ast.CLI": "luau-ast"}

VECLIB = os.path.join("VM", "src", "lveclib.cpp")
FREEZE = "    lua_setreadonly(L, -1, true);\n    lua_pop(L, 1); // pop the metatable\n"
PATCHED = ("    // patched for deobfuscation: left writable so the sandbox can add\n"
           "    // Roblox's Vector3 members (Magnitude, Unit, Dot, Cross, Lerp, ...)\n"
           "    lua_pop(L, 1); // pop the metatable\n")


def run(cmd, cwd=None):
    print("[*] " + " ".join(str(c) for c in cmd), file=sys.stderr)
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True)


def patch(src: str) -> bool:
    """Freeze -> writable. Returns True when the source is patched."""
    path = os.path.join(src, VECLIB)
    if not os.path.isfile(path):
        sys.exit("[!] %s not found - is --src a Luau checkout?" % path)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if PATCHED in text or "lua_setreadonly(L, -1, true)" not in text:
        print("[=] vector metatable already writable", file=sys.stderr)
        return True
    count = text.count(FREEZE)
    if count != 1:
        sys.exit("[!] lveclib.cpp changed upstream: expected 1 freeze site, found %d.\n"
                 "    Patch createmetatable() by hand (drop the lua_setreadonly call)." % count)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace(FREEZE, PATCHED))
    print("[+] patched VM/src/lveclib.cpp", file=sys.stderr)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", default=TAG, help="Luau release tag (default %(default)s)")
    ap.add_argument("--src", help="existing checkout to build (default: fresh shallow clone)")
    ap.add_argument("--portable", action="store_true",
                    help="no -march=native, so the binary runs on other machines")
    ap.add_argument("--jobs", type=int, default=0, help="parallel build jobs (default: auto)")
    ap.add_argument("--keep-src", action="store_true", help="do not delete a temporary clone")
    args = ap.parse_args()

    for tool in ("git", "cmake"):
        if not shutil.which(tool):
            sys.exit("[!] %s not found - install it and re-run" % tool)
    if not (shutil.which("g++") or shutil.which("c++") or shutil.which("cl")):
        sys.exit("[!] no C++ compiler found (g++/c++/cl)")

    tmp = None
    src = args.src
    if not src:
        tmp = tempfile.mkdtemp(prefix="luau-build-")
        src = os.path.join(tmp, "luau")
        run(["git", "clone", "-q", "--depth", "1", "--branch", args.tag, REPO, src])
    src = os.path.abspath(src)
    patch(src)

    build = os.path.join(src, "build-kers")
    cfg = ["cmake", "-S", src, "-B", build, "-DCMAKE_BUILD_TYPE=Release",
           "-DLUAU_BUILD_TESTS=OFF"]
    if shutil.which("ninja"):
        cfg += ["-G", "Ninja"]
    if not shutil.which("cl") and (shutil.which("g++") or shutil.which("c++")):
        cxx = shutil.which("g++") or shutil.which("c++")
        cc = shutil.which("gcc") or shutil.which("cc") or cxx
        opt = "-O2" if args.portable else "-O3 -march=native"
        cfg += ["-DCMAKE_C_COMPILER=%s" % cc, "-DCMAKE_CXX_COMPILER=%s" % cxx,
                "-DCMAKE_C_FLAGS_RELEASE=%s -DNDEBUG" % opt,
                "-DCMAKE_CXX_FLAGS_RELEASE=%s -DNDEBUG" % opt]
    run(cfg)

    build_cmd = ["cmake", "--build", build, "--config", "Release", "--target"] + TARGETS
    jobs = args.jobs or (os.cpu_count() or 2)
    build_cmd += ["--parallel", str(max(1, min(jobs, 8)))]
    run(build_cmd)

    os.makedirs(BIN_DIR, exist_ok=True)
    exe_suffix = ".exe" if os.name == "nt" else ""
    wrote = []
    for target, name in OUTPUTS.items():
        exe = name + exe_suffix
        for cand in (os.path.join(build, "Release", exe), os.path.join(build, exe)):
            if os.path.isfile(cand):
                dest = os.path.join(BIN_DIR, exe)
                shutil.copy2(cand, dest)
                os.chmod(dest, 0o755)
                wrote.append(dest)
                print("[+] wrote %s (%d bytes)" % (dest, os.path.getsize(dest)), file=sys.stderr)
                break
        else:
            print("[!] %s not found under %s" % (exe, build), file=sys.stderr)

    if tmp and not args.keep_src:
        shutil.rmtree(tmp, ignore_errors=True)
    if len(wrote) != len(OUTPUTS):
        return 1
    print("[+] Luau %s ready in %s" % (args.tag, BIN_DIR), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
