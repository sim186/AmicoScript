"""Tests for the meeting-watcher status endpoints.

Covers the heartbeat/staleness contract in backend/api/routes/settings.py:
- ``_to_bool`` accepts the same truthy set the watcher posts.
- ``POST /api/watcher/status`` stores recording/app/ts in ``state``.
- protected POSTs reject missing/invalid session tokens.
- ``GET /api/watcher/status`` applies the TTL rules so a crashed watcher
  (stale heartbeat) never leaves the UI stuck on "recording" or "alive".

The route handlers do ``import state`` inside the function body, which works
via tests/conftest.py adding the backend dir to sys.path.
"""
import asyncio
import time

import pytest
from fastapi import HTTPException

from api.routes import settings as route

TEST_TOKEN = "test-session-token"


@pytest.fixture(autouse=True)
def _set_session_token():
    import state
    previous = state.exit_token
    state.exit_token = TEST_TOKEN
    yield
    state.exit_token = previous


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


def _post_status(recording: str, app: str = "", version: str = "", token: str = TEST_TOKEN) -> dict:
    return asyncio.run(route.set_watcher_status(recording=recording, app=app, version=version, token=token))


def _get_status() -> dict:
    return route.get_watcher_status()


def _set_state(ts: float, recording: bool = True, app: str = "Zoom", started_at: float = None,
                version: str = "") -> None:
    import state
    if started_at is None:
        started_at = ts if recording else 0.0
    state.watcher_status = {
        "recording": recording, "app": app, "ts": ts, "started_at": started_at, "version": version,
    }


def test_post_status_stores_recording_app_and_ts(monkeypatch):
    import state
    fixed = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: fixed)
    result = _post_status("true", "Zoom", version="3")
    assert result == {"ok": True}
    assert state.watcher_status == {
        "recording": True, "app": "Zoom", "version": "3", "ts": fixed, "started_at": fixed,
    }


def test_post_status_keeps_started_at_across_heartbeats(monkeypatch):
    import state
    state.watcher_status = {"recording": False, "app": "", "ts": 0.0, "started_at": 0.0}
    monkeypatch.setattr(time, "time", lambda: 100.0)
    _post_status("true", "Zoom")
    monkeypatch.setattr(time, "time", lambda: 105.0)
    _post_status("true", "Zoom")
    assert state.watcher_status["started_at"] == 100.0
    assert state.watcher_status["ts"] == 105.0


def test_post_status_resets_started_at_after_stop_and_restart(monkeypatch):
    import state
    monkeypatch.setattr(time, "time", lambda: 100.0)
    _post_status("true", "Zoom")
    monkeypatch.setattr(time, "time", lambda: 105.0)
    _post_status("false", "Zoom")
    monkeypatch.setattr(time, "time", lambda: 110.0)
    _post_status("true", "Zoom")
    assert state.watcher_status["started_at"] == 110.0


def test_post_status_strips_app_and_parses_false(monkeypatch):
    import state
    monkeypatch.setattr(time, "time", lambda: 100.0)
    _post_status("false", "  Meet  ")
    assert state.watcher_status["recording"] is False
    assert state.watcher_status["app"] == "Meet"


def test_post_status_rejects_missing_token():
    with pytest.raises(HTTPException) as exc:
        _post_status("true", "Zoom", token="")
    assert exc.value.status_code == 403


def test_get_status_alive_and_recording_when_fresh(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 100.0)
    _set_state(ts=95.0, recording=True, app="Zoom")  # age = 5s
    d = _get_status()
    assert d["alive"] is True
    assert d["recording"] is True
    assert d["app"] == "Zoom"
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
    # age = 10s: < ALIVE(15) but > RECORDING_TTL(8)
    _set_state(ts=90.0, recording=True, app="Zoom")
    d = _get_status()
    assert d["alive"] is True
    assert d["recording"] is False
    assert d["app"] == ""
    assert d["since"] == 0


def test_get_status_not_alive_when_heartbeat_too_old(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 100.0)
    _set_state(ts=10.0, recording=True, app="Zoom")  # age = 90s > ALIVE
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

    out = asyncio.run(route.set_meeting_capture(enabled="true", token=TEST_TOKEN))
    assert out == {"ok": True, "enabled": True}
    assert route_settings._get_meeting_capture_enabled() is True

    out = asyncio.run(route.set_meeting_capture(enabled="0", token=TEST_TOKEN))
    assert out == {"ok": True, "enabled": False}
    assert route_settings._get_meeting_capture_enabled() is False


def test_set_meeting_capture_rejects_bad_token(monkeypatch, tmp_path):
    import settings as route_settings
    sf = tmp_path / "settings.json"
    monkeypatch.setattr(route_settings, "_settings_file", lambda: sf)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.set_meeting_capture(enabled="true", token="wrong"))
    assert exc.value.status_code == 403
    assert route_settings._get_meeting_capture_enabled() is False


def test_get_settings_includes_meeting_capture_flag(monkeypatch, tmp_path):
    import settings as route_settings
    sf = tmp_path / "settings.json"
    monkeypatch.setattr(route_settings, "_settings_file", lambda: sf)
    route_settings._set_meeting_capture_enabled(True)

    data = route.get_settings()
    assert data["meeting_capture_enabled"] is True


def test_bundled_watcher_version_reads_real_constant():
    # Sanity check against the actual shipped file (no mocking) so a typo'd
    # WATCHER_VERSION constant or a moved file breaks this test loudly.
    version = route._bundled_watcher_version()
    assert version != ""


def test_get_status_flags_update_available_when_versions_differ(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 100.0)
    monkeypatch.setattr(route, "_bundled_watcher_version", lambda: "3")
    _set_state(ts=95.0, recording=False, app="", version="2")
    d = _get_status()
    assert d["installed_version"] == "2"
    assert d["current_version"] == "3"
    assert d["update_available"] is True


def test_get_status_no_update_when_versions_match(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 100.0)
    monkeypatch.setattr(route, "_bundled_watcher_version", lambda: "3")
    _set_state(ts=95.0, recording=False, app="", version="3")
    d = _get_status()
    assert d["update_available"] is False


def test_get_status_no_update_when_installed_version_unknown(monkeypatch):
    # An old watcher that predates the version field reports "" -- never
    # claim an update is available without a real installed version to compare.
    monkeypatch.setattr(time, "time", lambda: 100.0)
    monkeypatch.setattr(route, "_bundled_watcher_version", lambda: "3")
    _set_state(ts=95.0, recording=False, app="", version="")
    d = _get_status()
    assert d["update_available"] is False


def test_get_status_clears_installed_version_when_not_alive(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 100.0)
    monkeypatch.setattr(route, "_bundled_watcher_version", lambda: "3")
    _set_state(ts=10.0, recording=False, app="", version="2")  # stale heartbeat
    d = _get_status()
    assert d["installed_version"] == ""
    assert d["update_available"] is False


def test_install_watcher_rejects_missing_token():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.install_watcher(token=""))
    assert exc.value.status_code == 403


def test_install_watcher_reports_not_windows_off_platform(monkeypatch):
    monkeypatch.setattr(route.platform, "system", lambda: "Linux")
    out = asyncio.run(route.install_watcher(token=TEST_TOKEN))
    assert out == {"ok": False, "error": "not_windows"}
