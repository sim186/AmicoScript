"""Tests for the meeting-watcher status endpoints.

Covers the heartbeat/staleness contract in backend/api/routes/settings.py:
- ``_to_bool`` accepts the same truthy set the watcher posts.
- ``POST /api/watcher/status`` stores recording/app/ts in ``state``.
- ``GET /api/watcher/status`` applies the TTL rules so a crashed watcher
  (stale heartbeat) never leaves the UI stuck on "recording" or "alive".

The route handlers do ``import state`` inside the function body, which works
via tests/conftest.py adding the backend dir to sys.path.
"""
import asyncio
import time

import pytest

from api.routes import settings as route


def _to_bool(value: str) -> bool:
    return route._to_bool(value)


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("1", True), ("yes", True), ("on", True),
    ("TRUE", True), (" On ", True),
    ("false", False), ("0", False), ("", False), ("nope", False), ("  ", False),
])
def test_to_bool_truthy_and_falsy(raw, expected):
    assert _to_bool(raw) is expected


def test_watcher_status_constants_sane():
    # ALIVE must exceed the watcher's heartbeat period (10s) so a running
    # watcher is reliably seen as alive between heartbeats.
    assert route.WATCHER_ALIVE_TTL > 10.0
    # RECORDING TTL is shorter so a crashed-mid-call watcher clears the chip
    # promptly even while still within the ALIVE window.
    assert route.WATCHER_STATUS_TTL < route.WATCHER_ALIVE_TTL


def _post_status(recording: str, app: str = "") -> dict:
    return asyncio.run(route.set_watcher_status(recording=recording, app=app))


def _get_status() -> dict:
    return route.get_watcher_status()


def _set_state(ts: float, recording: bool = True, app: str = "Teams") -> None:
    import state
    state.watcher_status = {"recording": recording, "app": app, "ts": ts}


def test_post_status_stores_recording_app_and_ts(monkeypatch):
    import state
    fixed = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: fixed)
    result = _post_status("true", "Zoom")
    assert result == {"ok": True}
    assert state.watcher_status == {"recording": True, "app": "Zoom", "ts": fixed}


def test_post_status_strips_app_and_parses_false(monkeypatch):
    import state
    monkeypatch.setattr(time, "time", lambda: 100.0)
    _post_status("false", "  Teams  ")
    assert state.watcher_status["recording"] is False
    assert state.watcher_status["app"] == "Teams"


def test_get_status_alive_and_recording_when_fresh(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 100.0)
    _set_state(ts=95.0, recording=True, app="Teams")  # age = 5s
    d = _get_status()
    assert d["alive"] is True
    assert d["recording"] is True
    assert d["app"] == "Teams"
    assert d["since"] == 95.0


def test_get_status_alive_but_not_recording_when_recording_flag_false(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 100.0)
    _set_state(ts=95.0, recording=False, app="")  # idle heartbeat
    d = _get_status()
    assert d["alive"] is True
    assert d["recording"] is False
    assert d["app"] == ""
    assert d["since"] == 0


def test_get_status_recording_flag_stale_within_alive_window(monkeypatch):
    # Heartbeat is recent enough to be "alive" but the recording heartbeat
    # is older than WATCHER_STATUS_TTL -> a crashed-mid-call watcher must NOT
    # show as recording, while still being seen as installed/running.
    monkeypatch.setattr(time, "time", lambda: 100.0)
    # age = 30s: < ALIVE(45) but > RECORDING_TTL(20)
    _set_state(ts=70.0, recording=True, app="Teams")
    d = _get_status()
    assert d["alive"] is True
    assert d["recording"] is False
    assert d["app"] == ""
    assert d["since"] == 0


def test_get_status_not_alive_when_heartbeat_too_old(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 100.0)
    _set_state(ts=10.0, recording=True, app="Teams")  # age = 90s > ALIVE
    d = _get_status()
    assert d["alive"] is False
    assert d["recording"] is False
    assert d["app"] == ""
    assert d["since"] == 0


def test_get_status_never_heartbeat_is_not_alive():
    import state
    # Default module state: ts=0.0 -> never heartbeated.
    state.watcher_status = {"recording": False, "app": "", "ts": 0.0}
    d = _get_status()
    assert d["alive"] is False
    assert d["recording"] is False


def test_set_meeting_capture_endpoint_persists_and_returns(monkeypatch, tmp_path):
    # The route does ``from settings import ...`` which binds the top-level
    # ``settings`` module (exposed via conftest's BACKEND_DIR on sys.path) —
    # a distinct module object from ``backend.settings``. Patch the one the
    # route actually calls so the persist round-trips to the same file.
    import settings as route_settings
    sf = tmp_path / "settings.json"
    monkeypatch.setattr(route_settings, "_settings_file", lambda: sf)

    out = asyncio.run(route.set_meeting_capture(enabled="true"))
    assert out == {"ok": True, "enabled": True}
    assert route_settings._get_meeting_capture_enabled() is True

    out = asyncio.run(route.set_meeting_capture(enabled="0"))
    assert out == {"ok": True, "enabled": False}
    assert route_settings._get_meeting_capture_enabled() is False


def test_get_settings_includes_meeting_capture_flag(monkeypatch, tmp_path):
    import settings as route_settings
    sf = tmp_path / "settings.json"
    monkeypatch.setattr(route_settings, "_settings_file", lambda: sf)
    route_settings._set_meeting_capture_enabled(True)

    data = route.get_settings()
    assert data["meeting_capture_enabled"] is True
