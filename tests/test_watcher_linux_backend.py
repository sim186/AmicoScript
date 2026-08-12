"""The Linux meeting-watcher backend: parsing what PulseAudio/PipeWire reports.

The parser is the whole backend's correctness. Two failures matter most: a
paused stream counted as active (every backgrounded media player becomes a
meeting), and the watcher's own ``parec`` counted as an application on the
microphone (the mic heuristic then sees a call that never ends, and the
recording runs until the toggle is switched off).
"""
import json
import sys
from pathlib import Path

import pytest

WATCHER_DIR = Path(__file__).resolve().parents[1] / "scripts" / "meeting_watcher"
if str(WATCHER_DIR) not in sys.path:
    sys.path.insert(0, str(WATCHER_DIR))

from watcher_platform import linux  # noqa: E402


def _stream(binary=None, name=None, pid=None, corked=False, node=None):
    props = {}
    if binary is not None:
        props["application.process.binary"] = binary
    if name is not None:
        props["application.name"] = name
    if pid is not None:
        props["application.process.id"] = str(pid)
    if node is not None:
        props["node.name"] = node
    return {"index": 1, "corked": corked, "properties": props}


def test_active_streams_are_reported_by_binary_name():
    payload = json.dumps([
        _stream(binary="chromium", name="Chromium", pid=1000),
        _stream(binary="zoom", name="Zoom", pid=1001),
    ])
    assert {"chromium", "zoom"} <= linux.parse_streams(payload, set())


def test_a_corked_stream_is_not_playing():
    """A paused Spotify holds a sink-input forever; counting it as audio would
    make every backgrounded media player look like a meeting."""
    payload = json.dumps([
        _stream(binary="spotify", name="Spotify", pid=1000, corked=True),
        _stream(binary="zoom", name="Zoom", pid=1001),
    ])
    names = linux.parse_streams(payload, set())
    assert "zoom" in names
    assert "spotify" not in names


def test_our_own_recorder_is_never_counted():
    """parec on the monitor source appears as an app holding the microphone."""
    payload = json.dumps([
        _stream(binary="parec", name=linux.CLIENT_NAME, pid=4242),
        _stream(binary="zoom", name="Zoom", pid=1001),
    ])
    assert linux.parse_streams(payload, set()) == {"zoom", "zoom"}


def test_our_own_pid_is_never_counted_even_without_the_name_tag():
    payload = json.dumps([_stream(binary="python3", name="python3", pid=99)])
    assert linux.parse_streams(payload, {"99"}) == set()


def test_a_stream_without_a_binary_falls_back_to_the_other_names():
    payload = json.dumps([_stream(name="WEBRTC VoiceEngine", node="webrtc-node")])
    names = linux.parse_streams(payload, set())
    assert "webrtc voiceengine" in names
    assert "webrtc-node" in names


def test_names_are_lowercased_for_the_app_lists():
    """The app lists are matched as lowercase substrings."""
    payload = json.dumps([_stream(binary="Discord", name="Discord", pid=1)])
    assert "discord" in linux.parse_streams(payload, set())


@pytest.mark.parametrize("payload", ["", "not json", "{}", "null", '"a string"'])
def test_unparseable_output_yields_nothing_rather_than_raising(payload):
    assert linux.parse_streams(payload, set()) == set()


def test_junk_entries_are_skipped_not_fatal():
    payload = json.dumps([None, 42, "x", _stream(binary="zoom", pid=1)])
    assert linux.parse_streams(payload, set()) == {"zoom"}


def test_an_entry_without_properties_is_skipped():
    payload = json.dumps([{"index": 3}, {"index": 4, "properties": None}])
    assert linux.parse_streams(payload, set()) == set()


def test_listening_reports_unavailable_rather_than_empty_when_pactl_is_missing(monkeypatch):
    """None switches the mic heuristic off; an empty set would claim, wrongly,
    that nobody is on the microphone."""
    monkeypatch.setattr(linux, "_pactl", lambda: None)
    probe = linux.LinuxProbe()
    assert probe.listening_procs() is None
    assert probe.speaking_procs() == set()


def test_a_failing_pactl_is_reported_as_unavailable(monkeypatch):
    monkeypatch.setattr(linux, "_pactl", lambda: "/usr/bin/pactl")
    monkeypatch.setattr(linux, "run_text", lambda *a, **k: None)
    assert linux.LinuxProbe().listening_procs() is None


def test_capture_is_blocked_without_the_pulseaudio_tools(monkeypatch):
    monkeypatch.setattr(linux, "_pactl", lambda: None)
    reason = linux.LinuxBackend().capture_blocked()
    assert "pulseaudio-utils" in reason
    with pytest.raises(linux.PactlUnavailable):
        linux.LinuxBackend().open_session(mix_mic=True, out_dir=Path("."))


def test_capture_is_blocked_when_only_pactl_is_present(monkeypatch):
    monkeypatch.setattr(linux, "_pactl", lambda: "/usr/bin/pactl")
    monkeypatch.setattr(linux.shutil, "which", lambda name: None)
    assert "parec" in linux.LinuxBackend().capture_blocked()


def test_nothing_is_blocked_on_a_normal_desktop(monkeypatch):
    monkeypatch.setattr(linux, "_pactl", lambda: "/usr/bin/pactl")
    monkeypatch.setattr(linux.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert linux.LinuxBackend().capture_blocked() == ""


def test_parec_is_asked_for_exactly_the_format_the_mixer_reads(monkeypatch, tmp_path):
    monkeypatch.setattr(linux.shutil, "which",
                        lambda name: "/usr/bin/parec" if name == "parec" else None)
    probe = linux.LinuxProbe()
    session = linux._LinuxSession(mix_mic=True, out_dir=tmp_path, probe=probe)
    cmd = session._command("@DEFAULT_MONITOR@")
    assert "--format=s16le" in cmd
    assert f"--rate={linux.CAPTURE_RATE}" in cmd
    assert f"--client-name={linux.CLIENT_NAME}" in cmd
    # The RawSource must describe the same thing parec was told to produce, or
    # the mixer reads the raw file at the wrong rate.
    assert [s.rate for s in session.sources] == [linux.CAPTURE_RATE] * 2
    assert [s.channels for s in session.sources] == [linux.CAPTURE_CHANNELS] * 2


def test_pw_record_is_the_fallback_when_parec_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(linux.shutil, "which",
                        lambda name: "/usr/bin/pw-record" if name == "pw-record" else None)
    session = linux._LinuxSession(mix_mic=False, out_dir=tmp_path, probe=linux.LinuxProbe())
    cmd = session._command("@DEFAULT_MONITOR@")
    assert cmd[0].endswith("pw-record")
    assert "--target" in cmd and "s16" in cmd


def test_the_microphone_can_be_left_out(monkeypatch, tmp_path):
    monkeypatch.setattr(linux.shutil, "which",
                        lambda name: "/usr/bin/parec" if name == "parec" else None)
    session = linux._LinuxSession(mix_mic=False, out_dir=tmp_path, probe=linux.LinuxProbe())
    assert len(session.sources) == 1
    assert session.sources[0].name.startswith("System audio")


def test_an_empty_monitor_capture_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(linux.shutil, "which",
                        lambda name: "/usr/bin/parec" if name == "parec" else None)
    session = linux._LinuxSession(mix_mic=False, out_dir=tmp_path, probe=linux.LinuxProbe())
    assert "no system audio" in session.health()
    session.sources[0].path.write_bytes(b"\x00\x01" * 100)
    assert session.health() == ""
