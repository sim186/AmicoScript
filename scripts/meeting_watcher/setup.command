#!/bin/bash
# AmicoScript meeting watcher — one-step setup for macOS.
#
# Works two ways, like setup.bat does on Windows:
#   - run from the project (scripts/meeting_watcher/) -> uses the files here.
#   - downloaded on its own -> fetches the rest from the running app
#     (set AMICO_URL to override the URL).
#
# A file downloaded by a browser is not executable and is quarantined, so
# double-clicking it will not work. Run it instead:
#
#     bash ~/Downloads/setup.command
set -euo pipefail

AMICO_URL="${AMICO_URL:-http://localhost:8002}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== AmicoScript meeting watcher setup ==="

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found." >&2
  echo "Install it from https://www.python.org/downloads/ (or 'brew install python')," >&2
  echo "then run this file again." >&2
  exit 1
fi

if [ ! -f "$SRC/watcher.py" ]; then
  SRC="$HOME/Library/Application Support/AmicoScript/watcher"
  echo "Fetching watcher files from $AMICO_URL ..."
  mkdir -p "$SRC/watcher_platform"
  # watcher_platform/ holds the detection and capture backends — watcher.py
  # refuses to start without them, so they are not optional extras.
  for f in watcher.py requirements.txt install-macos.sh uninstall-macos.sh diag.py \
           watcher_platform/__init__.py watcher_platform/macos.py \
           watcher_platform/coreaudio.py; do
    if ! curl -fsSL "$AMICO_URL/scripts/meeting_watcher/$f" -o "$SRC/$f"; then
      echo "ERROR: could not download $f from $AMICO_URL." >&2
      echo "Make sure AmicoScript is running, then try again." >&2
      exit 1
    fi
  done
fi

bash "$SRC/install-macos.sh"
