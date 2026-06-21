import datetime as dt
import importlib.util
import sys
import types
from pathlib import Path


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


def _load_watcher(monkeypatch, tmp_path):
    """Load watcher.py with Windows/audio-only imports stubbed out."""
    root = Path(__file__).resolve().parents[1]
    watcher_path = root / "scripts" / "meeting_watcher" / "watcher.py"
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
