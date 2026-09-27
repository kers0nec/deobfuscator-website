#!/usr/bin/env bash
# Install the pinned Lune runtime into <repo>/bin.
#
# Lune (https://lune-org.github.io, MIT) is the Luau runtime that luau-vmp-deobf
# uses for its restricted prototype-capture sandbox. It is only needed for full
# Luraph v14.x devirtualization; everything else on this site works without it.
#
#   bash tools/install_lune.sh            # pinned version
#   bash tools/install_lune.sh 0.10.5     # explicit version
#
# Afterwards server.runtime.find_lune() picks it up from <repo>/bin, or point at
# it explicitly with LUNE_PATH=/path/to/lune.
set -euo pipefail

VERSION="${1:-${KERS_LUNE_VERSION:-0.10.5}}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIR="${KERS_LUNE_DIR:-$REPO_ROOT/bin}"
DEST="$DEST_DIR/lune"

if [[ -x "$DEST" ]]; then
    echo "[=] already installed: $DEST"
    exit 0
fi

case "$(uname -s)" in
    Linux)  OS="linux" ;;
    Darwin) OS="macos" ;;
    *) echo "[!] unsupported platform for automatic install: $(uname -s)" >&2
       echo "    download Lune manually from https://github.com/lune-org/lune/releases" >&2
       echo "    then set LUNE_PATH=/path/to/lune" >&2
       exit 1 ;;
esac

case "$(uname -m)" in
    x86_64|amd64)  ARCH="x86_64" ;;
    aarch64|arm64) ARCH="aarch64" ;;
    *) echo "[!] unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

NAME="lune-${VERSION}-${OS}-${ARCH}"
URL="https://github.com/lune-org/lune/releases/download/v${VERSION}/${NAME}.zip"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[*] fetching $URL"
if ! curl -fsSL --retry 3 --connect-timeout 20 -o "$TMP/lune.zip" "$URL"; then
    echo "[!] download failed." >&2
    echo "    Some networks block GitHub release assets (release-assets.githubusercontent.com)." >&2
    echo "    Download ${NAME}.zip yourself, unzip it, and put the binary at:" >&2
    echo "        $DEST" >&2
    echo "    or set LUNE_PATH to wherever it lives." >&2
    exit 1
fi

if command -v unzip >/dev/null 2>&1; then
    unzip -q "$TMP/lune.zip" -d "$TMP/x"
else
    python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
        "$TMP/lune.zip" "$TMP/x"
fi

FOUND="$(find "$TMP/x" -type f -name 'lune*' ! -name '*.zip' | head -1)"
if [[ -z "$FOUND" ]]; then
    echo "[!] archive did not contain a lune binary" >&2
    exit 1
fi

mkdir -p "$DEST_DIR"
install -m 0755 "$FOUND" "$DEST" 2>/dev/null || { cp "$FOUND" "$DEST"; chmod 0755 "$DEST"; }
echo "[+] installed $DEST"
"$DEST" --version 2>/dev/null || true
echo "[+] Luraph v14.x full devirtualization is now available"
