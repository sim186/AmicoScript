import datetime as dt
import importlib.util
import math
import sys
import types
from pathlib import Path

import pytest


class _Response:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class _ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, **_):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


class _FakeSession:
    """A capture session that records nothing — the loop tests never mix audio."""

    def __init__(self, sources):
        self.sources = sources
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _fake_backend_module(monkeypatch, name="fake_watcher_backend"):
    """Register an importable backend so watcher.py needs no real audio stack.

    The platform backends are the only part of the watcher that cannot run on
    a CI box; everything the tests below care about — detection decisions, the
    debounce loop, uploads — lives above that seam.
    """
    module = types.ModuleType(name)
    module.speaking = set()
    module.listening = set()
    module.sessions = []

    class _Backend:
        name = "fake"

        def speaking_procs(self):
            return set(module.speaking)

        def listening_procs(self):
            return None if module.listening is None else set(module.listening)

        def open_session(self, mix_mic, out_dir):
            session = _FakeSession([])
            module.sessions.append(session)
            return session

    module.create_backend = _Backend
    monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("AMICOSCRIPT_WATCHER_BACKEND", name)
    return module


def _load_watcher(monkeypatch, tmp_path):
    """Load watcher.py against a fake platform backend and stubbed numpy/requests."""
    root = Path(__file__).resolve().parents[1]
    watcher_path = root / "scripts" / "meeting_watcher" / "watcher.py"
    monkeypatch.setenv("AMICOSCRIPT_WATCHER_OUT", str(tmp_path))

    _fake_backend_module(monkeypatch)
    monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))
    monkeypatch.setitem(
        sys.modules,
        "requests",
        types.SimpleNamespace(
            get=lambda *_, **__: _Response(),
            post=lambda *_, **__: _Response(data={"job_id": "job", "recording_id": "rec"}),
        ),
    )

    module_name = f"meeting_watcher_under_test_{id(tmp_path)}"
    spec = importlib.util.spec_from_file_location(module_name, watcher_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module.OUTPUT_DIR = tmp_path
    module._TRAY_OK = False
    module._embedded_quit.clear()
    module._tray_quit.clear()
    return module


def _install_fast_loop(monkeypatch, watcher):
    monkeypatch.setattr(watcher, "START_DEBOUNCE", 2)
    monkeypatch.setattr(watcher, "STOP_DEBOUNCE", 2)
    monkeypatch.setattr(watcher, "POLL_SECONDS", 0)
    monkeypatch.setattr(watcher.time, "sleep", lambda _: None)
    monkeypatch.setattr(watcher, "notify", lambda *_, **__: None)
    monkeypatch.setattr(watcher, "log", lambda *_, **__: None)
    monkeypatch.setattr(watcher, "report_status", lambda *_, **__: None)
    monkeypatch.setattr(watcher._tray_quit, "is_set", lambda: False)
    monkeypatch.setattr(watcher.threading, "Thread", _ImmediateThread)


def test_main_loop_detects_call_stops_and_submits_without_report(monkeypatch, tmp_path):
    watcher = _load_watcher(monkeypatch, tmp_path)
    _install_fast_loop(monkeypatch, watcher)

    states = iter([
        (True, "Zoom"),
        (True, "Zoom"),   # start after debounce
        (False, ""),
        (False, ""),      # stop after debounce
    ])
    submitted = []

    class FakeCapture:
        instances = []

        def __init__(self, mix_mic=True):
            self.started = False
            self.stopped = False
            self.stop_path = None
            FakeCapture.instances.append(self)

        def start(self):
            self.started = True

        def stop(self, out_path):
            self.stopped = True
            self.stop_path = Path(out_path)
            self.stop_path.write_bytes(b"fake wav")
            return 20.0

    def fake_call_in_progress():
        try:
            return next(states)
        except StopIteration:
            watcher._embedded_quit.set()
            return False, ""

    monkeypatch.setattr(watcher, "Capture", FakeCapture)
    monkeypatch.setattr(watcher, "capture_enabled", lambda: True)
    monkeypatch.setattr(watcher, "call_in_progress", fake_call_in_progress)
    monkeypatch.setattr(watcher, "submit_recording", lambda path: submitted.append(Path(path)))

    watcher._main_loop()

    assert len(FakeCapture.instances) == 1
    capture = FakeCapture.instances[0]
    assert capture.started is True
    assert capture.stopped is True
    assert submitted == [capture.stop_path]
    assert not capture.stop_path.with_suffix(".report.md").exists()


def test_main_loop_stops_active_capture_when_toggle_turns_off(monkeypatch, tmp_path):
    watcher = _load_watcher(monkeypatch, tmp_path)
    _install_fast_loop(monkeypatch, watcher)
    monkeypatch.setattr(watcher, "STOP_DEBOUNCE", 99)

    call_states = iter([(True, "Meet"), (True, "Meet"), (True, "Meet")])
    enabled_states = iter([True, True, False])
    submitted = []

    class FakeCapture:
        instance = None

        def __init__(self, mix_mic=True):
            self.started = False
            self.stopped = False
            FakeCapture.instance = self

        def start(self):
            self.started = True

        def stop(self, out_path):
            self.stopped = True
            Path(out_path).write_bytes(b"fake wav")
            return 20.0

    def fake_call_in_progress():
        try:
            return next(call_states)
        except StopIteration:
            watcher._embedded_quit.set()
            return False, ""

    def fake_capture_enabled():
        try:
            return next(enabled_states)
        except StopIteration:
            watcher._embedded_quit.set()
            return False

    monkeypatch.setattr(watcher, "Capture", FakeCapture)
    monkeypatch.setattr(watcher, "capture_enabled", fake_capture_enabled)
    monkeypatch.setattr(watcher, "call_in_progress", fake_call_in_progress)
    monkeypatch.setattr(watcher, "submit_recording", lambda path: submitted.append(Path(path)))

    watcher._main_loop()

    assert FakeCapture.instance.started is True
    assert FakeCapture.instance.stopped is True
    assert len(submitted) == 1


def test_finalize_capture_deletes_short_recording_and_does_not_submit(monkeypatch, tmp_path):
    watcher = _load_watcher(monkeypatch, tmp_path)
    monkeypatch.setattr(watcher, "notify", lambda *_, **__: None)
    monkeypatch.setattr(watcher, "log", lambda *_, **__: None)
    submitted = []
    monkeypatch.setattr(watcher, "submit_recording", lambda path: submitted.append(Path(path)))

    class ShortCapture:
        def stop(self, out_path):
            Path(out_path).write_bytes(b"too short")
            return watcher.MIN_MEETING_SECONDS - 1

    started = dt.datetime(2026, 1, 2, 3, 4, 5)
    watcher._finalize_capture(ShortCapture(), started, "Zoom Call!")

    expected = tmp_path / "zoomcall_20260102_030405.wav"
    assert submitted == []
    assert not expected.exists()
    assert list(tmp_path.glob("*.report.md")) == []


def test_main_exits_when_another_watcher_owns_lock(monkeypatch, tmp_path):
    watcher = _load_watcher(monkeypatch, tmp_path)
    ran = False

    def fake_main_loop():
        nonlocal ran
        ran = True

    monkeypatch.setattr(watcher, "_acquire_instance_lock", lambda: False)
    monkeypatch.setattr(watcher, "_main_loop", fake_main_loop)
    monkeypatch.setattr(watcher, "log", lambda *_, **__: None)

    watcher.main()

    assert ran is False


def test_report_status_refreshes_token_and_retries_after_403(monkeypatch, tmp_path):
    watcher = _load_watcher(monkeypatch, tmp_path)
    watcher._server_token_cache["value"] = "old-token"
    posts = []

    def fake_get(url, timeout):
        assert url.endswith("/api/settings")
        return _Response(data={"exit_token": "new-token", "meeting_capture_enabled": True})

    def fake_post(url, data, timeout):
        posts.append((url, dict(data)))
        return _Response(status_code=403 if len(posts) == 1 else 200)

    monkeypatch.setattr(watcher.requests, "get", fake_get)
    monkeypatch.setattr(watcher.requests, "post", fake_post)

    watcher.report_status(True, "Zoom")

    assert len(posts) == 2
    assert posts[0][1]["token"] == "old-token"
    assert posts[1][1]["token"] == "new-token"
    assert all(url.endswith("/api/watcher/status") for url, _ in posts)


def test_transcription_options_follow_app_settings_by_default(monkeypatch, tmp_path):
    """No env overrides -> upload uses whatever the AmicoScript UI is set to."""
    watcher = _load_watcher(monkeypatch, tmp_path)

    assert watcher.WHISPER_MODEL is None
    assert watcher.LANGUAGE is None
    assert watcher.DIARIZE is None
    # Diarization must be off until the app says otherwise: it needs an HF token
    # and used to run on every auto-captured meeting regardless of the UI toggle.
    assert watcher.transcription_options() == {
        "model": "small", "language": "", "diarize": False,
    }

    watcher._remember_server_settings({
        "default_model": "medium",
        "default_language": "it",
        "default_diarize": True,
    })
    assert watcher.transcription_options() == {
        "model": "medium", "language": "it", "diarize": True,
    }


def test_env_vars_pin_transcription_options(monkeypatch, tmp_path):
    monkeypatch.setenv("AMICOSCRIPT_MODEL", "large-v3")
    monkeypatch.setenv("AMICOSCRIPT_LANGUAGE", "de")
    monkeypatch.setenv("AMICOSCRIPT_DIARIZE", "false")
    watcher = _load_watcher(monkeypatch, tmp_path)

    watcher._remember_server_settings({
        "default_model": "small",
        "default_language": "it",
        "default_diarize": True,
    })
    assert watcher.transcription_options() == {
        "model": "large-v3", "language": "de", "diarize": False,
    }


def test_transcribe_posts_resolved_options(monkeypatch, tmp_path):
    watcher = _load_watcher(monkeypatch, tmp_path)
    monkeypatch.setattr(watcher, "log", lambda *_, **__: None)
    watcher._remember_server_settings({
        "default_model": "medium",
        "default_language": "it",
        "default_diarize": False,
    })
    posted = {}

    def fake_post(url, files, data, timeout):
        posted.update(url=url, data=dict(data))
        return _Response(data={"job_id": "job", "recording_id": "rec"})

    monkeypatch.setattr(watcher.requests, "post", fake_post)
    wav = tmp_path / "meeting.wav"
    wav.write_bytes(b"fake wav")

    assert watcher.transcribe(wav) == ("job", "rec")
    assert posted["url"].endswith("/api/transcribe")
    assert posted["data"] == {
        "model": "medium",
        "language": "it",
        "diarize": "false",
        # Tags the recording as a captured call so the backend can auto-summarize it.
        "source": "meeting",
    }


def test_cleanup_orphan_raw_removes_only_scratch_files(monkeypatch, tmp_path):
    watcher = _load_watcher(monkeypatch, tmp_path)
    monkeypatch.setattr(watcher, "log", lambda *_, **__: None)
    orphan = tmp_path / "capture-abc123.raw"
    orphan.write_bytes(b"raw pcm")
    keep = tmp_path / "zoom_20260101_100000.wav"
    keep.write_bytes(b"wav")

    watcher._cleanup_orphan_raw()

    assert not orphan.exists()
    assert keep.exists()


def _load_watcher_with_numpy(monkeypatch, tmp_path):
    """Load watcher.py with real numpy (only the Windows audio deps stubbed).

    The other tests stub numpy out entirely; the resampler needs the real thing.
    """
    np = pytest.importorskip("numpy")
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("AMICOSCRIPT_WATCHER_OUT", str(tmp_path))

    pycaw_pkg = types.ModuleType("pycaw")
    pycaw_pkg.__path__ = []
    pycaw_mod = types.ModuleType("pycaw.pycaw")
    pycaw_mod.AudioUtilities = object()
    monkeypatch.setitem(sys.modules, "pycaw", pycaw_pkg)
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", pycaw_mod)
    monkeypatch.setitem(
        sys.modules,
        "pyaudiowpatch",
        types.SimpleNamespace(paWASAPI=0, paInt16=8, PyAudio=lambda: object()),
    )

    module_name = f"meeting_watcher_numpy_{id(tmp_path)}"
    spec = importlib.util.spec_from_file_location(
        module_name, root / "scripts" / "meeting_watcher" / "watcher.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module.OUTPUT_DIR = tmp_path
    return module, np


def _resample_all(watcher, np, raw_path, src_rate, frames, out_rate):
    stat = {"rate": src_rate, "frames": frames, "channels": 1, "bytes_per_frame": 2}
    total = int(math.ceil(frames / float(src_rate) * out_rate))
    chunk = out_rate * 10
    parts = []
    with open(raw_path, "rb") as fh:
        for start in range(0, total, chunk):
            parts.append(
                watcher.Capture._read_resampled_window(
                    fh, stat, start, min(chunk, total - start), out_rate
                )
            )
    return np.concatenate(parts), total


def test_downsampling_suppresses_aliasing(monkeypatch, tmp_path):
    """A 15 kHz tone must not fold back to 1 kHz when decimating 48k -> 16k.

    Without the anti-alias low-pass it lands right in the middle of the band
    Whisper transcribes from.
    """
    watcher, np = _load_watcher_with_numpy(monkeypatch, tmp_path)
    src_rate, seconds = 48000, 25.0
    t = np.arange(int(src_rate * seconds)) / src_rate
    signal = 0.4 * np.sin(2 * np.pi * 440 * t) + 0.4 * np.sin(2 * np.pi * 15000 * t)
    raw = tmp_path / "src.raw"
    raw.write_bytes((signal * 32767).astype(np.int16).tobytes())

    out, total = _resample_all(watcher, np, raw, src_rate, t.size, watcher.OUT_RATE)

    assert watcher.OUT_RATE == 16000
    assert out.size == total

    spectrum = np.abs(np.fft.rfft(out * np.hanning(out.size)))
    freqs = np.fft.rfftfreq(out.size, 1.0 / watcher.OUT_RATE)

    def peak(hz):
        i = int(np.argmin(np.abs(freqs - hz)))
        return spectrum[max(0, i - 3):i + 4].max()

    # The 440 Hz tone survives; its 15 kHz alias image at 1 kHz is buried.
    assert peak(1000) < peak(440) / 1000.0


def test_resampled_chunks_join_without_clicks(monkeypatch, tmp_path):
    """Per-chunk filtering must not leave a step at the 10 s window boundary."""
    watcher, np = _load_watcher_with_numpy(monkeypatch, tmp_path)
    src_rate, seconds = 48000, 25.0
    t = np.arange(int(src_rate * seconds)) / src_rate
    signal = 0.4 * np.sin(2 * np.pi * 440 * t)
    raw = tmp_path / "src.raw"
    raw.write_bytes((signal * 32767).astype(np.int16).tobytes())

    out, _ = _resample_all(watcher, np, raw, src_rate, t.size, watcher.OUT_RATE)

    boundary = watcher.OUT_RATE * 10
    step = abs(float(out[boundary] - out[boundary - 1]))
    # Largest legitimate sample-to-sample change for this tone at 16 kHz.
    max_slope = 2 * np.pi * 440 / watcher.OUT_RATE * float(np.abs(out).max())
    assert step <= max_slope * 1.1
