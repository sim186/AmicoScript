"""The macOS meeting-watcher backend, with Core Audio faked out.

Two things here are worth more than the rest. First, own-process exclusion:
the watcher's own aggregate device makes coreaudiod report it as being on the
microphone, so without the skip the mic heuristic sees a call that never ends.
Second, the silence check — macOS answers a missing system-audio permission by
handing back a working, permanently silent tap, so "it recorded nothing" has to
be treated as a failure rather than as a quiet meeting.
"""
import sys
import types
from pathlib import Path

import pytest

WATCHER_DIR = Path(__file__).resolve().parents[1] / "scripts" / "meeting_watcher"
if str(WATCHER_DIR) not in sys.path:
    sys.path.insert(0, str(WATCHER_DIR))

macos = pytest.importorskip(
    "watcher_platform.macos",
    reason="the macOS backend imports CoreAudio through ctypes",
)
from watcher_platform import coreaudio as ca  # noqa: E402


class _FakeCoreAudio:
    """Just enough of watcher_platform.coreaudio to drive the probe.

    Processes are (object_id -> (pid, bundle_id, on_mic, on_speaker)).
    """

    kAudioProcessPropertyPID = ca.kAudioProcessPropertyPID
    kAudioProcessPropertyBundleID = ca.kAudioProcessPropertyBundleID
    kAudioProcessPropertyIsRunningInput = ca.kAudioProcessPropertyIsRunningInput
    kAudioProcessPropertyIsRunningOutput = ca.kAudioProcessPropertyIsRunningOutput

    def __init__(self, processes, paths):
        self.processes = processes
        self.paths = paths

    def process_objects(self):
        return list(self.processes)

    def get_uint32(self, obj, selector, scope=None):
        pid, _bundle, mic, speaker = self.processes[obj]
        if selector == self.kAudioProcessPropertyPID:
            return pid
        if selector == self.kAudioProcessPropertyIsRunningInput:
            return int(mic)
        if selector == self.kAudioProcessPropertyIsRunningOutput:
            return int(speaker)
        return 0

    def get_string(self, obj, selector, scope=None):
        return self.processes[obj][1]

    def proc_path(self, pid):
        return self.paths.get(pid)


@pytest.fixture
def probe(monkeypatch):
    processes = {
        10: (100, "us.zoom.xos", True, True),
        11: (200, "com.spotify.client", False, True),
        12: (300, "com.apple.corespeech", True, False),
        13: (macos._OWN_PID, "org.amico.AmicoScript", True, True),
        14: (400, None, False, True),          # no bundle id, path only
    }
    paths = {
        100: "/Applications/zoom.us.app/Contents/MacOS/zoom.us",
        200: "/Applications/Spotify.app/Contents/MacOS/Spotify",
        300: "/System/Library/PrivateFrameworks/corespeechd",
        macos._OWN_PID: "/usr/bin/python3",
        400: "/Applications/Weird.app/Contents/MacOS/Weird",
    }
    monkeypatch.setattr(macos, "ca", _FakeCoreAudio(processes, paths))
    return macos.MacProbe()


def test_speaking_and_listening_come_from_the_process_list(probe):
    speaking = probe.speaking_procs()
    listening = probe.listening_procs()
    assert "zoom.us" in speaking and "us.zoom.xos" in speaking
    assert "spotify" in speaking
    assert "zoom.us" in listening
    assert "spotify" not in listening


def test_both_the_executable_name_and_the_bundle_id_are_reported(probe):
    """The app lists are substring matches, and users write either form."""
    assert {"zoom.us", "us.zoom.xos"} <= probe.speaking_procs()


def test_a_process_without_a_bundle_id_still_gets_a_name(probe):
    assert "weird" in probe.speaking_procs()


def test_the_watcher_never_counts_itself(probe):
    """Our own aggregate device shows up as mic activity for the whole meeting."""
    for names in (probe.speaking_procs(), probe.listening_procs()):
        assert "python3" not in names
        assert "org.amico.amicoscript" not in names


def test_listening_is_a_set_not_none_when_the_probe_works(probe):
    """None means "this host cannot tell", which switches the mic heuristic
    off — macOS can always tell, so it must never return it."""
    assert isinstance(probe.listening_procs(), set)


@pytest.mark.parametrize("version, supported", [
    ("13.6", False), ("14.1.2", False), ("14.2", True), ("15.0", True), ("26.5.2", True),
])
def test_capture_needs_macos_14_2(monkeypatch, version, supported):
    monkeypatch.setattr(macos.platform, "mac_ver", lambda: (version, ("", "", ""), ""))
    monkeypatch.setattr(macos.ca, "has_tap_api", lambda: True)
    assert macos.capture_supported() is supported


def test_an_old_macos_explains_itself_and_keeps_detecting(monkeypatch):
    monkeypatch.setattr(macos.platform, "mac_ver", lambda: ("13.6", ("", "", ""), ""))
    monkeypatch.setattr(macos.ca, "has_tap_api", lambda: True)
    backend = macos.MacBackend()
    reason = backend.capture_blocked()
    assert "14.2" in reason and "13.6" in reason
    with pytest.raises(RuntimeError):
        backend.open_session(mix_mic=True, out_dir=Path("."))


def test_a_current_macos_reports_no_blocker(monkeypatch):
    monkeypatch.setattr(macos.platform, "mac_ver", lambda: ("15.1", ("", "", ""), ""))
    monkeypatch.setattr(macos.ca, "has_tap_api", lambda: True)
    assert macos.MacBackend().capture_blocked() == ""


def _session_with(streams):
    session = macos._MacSession.__new__(macos._MacSession)
    session._streams = streams
    return session


def test_a_silent_system_tap_is_reported_as_a_failure():
    """macOS grants a tap and then feeds it zeros when permission is missing;
    it never returns an error, so silence is the only symptom."""
    system = types.SimpleNamespace(kind="system", peak=0.0)
    mic = types.SimpleNamespace(kind="mic", peak=0.4)
    problem = _session_with([system, mic]).health()
    assert "permission" in problem.lower()
    assert "System Settings" in problem


def test_a_tap_that_captured_audio_is_healthy():
    system = types.SimpleNamespace(kind="system", peak=0.02)
    mic = types.SimpleNamespace(kind="mic", peak=0.0)
    # A muted microphone is a normal way to attend a meeting; only the system
    # side being empty means the recording is worthless.
    assert _session_with([system, mic]).health() == ""


def test_float32_frames_become_interleaved_int16():
    np = pytest.importorskip("numpy")
    stream = macos._Stream.__new__(macos._Stream)
    stream._non_interleaved = False
    stream.peak = 0.0
    left = np.array([0.0, 0.5, -0.5, 1.0], dtype="<f4").tobytes()
    out = np.frombuffer(stream._to_int16([(2, left)]), dtype="<i2")
    assert list(out) == [0, 16383, -16383, 32767]
    assert stream.peak == pytest.approx(1.0)


def test_out_of_range_samples_are_clipped_not_wrapped():
    """Without the clip, a sample above 1.0 wraps to full-scale negative — an
    audible click on every peak rather than mild distortion."""
    np = pytest.importorskip("numpy")
    stream = macos._Stream.__new__(macos._Stream)
    stream._non_interleaved = False
    stream.peak = 0.0
    hot = np.array([2.5, -2.5], dtype="<f4").tobytes()
    out = np.frombuffer(stream._to_int16([(1, hot)]), dtype="<i2")
    assert list(out) == [32767, -32767]


def test_per_channel_buffers_are_interleaved():
    """Core Audio may hand over one buffer per channel; the mixer only ever
    reads interleaved frames."""
    np = pytest.importorskip("numpy")
    stream = macos._Stream.__new__(macos._Stream)
    stream._non_interleaved = True
    stream.peak = 0.0
    left = np.array([0.1, 0.3], dtype="<f4").tobytes()
    right = np.array([0.2, 0.4], dtype="<f4").tobytes()
    out = np.frombuffer(stream._to_int16([(1, left), (1, right)]), dtype="<i2")
    assert list(out) == pytest.approx([3276, 6553, 9830, 13106], abs=2)
