"""Settings endpoints."""

import asyncio
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException

import state
from llm_providers import in_container as _in_container

# Reached through the module, not imported by name: several route handlers here
# are named after the operation they expose (save_settings, get_settings) and
# would otherwise shadow the store function they call.
import settings

router = APIRouter()

# A recording heartbeat older than this (seconds) is treated as idle, so a
# crashed/killed watcher never leaves the UI stuck showing "recording".
WATCHER_STATUS_TTL = 8.0
# No heartbeat at all within this window means the watcher isn't running, so the
# UI shows its one-time setup prompt. Must exceed the watcher's heartbeat period.
WATCHER_ALIVE_TTL = 15.0

# Mirrors backend/main.py's BASE_DIR/SCRIPTS_DIR resolution (PyInstaller bundle
# vs running from source) so this route finds the same scripts/ the app served.
if hasattr(sys, "_MEIPASS"):
    _BASE_DIR = Path(sys._MEIPASS)
else:
    _BASE_DIR = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _BASE_DIR / "scripts" if (_BASE_DIR / "scripts").exists() else _BASE_DIR.parent / "scripts"
_WATCHER_SRC_DIR = _SCRIPTS_DIR / "meeting_watcher"


def _bundled_watcher_version() -> str:
    """Version of the watcher.py shipped with the *running* app, parsed without
    importing it (it pulls in Windows-only audio deps we don't want to load
    just to read a constant)."""
    try:
        text = (_WATCHER_SRC_DIR / "watcher.py").read_text(encoding="utf-8")
        m = re.search(r'WATCHER_VERSION\s*=\s*["\']([^"\']+)["\']', text)
        return m.group(1) if m else ""
    except Exception:
        return ""


# Sentinel the UI posts back for a masked secret it did not touch, so saving
# an unrelated setting cannot overwrite a stored token with its own bullets.
_UNCHANGED = "__unchanged__"


def _to_bool(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _require_session_token(token: str) -> None:
    if not getattr(state, "exit_token", "") or token != state.exit_token:
        raise HTTPException(403, "Invalid session token")


def _mask_secret(value: str) -> str:
    """Show that a secret exists without handing it back out.

    GET /api/settings used to return the Hugging Face token in full. Even
    behind authentication there is no reason to keep echoing a credential to
    every client that asks — the UI only needs to know whether one is stored.
    """
    if not value:
        return ""
    return f"••••••••{value[-4:]}" if len(value) > 4 else "••••••••"


@router.get("/api/settings")
def get_settings() -> dict:
    stored = settings.load_settings()
    defaults = settings.get_transcription_defaults()
    ws = settings.get_whisper_settings()
    hf_token = stored.get("hf_token", "")
    return {
        "hf_token_set": bool(hf_token),
        "hf_token_preview": _mask_secret(hf_token),
        "exit_token": getattr(state, "exit_token", ""),
        "meeting_capture_enabled": settings.get_meeting_capture_enabled(),
        "auto_summarize_meetings": settings.get_auto_summarize_meetings(),
        # Shared by the web UI and the meeting watcher so auto-captured
        # meetings use the same model / language / diarize as manual uploads.
        "default_model": defaults["default_model"],
        "default_language": defaults["default_language"],
        "default_diarize": defaults["default_diarize"],
        "whisper_model": ws["whisper_model"],
        "whisper_device": ws["whisper_device"],
        "whisper_compute": ws["whisper_compute"],
    }


@router.post("/api/settings")
async def save_settings(
    hf_token: str | None = Form(None),
    model: str | None = Form(None),
    language: str | None = Form(None),
    diarize: str | None = Form(None),
    whisper_model: str | None = Form(None),
    whisper_device: str | None = Form(None),
    whisper_compute: str | None = Form(None),
    auto_summarize_meetings: str | None = Form(None),
) -> dict:
    """Persist HF token and/or transcription defaults.

    Fields are optional so the UI can save the HF token alone (existing
    behaviour) or push model/language/diarize without clearing the token.
    """
    stored = settings.load_settings()
    if hf_token is not None and hf_token != _UNCHANGED:
        stored["hf_token"] = hf_token
        settings.save_settings(stored)
    if auto_summarize_meetings is not None:
        settings.set_auto_summarize_meetings(_to_bool(auto_summarize_meetings))
    if model is not None or language is not None or diarize is not None:
        settings.set_transcription_defaults(
            model=model,
            language=language,
            diarize=_to_bool(diarize) if diarize is not None else None,
        )
    # Any one of the three is enough to save. Gating on whisper_model meant a
    # request that set only the device silently did nothing.
    if whisper_model or whisper_device or whisper_compute:
        ws = settings.get_whisper_settings()
        settings.save_whisper_settings(
            whisper_model or ws["whisper_model"],
            whisper_device or ws["whisper_device"],
            whisper_compute or ws["whisper_compute"],
        )
    return {"ok": True, **settings.get_transcription_defaults(), **settings.get_whisper_settings()}


@router.post("/api/settings/meeting-capture")
async def set_meeting_capture(enabled: str = Form("false"), token: str = Form("")) -> dict:
    """Toggle the external meeting auto-capture watcher on/off.

    The watcher (scripts/meeting_watcher/watcher.py) polls this flag and only
    records meetings while it is enabled.
    """
    _require_session_token(token)
    value = _to_bool(enabled)
    settings.set_meeting_capture_enabled(value)
    return {"ok": True, "enabled": value}


@router.post("/api/watcher/status")
async def set_watcher_status(
    recording: str = Form("false"),
    app: str = Form(""),
    version: str = Form(""),
    unsupported: str = Form(""),
    token: str = Form(""),
) -> dict:
    """Heartbeat from the meeting watcher (scripts/meeting_watcher/watcher.py).

    The watcher posts ``recording=true`` when a capture starts and again
    periodically while it runs, then ``recording=false`` on stop. Stored only in
    memory — see WATCHER_STATUS_TTL for the staleness rule.

    ``unsupported`` carries the reason a running watcher cannot actually
    capture — an old macOS, a missing audio stack, a permission the OS will not
    prompt for. It heartbeats like the rest so the sidebar can say "running, but
    it will not record" instead of the reassuring plain "running".
    """
    _require_session_token(token)
    is_recording = _to_bool(recording)
    prev = getattr(state, "watcher_status", None) or {}
    was_recording = bool(prev.get("recording")) and (time.time() - prev.get("ts", 0)) < WATCHER_STATUS_TTL
    started_at = prev.get("started_at", 0.0) if (is_recording and was_recording) else (time.time() if is_recording else 0.0)
    state.watcher_status = {
        "recording": is_recording,
        "app": (app or "").strip(),
        "version": (version or "").strip(),
        "unsupported": (unsupported or "").strip(),
        "ts": time.time(),
        "started_at": started_at,
    }
    return {"ok": True}


# Which installer this host's users need, and how they are meant to run it.
# Keyed by the *browser's* platform, because in the Docker case the machine that
# needs the helper is the one holding the browser, not the one running the app.
WATCHER_INSTALLERS = {
    "windows": {
        "file": "setup.bat",
        "hint": "Double-click setup.bat.",
    },
    "macos": {
        "file": "setup.command",
        # A browser download is neither executable nor un-quarantined, so
        # "double-click it" is advice that does not work on macOS.
        "hint": "Open Terminal and run:  bash ~/Downloads/setup.command",
    },
    "linux": {
        "file": "setup.sh",
        "hint": "Open a terminal and run:  bash ~/Downloads/setup.sh",
    },
}

_PLATFORM_KEYS = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}


def _host_platform() -> str:
    return _PLATFORM_KEYS.get(platform.system(), "")


@router.get("/api/watcher/status")
def get_watcher_status() -> dict:
    """Current watcher state for the web UI: whether it's installed/running
    (``alive``), whether it's recording right now (``recording``), and what this
    host can do about it.

    The capability fields exist so the frontend stops guessing from the user
    agent. Whether the *backend* can install a helper is a fact about the
    machine this process runs on, which the browser cannot see — a Mac browser
    pointed at a Linux container would guess wrong in both directions.
    """
    st = getattr(state, "watcher_status", None) or {}
    ts = st.get("ts", 0)
    age = time.time() - ts
    alive = ts > 0 and age < WATCHER_ALIVE_TTL
    fresh = bool(st.get("recording")) and age < WATCHER_STATUS_TTL
    installed_version = st.get("version", "") if alive else ""
    current_version = _bundled_watcher_version()
    host = _host_platform()
    return {
        "alive": alive,
        "recording": fresh,
        "app": st.get("app", "") if fresh else "",
        "since": st.get("started_at", 0) if fresh else 0,
        "installed_version": installed_version,
        "current_version": current_version,
        "update_available": bool(installed_version and current_version and installed_version != current_version),
        "unsupported": st.get("unsupported", "") if alive else "",
        "host_platform": host,
        "host_can_install": bool(host) and not _in_container(),
        "installers": {
            key: {
                "url": f"/scripts/meeting_watcher/{spec['file']}",
                "name": spec["file"],
                "hint": spec["hint"],
            }
            for key, spec in WATCHER_INSTALLERS.items()
        },
    }


def _watcher_install_dir() -> Path | None:
    """Per-user, writable, and conventional for the platform."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "AmicoScript" / "watcher"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "AmicoScript" / "watcher"
    if system == "Linux":
        data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        return Path(data_home) / "amicoscript" / "watcher"
    return None


def _watcher_install_command(dest: Path) -> list[str] | None:
    system = platform.system()
    if system == "Windows":
        return ["powershell", "-ExecutionPolicy", "Bypass", "-NoProfile",
                "-File", str(dest / "install-windows.ps1")]
    if system == "Darwin":
        return ["/bin/bash", str(dest / "install-macos.sh")]
    if system == "Linux":
        return ["/bin/bash", str(dest / "install-linux.sh")]
    return None


# Creating a virtualenv and pip-installing numpy into it from cold takes well
# over the minute a bare PowerShell re-registration needs.
_INSTALL_TIMEOUT = 300


def _install_watcher_sync() -> dict:
    """Copy the bundled watcher into a per-user directory and (re)register it,
    mirroring what the platform's setup script does by hand.

    Only works when the backend runs on the machine that needs the helper: the
    app may itself be elsewhere (e.g. in Docker) while the browser's host is
    the target, so callers must treat a platform mismatch as "offer the manual
    installer download instead", not as an error."""
    dest = _watcher_install_dir()
    command = _watcher_install_command(dest) if dest else None
    if dest is None or command is None:
        return {"ok": False, "error": "unsupported_host", "platform": platform.system()}
    if _in_container():
        # A container can copy the files and run the script, and none of it
        # reaches the audio devices the user actually has.
        return {"ok": False, "error": "unsupported_host", "platform": "container"}
    if not _WATCHER_SRC_DIR.exists():
        return {"ok": False, "error": "bundled watcher files not found"}

    try:
        shutil.copytree(
            _WATCHER_SRC_DIR, dest, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("meetings", "__pycache__", "*.pyc", ".venv"),
        )
    except Exception as exc:
        return {"ok": False, "error": f"copy failed: {exc}"}

    try:
        proc = subprocess.run(
            command, cwd=str(dest), capture_output=True, text=True,
            timeout=_INSTALL_TIMEOUT,
        )
    except Exception as exc:
        return {"ok": False, "error": f"install failed: {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "install script failed").strip()[-500:]}
    return {"ok": True}


@router.post("/api/watcher/install")
async def install_watcher(token: str = Form("")) -> dict:
    """Install/update the external watcher on this host, triggered from the
    UI instead of the user running the setup script by hand. Only works when
    the backend itself runs on the target machine (not e.g. in Docker) —
    see _install_watcher_sync."""
    _require_session_token(token)
    return await asyncio.to_thread(_install_watcher_sync)
