#!/usr/bin/env bash
# Plain-Python setup, no Docker.
#
#   bash setup.sh              # venv + deps + patched Luau (+ Lune if reachable)
#   bash setup.sh --no-lune    # skip the Lune download
#   bash setup.sh --system     # install into the current Python, not a venv
#
# Then:  . .venv/bin/activate && python run.py
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

WANT_LUNE=1
USE_VENV=1
for arg in "$@"; do
    case "$arg" in
        --no-lune) WANT_LUNE=0 ;;
        --system)  USE_VENV=0 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "[!] unknown option: $arg" >&2; exit 2 ;;
    esac
done

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "[!] python3 not found" >&2; exit 1; }

if [ "$USE_VENV" = 1 ]; then
    if [ ! -d .venv ]; then
        echo "[*] creating .venv"
        "$PY" -m venv .venv
    fi
    # shellcheck disable=SC1091
    . .venv/bin/activate
fi

echo "[*] installing python dependencies"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt
# build-time helpers for the Luau compile, if the system lacks them
command -v cmake >/dev/null || python -m pip install cmake ninja || true

echo "[*] building the patched Luau runtime (luau + luau-ast)"
if command -v git >/dev/null && command -v cmake >/dev/null; then
    python tools/build_luau.py --portable
else
    echo "[!] git and cmake are required to build Luau." >&2
    echo "    Debian/Ubuntu: apt-get install -y build-essential cmake ninja-build git" >&2
    echo "    macOS:       brew install cmake ninja git" >&2
    exit 1
fi

if [ "$WANT_LUNE" = 1 ]; then
    echo "[*] installing Lune (full Luraph v14.x devirtualization)"
    bash tools/install_lune.sh || \
        echo "[i] Lune unavailable; Luraph v14.x will unpack + trace only"
fi

echo
echo "[+] setup complete"
python -c "import sys; sys.path.insert(0,'.'); from server import runtime; \
import json; print(json.dumps({t['name']: ('ok' if t['found'] else 'MISSING') for t in runtime.health()['tools']}, indent=2))"
echo
echo "    start it with:  python run.py"
