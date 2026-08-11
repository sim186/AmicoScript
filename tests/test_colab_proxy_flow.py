import json
import threading
from pathlib import Path

import state
from core import colab_proxy


class _FakeResponse:
    def __init__(self, *, json_data=None, lines=None):
        self._json_data = json_data or {}
        self._lines = lines or []

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_data

    def iter_lines(self):
        for line in self._lines:
            yield line

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _build_job(tmp_path: Path) -> str:
    fp = tmp_path / "sample.wav"
    fp.write_bytes(b"abc")
    job_id = "job-colab"
    state.jobs[job_id] = {
        "id": job_id,
        "file_path": str(fp),
        "original_filename": "sample.wav",
        "options": {
            "colab_url": "http://colab.local",
            "model": "small",
            "language": "",
            "diarize": False,
            "hf_token": "",
            "num_speakers": None,
            "min_speakers": None,
            "max_speakers": None,
        },
        "cancel_flag": threading.Event(),
        "logs": [],
    }
    return job_id


def test_handle_colab_job_done_flow(tmp_path, monkeypatch):
    job_id = _build_job(tmp_path)

    pushed = []
    sync_calls = []

    monkeypatch.setattr(colab_proxy, "push_event", lambda *args, **kwargs: pushed.append((args, kwargs)))
    monkeypatch.setattr(colab_proxy, "sync_job_to_db", lambda j: sync_calls.append(j))

    def _fake_post(url, **kwargs):
        if url.endswith("/api/transcribe"):
            return _FakeResponse(json_data={"job_id": "remote-1"})
        raise AssertionError(f"Unexpected POST URL {url}")

    def _fake_get(url, **kwargs):
        if url.endswith("/api/jobs/remote-1/stream"):
            lines = [
                b"data: " + json.dumps({"status": "transcribing", "progress": 0.5, "message": "Working"}).encode("utf-8"),
                b"data: " + json.dumps({"status": "done", "progress": 1.0, "message": "Done"}).encode("utf-8"),
            ]
            return _FakeResponse(lines=lines)
        if url.endswith("/api/jobs/remote-1/result"):
            return _FakeResponse(json_data={"language": "en", "segments": [], "duration": 1.0})
        raise AssertionError(f"Unexpected GET URL {url}")

    monkeypatch.setattr(colab_proxy.requests, "post", _fake_post)
    monkeypatch.setattr(colab_proxy.requests, "get", _fake_get)

    colab_proxy.handle_colab_job(job_id)

    assert state.jobs[job_id]["result"]["language"] == "en"
    assert any(args[1] == "done" for args, _ in pushed)
    assert sync_calls == [job_id]


def test_handle_colab_job_error_status_flow(tmp_path, monkeypatch):
    job_id = _build_job(tmp_path)

    pushed = []
    sync_calls = []

    monkeypatch.setattr(colab_proxy, "push_event", lambda *args, **kwargs: pushed.append((args, kwargs)))
    monkeypatch.setattr(colab_proxy, "sync_job_to_db", lambda j: sync_calls.append(j))

    monkeypatch.setattr(colab_proxy.requests, "post", lambda *_args, **_kwargs: _FakeResponse(json_data={"job_id": "remote-2"}))

    def _fake_get(url, **kwargs):
        if url.endswith("/api/jobs/remote-2/stream"):
            lines = [
                b"data: " + json.dumps({"status": "error", "progress": -1, "message": "remote failed"}).encode("utf-8"),
            ]
            return _FakeResponse(lines=lines)
        raise AssertionError(f"Unexpected GET URL {url}")

    monkeypatch.setattr(colab_proxy.requests, "get", _fake_get)

    colab_proxy.handle_colab_job(job_id)

    assert state.jobs[job_id]["error"] == "remote failed"
    assert any(args[1] == "error" for args, _ in pushed)
    assert sync_calls == [job_id]


def test_handle_colab_job_400_includes_remote_detail(tmp_path, monkeypatch):
    job_id = _build_job(tmp_path)

    class _BadRequestResponse:
        status_code = 400
        reason = "Bad Request"

        def raise_for_status(self):
            raise requests.HTTPError("400 Client Error", response=self)

        def json(self):
            return {"detail": "Unsupported file type: .webm"}

    import requests

    monkeypatch.setattr(colab_proxy.requests, "post", lambda *_args, **_kwargs: _BadRequestResponse())

    colab_proxy.handle_colab_job(job_id)

    assert "Unsupported file type: .webm" in (state.jobs[job_id].get("error") or "")
