#!/bin/bash
# Stop the AmicoScript meeting watcher and remove its autostart entry.
# Leaves the .venv and any captured recordings alone.
set -euo pipefail

UNIT="${AMICOSCRIPT_WATCHER_UNIT:-amicoscript-watcher.service}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now "$UNIT" 2>/dev/null || true
  rm -f "$UNIT_DIR/$UNIT"
  systemctl --user daemon-reload 2>/dev/null || true
fi

rm -f "$AUTOSTART_DIR/amicoscript-watcher.desktop"
pkill -f "watcher.py" 2>/dev/null || true

echo "Removed the meeting watcher autostart entry ($UNIT)."
echo "Recordings in ~/.amicoscript/data/meetings were left untouched."
