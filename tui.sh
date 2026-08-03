#!/usr/bin/env bash
# Convenience launcher: ensures TUI deps are installed, then runs the TUI.
# Usage: ./tui.sh [--api-url http://host:port] [--no-server] [--debug]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -c "import textual, httpx" >/dev/null 2>&1; then
  echo "Installing TUI dependencies..." >&2
  "$PYTHON" -m pip install -q -r tui/requirements.txt
fi

exec "$PYTHON" -m tui "$@"
