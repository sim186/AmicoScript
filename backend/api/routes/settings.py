"""Settings endpoints."""

import time

from fastapi import APIRouter, Form

from settings import (
    _get_meeting_capture_enabled,
    _load_settings,
    _save_settings,
    _set_meeting_capture_enabled,
)

router = APIRouter()

# A recording heartbeat older than this (seconds) is treated as idle, so a
# crashed/killed watcher never leaves the UI stuck showing "recording".
WATCHER_STATUS_TTL = 20.0
# No heartbeat at all within this window means the watcher isn't running, so the
# UI shows its one-time setup prompt. Must exceed the watcher's heartbeat period.
WATCHER_ALIVE_TTL = 45.0


def _to_bool(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@router.get("/api/settings")
def get_settings() -> dict:
    import state
    settings = _load_settings()
    return {
        "hf_token": settings.get("hf_token", ""),
        "exit_token": getattr(state, "exit_token", ""),
        "meeting_capture_enabled": _get_meeting_capture_enabled(),
    }


@router.post("/api/settings")
async def save_settings(hf_token: str = Form("")) -> dict:
    settings = _load_settings()
    settings["hf_token"] = hf_token
    _save_settings(settings)
    return {"ok": True}


@router.post("/api/settings/meeting-capture")
async def set_meeting_capture(enabled: str = Form("false")) -> dict:
    """Toggle the external Teams auto-capture watcher on/off.

    The watcher (scripts/teams_watcher/watcher.py) polls this flag and only
    records meetings while it is enabled.
    """
    value = _to_bool(enabled)
    _set_meeting_capture_enabled(value)
    return {"ok": True, "enabled": value}


@router.post("/api/watcher/status")
async def set_watcher_status(recording: str = Form("false"), app: str = Form("")) -> dict:
    """Heartbeat from the meeting watcher (scripts/teams_watcher/watcher.py).

    The watcher posts ``recording=true`` when a capture starts and again
    periodically while it runs, then ``recording=false`` on stop. Stored only in
    memory — see WATCHER_STATUS_TTL for the staleness rule.
    """
    import state
    state.watcher_status = {
        "recording": _to_bool(recording),
        "app": (app or "").strip(),
        "ts": time.time(),
    }
    return {"ok": True}


@router.get("/api/watcher/status")
def get_watcher_status() -> dict:
    """Current watcher state for the web UI: whether it's installed/running
    (``alive``) and whether it's recording right now (``recording``)."""
    import state
    st = getattr(state, "watcher_status", None) or {}
    ts = st.get("ts", 0)
    age = time.time() - ts
    alive = ts > 0 and age < WATCHER_ALIVE_TTL
    fresh = bool(st.get("recording")) and age < WATCHER_STATUS_TTL
    return {
        "alive": alive,
        "recording": fresh,
        "app": st.get("app", "") if fresh else "",
        "since": ts if fresh else 0,
    }
