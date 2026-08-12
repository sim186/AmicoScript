#!/bin/bash
# Install the AmicoScript meeting watcher as a per-user service that starts at
# login. No root, nothing outside your home folder.
#
# The watcher only records while the "Meeting auto-capture" toggle in the
# AmicoScript web UI is ON, so it is safe to leave installed.
#
#   bash install-linux.sh          # install + start
#   bash uninstall-linux.sh        # stop + remove
set -euo pipefail

UNIT="${AMICOSCRIPT_WATCHER_UNIT:-amicoscript-watcher.service}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

if [ ! -f "$DIR/watcher.py" ]; then
  echo "ERROR: watcher.py not found next to this installer ($DIR)." >&2
  exit 1
fi

echo "=== AmicoScript meeting watcher setup ==="
echo "Location: $DIR"

if ! command -v pactl >/dev/null 2>&1; then
  echo
  echo "WARNING: 'pactl' not found. Call detection and recording both need it."
  echo "  Debian/Ubuntu:  sudo apt install pulseaudio-utils"
  echo "  Fedora:         sudo dnf install pulseaudio-utils"
  echo "The watcher will install and run, but it will not record until this is"
  echo "fixed — it reports the reason in the AmicoScript sidebar."
  echo
fi

if [ ! -x "$VENV/bin/python3" ]; then
  echo "[1/3] Creating a Python environment..."
  python3 -m venv "$VENV"
else
  echo "[1/3] Reusing the existing Python environment."
fi

echo "[2/3] Installing dependencies..."
"$VENV/bin/python3" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$VENV/bin/python3" -m pip install --quiet --disable-pip-version-check -r "$DIR/requirements.txt"

echo "[3/3] Registering autostart..."
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/$UNIT" <<UNIT_EOF
[Unit]
Description=AmicoScript meeting watcher
# The audio server is a user service too; starting first would just mean a few
# failed polls, but ordering is free.
After=pipewire.service pulseaudio.service

[Service]
Type=simple
ExecStart=$VENV/bin/python3 $DIR/watcher.py
WorkingDirectory=$DIR
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT_EOF
  systemctl --user daemon-reload
  systemctl --user enable --now "$UNIT"
  systemctl --user restart "$UNIT"
  echo "Installed systemd user unit: $UNIT"
else
  # No systemd user session (some minimal WMs, some containers): fall back to
  # the XDG autostart spec, which every desktop environment honours.
  mkdir -p "$AUTOSTART_DIR"
  cat > "$AUTOSTART_DIR/amicoscript-watcher.desktop" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Name=AmicoScript meeting watcher
Exec=$VENV/bin/python3 $DIR/watcher.py
X-GNOME-Autostart-enabled=true
NoDisplay=true
DESKTOP_EOF
  echo "systemd --user not available; installed an XDG autostart entry instead."
  nohup "$VENV/bin/python3" "$DIR/watcher.py" >/dev/null 2>&1 &
fi

echo
echo "Done. The watcher is running and will start again at every login."
echo "Turn recording on/off in AmicoScript → sidebar → Meeting auto-capture."
echo "Logs: ${AMICOSCRIPT_WATCHER_OUT:-$HOME/.amicoscript/data/meetings}/watcher.log"
