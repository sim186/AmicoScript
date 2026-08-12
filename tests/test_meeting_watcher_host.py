"""Choosing which meeting watcher to run — a property of the host, not a setting.

This used to be a hundred lines inside main.py's startup path, where the only
way to exercise the decision was to boot the whole app on Windows.
"""
import types

import pytest

import meeting_watcher_host as host


@pytest.mark.parametrize("mode", ["0", "off", "false", "no"])
def test_an_explicit_off_never_runs_in_process(mode):
    assert host.wants_embedded(mode) is False


@pytest.mark.parametrize("mode", ["1", "on", "true", "yes"])
def test_an_explicit_on_runs_in_process_whatever_the_host(mode):
    """The PyInstaller build is trusted to know what it is."""
    assert host.wants_embedded(mode) is True


@pytest.mark.parametrize("system", ["Windows", "Darwin", "Linux"])
def test_auto_runs_in_process_on_a_desktop(monkeypatch, system):
    monkeypatch.setattr(host.platform, "system", lambda: system)
    monkeypatch.setattr(host, "_in_container", lambda: False)
    assert host.wants_embedded("auto") is True


def test_auto_never_runs_in_process_inside_a_container(monkeypatch):
    """The Docker image is Linux, and a Linux desktop is not — the container has
    no audio access at all, so the host's own watcher is the only option."""
    monkeypatch.setattr(host.platform, "system", lambda: "Linux")
    monkeypatch.setattr(host, "_in_container", lambda: True)
    assert host.wants_embedded("auto") is False


def test_auto_declines_a_platform_with_no_backend(monkeypatch):
    monkeypatch.setattr(host.platform, "system", lambda: "Haiku")
    monkeypatch.setattr(host, "_in_container", lambda: False)
    assert host.wants_embedded("auto") is False


@pytest.mark.parametrize("system, expected", [
    ("Windows", "schtasks"),
    ("Darwin", "launchctl"),
    ("Linux", "systemctl"),
])
def test_the_external_watcher_is_started_the_platform_s_own_way(monkeypatch, system, expected):
    monkeypatch.setattr(host.platform, "system", lambda: system)
    seen = {}

    def _fake_run(argv, **kwargs):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(host.subprocess, "run", _fake_run)
    host._start_external_task()
    assert seen["argv"][0] == expected


def test_starting_the_external_watcher_is_a_no_op_where_there_is_none(monkeypatch):
    monkeypatch.setattr(host.platform, "system", lambda: "Haiku")
    called = []
    monkeypatch.setattr(host.subprocess, "run", lambda *a, **k: called.append(a))
    host._start_external_task()
    assert called == []


def test_the_mode_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("AMICOSCRIPT_EMBEDDED_WATCHER", "off")
    assert host.wants_embedded() is False
    monkeypatch.setenv("AMICOSCRIPT_EMBEDDED_WATCHER", "on")
    assert host.wants_embedded() is True


def test_auto_on_a_host_without_audio_starts_nothing(monkeypatch, tmp_path):
    """"auto" means "if this host can"; it must not go poking at login tasks."""
    monkeypatch.setenv("AMICOSCRIPT_EMBEDDED_WATCHER", "auto")
    monkeypatch.setattr(host.platform, "system", lambda: "Haiku")
    started = []
    monkeypatch.setattr(host, "_start_external_task", lambda: started.append(True))

    host.start(tmp_path)

    assert started == []


def test_an_explicit_off_falls_back_to_the_installed_task(monkeypatch, tmp_path):
    monkeypatch.setenv("AMICOSCRIPT_EMBEDDED_WATCHER", "off")
    started = []
    monkeypatch.setattr(host, "_start_external_task", lambda: started.append(True))

    host.start(tmp_path)

    assert started == [True]


def test_stopping_when_nothing_is_running_is_a_no_op(monkeypatch):
    monkeypatch.setattr(host, "_module", None)
    host.stop()  # must not raise


def test_stopping_a_wedged_watcher_does_not_hang_shutdown(monkeypatch):
    class _Wedged:
        def stop_embedded(self):
            raise RuntimeError("stuck")

    monkeypatch.setattr(host, "_module", _Wedged())
    host.stop()  # swallowed and logged; shutdown continues


def test_captures_go_somewhere_the_user_can_write():
    """Never Program Files — the packaged app runs from there."""
    assert host.output_dir().name == "meetings"
