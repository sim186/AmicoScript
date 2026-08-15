#!/bin/bash
# Install the AmicoScript meeting watcher as a per-user launchd agent that
# starts at login. No admin rights, nothing outside your home folder.
#
# The watcher only records while the "Meeting auto-capture" toggle in the
# AmicoScript web UI is ON, so it is safe to leave installed.
#
#   bash install-macos.sh          # install + start
#   bash uninstall-macos.sh        # stop + remove
set -euo pipefail

LABEL="${AMICOSCRIPT_WATCHER_LABEL:-org.amico.AmicoScript.watcher}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
VENV="$DIR/.venv"
LOG_DIR="${AMICOSCRIPT_WATCHER_OUT:-$HOME/.amicoscript/data/meetings}"
PORT="${AMICOSCRIPT_PORT:-8002}"

if [ ! -f "$DIR/watcher.py" ]; then
  echo "ERROR: watcher.py not found next to this installer ($DIR)." >&2
  exit 1
fi

echo "=== AmicoScript meeting watcher setup ==="
echo "Location: $DIR"

# --copies, not the default symlink, and this is the whole reason:
# macOS attaches the audio-recording permission to the *binary that asks*. A
# symlinked venv asks as the shared Homebrew/python.org interpreter, so the
# grant leaks to every other script on the machine and evaporates whenever that
# interpreter is upgraded. A private copy owns its own grant, at a path the
# user can recognise in System Settings.
if [ ! -x "$VENV/bin/python3" ]; then
  echo "[1/4] Creating a private Python environment..."
  python3 -m venv --copies "$VENV"
else
  echo "[1/4] Reusing the existing Python environment."
fi

echo "[2/4] Installing dependencies..."
"$VENV/bin/python3" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$VENV/bin/python3" -m pip install --quiet --disable-pip-version-check -r "$DIR/requirements.txt"

echo "[3/4] Registering the login agent..."
mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV/bin/python3</string>
        <string>$DIR/watcher.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ProcessType</key>
    <string>Background</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>AMICOSCRIPT_PORT</key>
        <string>$PORT</string>
    </dict>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/watcher.out.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/watcher.err.log</string>
</dict>
</plist>
PLIST_EOF

# bootout first so re-running picks up an edited watcher.py instead of leaving
# the old process in memory. It fails when nothing is loaded — that is fine.
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl kickstart -k "gui/$UID/$LABEL"

echo "[4/4] Done."
echo
echo "The watcher is running and will start again at every login."
echo "Turn recording on/off in AmicoScript → sidebar → Meeting auto-capture."
echo
echo "IMPORTANT — one manual step macOS cannot prompt for:"
echo "  System Settings › Privacy & Security › Screen & System Audio Recording"
echo "  Enable the entry for:"
echo "    $VENV/bin/python3"
echo "  Without it macOS hands the watcher a silent recording of your meetings"
echo "  rather than refusing outright. The watcher detects this and says so in"
echo "  its log, but it cannot fix it for you."
echo
echo "Logs: $LOG_DIR/watcher.log"
