"""Choosing and loading a meeting-watcher platform backend.

The watcher ships on hosts that cannot run it — a Linux container, a macOS
older than the tap API, a Windows box without the optional audio wheels. None
of those may crash the import or the loop: the helper still has to heartbeat so
the web UI can say what is missing. That contract is what these cover.
"""
import sys
import types
from pathlib import Path

import pytest

WATCHER_DIR = Path(__file__).resolve().parents[1] / "scripts" / "meeting_watcher"
if str(WATCHER_DIR) not in sys.path:
    sys.path.insert(0, str(WATCHER_DIR))

import watcher_platform as wp  # noqa: E402


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv("AMICOSCRIPT_WATCHER_BACKEND", raising=False)


@pytest.mark.parametrize(
    "platform, expected",
    [
        ("win32", "windows"),
        ("darwin", "macos"),
        ("linux", "linux"),
        ("linux2", "linux"),
        ("freebsd14", ""),
    ],
)
def test_platform_key_follows_sys_platform(monkeypatch, platform, expected):
    monkeypatch.setattr(wp.sys, "platform", platform)
    assert wp.platform_key() == expected


def test_an_unsupported_platform_reports_a_reason_instead_of_raising(monkeypatch):
    monkeypatch.setattr(wp.sys, "platform", "freebsd14")
    backend, reason = wp.get_backend()
    assert backend is None
    assert "freebsd14" in reason


def test_a_backend_whose_dependencies_are_missing_reports_a_reason(monkeypatch):
    monkeypatch.setattr(wp.sys, "platform", "win32")
    monkeypatch.setattr(
        wp.importlib, "import_module",
        lambda name: (_ for _ in ()).throw(ImportError("No module named 'pycaw'")),
    )
    backend, reason = wp.get_backend()
    assert backend is None
    assert "windows backend unavailable" in reason
    assert "pycaw" in reason


def test_a_backend_that_fails_to_start_reports_a_reason(monkeypatch):
    module = types.ModuleType("exploding_backend")

    def _boom():
        raise RuntimeError("no audio devices")

    module.create_backend = _boom
    monkeypatch.setitem(sys.modules, "exploding_backend", module)
    monkeypatch.setenv("AMICOSCRIPT_WATCHER_BACKEND", "exploding_backend")

    backend, reason = wp.get_backend()
    assert backend is None
    assert "no audio devices" in reason


def test_the_override_can_name_an_arbitrary_module(monkeypatch):
    module = types.ModuleType("pretend_backend")
    sentinel = object()
    module.create_backend = lambda: sentinel
    monkeypatch.setitem(sys.modules, "pretend_backend", module)
    monkeypatch.setenv("AMICOSCRIPT_WATCHER_BACKEND", "pretend_backend")

    backend, reason = wp.get_backend()
    assert backend is sentinel
    assert reason == ""


def test_a_module_override_does_not_change_what_platform_this_host_is(monkeypatch):
    """App lists and the notifier follow the machine, not the fake backend."""
    monkeypatch.setattr(wp.sys, "platform", "darwin")
    monkeypatch.setenv("AMICOSCRIPT_WATCHER_BACKEND", "pretend_backend")
    assert wp.platform_key() == "macos"
    assert wp.app_defaults() is wp.APP_DEFAULTS["macos"]


def test_every_platform_has_its_own_app_lists():
    for key, lists in wp.APP_DEFAULTS.items():
        assert set(lists) == {"call", "chat", "block"}, key
        for name, value in lists.items():
            entries = [e for e in value.split(",") if e.strip()]
            assert entries, f"{key}/{name} is empty"
            assert all(e == e.lower().strip() for e in entries), f"{key}/{name} not normalized"


def test_a_blocklist_never_swallows_a_meeting_app():
    """A media player on the blocklist that also matches a call app would make
    that app permanently undetectable under the mic heuristic."""
    for key, lists in wp.APP_DEFAULTS.items():
        blocked = {e.strip() for e in lists["block"].split(",") if e.strip()}
        calls = {e.strip() for e in (lists["call"] + "," + lists["chat"]).split(",") if e.strip()}
        for b in blocked:
            for c in calls:
                assert b not in c, f"{key}: blocklist entry {b!r} matches call app {c!r}"


def test_tray_is_windows_only(monkeypatch):
    monkeypatch.setattr(wp.sys, "platform", "darwin")
    assert wp.tray_supported() is False
    monkeypatch.setattr(wp.sys, "platform", "linux")
    assert wp.tray_supported() is False


def test_notify_is_a_no_op_rather_than_an_error_where_unsupported(monkeypatch):
    monkeypatch.setattr(wp.sys, "platform", "freebsd14")
    assert wp.notify("title", "message") is False
    assert wp.notify_supported() is False


def test_notify_swallows_a_failing_notifier(monkeypatch):
    monkeypatch.setattr(wp.sys, "platform", "darwin")
    monkeypatch.setitem(
        wp._NOTIFIERS, "macos",
        lambda *_: (_ for _ in ()).throw(OSError("osascript missing")),
    )
    assert wp.notify("title", "message") is False


def test_applescript_strings_are_escaped():
    assert wp._applescript_str('say "hi"\\n') == '"say \\"hi\\"\\\\n"'
