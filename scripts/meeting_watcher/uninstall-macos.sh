#!/bin/bash
# Stop the AmicoScript meeting watcher and remove its login agent.
# Leaves the .venv and any captured recordings alone.
set -euo pipefail

LABEL="${AMICOSCRIPT_WATCHER_LABEL:-org.amico.AmicoScript.watcher}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
rm -f "$PLIST"

echo "Removed the meeting watcher login agent ($LABEL)."
echo "Recordings in ~/.amicoscript/data/meetings were left untouched."
echo "You can also remove its permission entry under"
echo "  System Settings › Privacy & Security › Screen & System Audio Recording."
