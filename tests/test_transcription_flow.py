import asyncio
import threading
from types import SimpleNamespace

import state
from core import transcription


class _FakeWord:
    def __init__(self, word: str, start: float, end: float, probability: float):
        self.word = word
        self.start = start
        self.end = end
        self.probability = probability


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str):
        self.start = start
        self.end = end
        self.text = text
        self.words = [_FakeWord("hello", start, end, 0.9)]


class _FakeModel:
    def transcribe(self, *_args, **_kwargs):
        def _gen():
            yield _FakeSegment(0.0, 1.0, " hello ")
            yield _FakeSegment(1.0, 2.0, " world ")

        return _gen(), SimpleNamespace(duration=2.0, language="en")


def _base_job() -> dict:
    return {
        "id": "job-t",
        "status": "queued",
        "progress": 0.0,
        "message": "Queued",
        "file_path": "/tmp/input.mp3",
        "options": {
            "model": "small",
            "language": "",
            "diarize": False,
            "word_timestamps": False,
            "vad_filter": True,
            "beam_size": 5,
            "best_of": 5,
            "force_normalize_audio": False,
        },
        "sse_queue": asyncio.Queue(),
        "cancel_flag": threading.Event(),
        "logs": [],
        "temp_files": [],
    }


def test_run_transcription_phase_emits_progress_and_segments(monkeypatch):
    job_id = "job-t"
    state.jobs[job_id] = _base_job()

    events = []
    monkeypatch.setattr(transcription, "push_event", lambda *_args, **kwargs: events.append((_args, kwargs)))
    monkeypatch.setattr(transcription, "get_whisper_model", lambda *args, **kwargs: (_FakeModel(), "cpu"))
    monkeypatch.setattr(transcription, "convert_audio_for_transcription", lambda *_args, **_kwargs: "/tmp/input.mp3")
    monkeypatch.setattr(transcription.ffmpeg_helper, "start_background_download", lambda: None)

    segments, meta = transcription.run_transcription_phase(job_id)

    assert len(segments) == 2
    assert segments[0]["text"] == "hello"
    assert segments[1]["text"] == "world"
    assert meta == {"language": "en", "duration": 2.0}
    assert any(args[1] == "transcribing" for args, _ in events)


def test_process_job_cancelled_path(monkeypatch):
    job_id = "job-cancel"
    state.jobs[job_id] = _base_job()

    def _cancelled_phase(_job_id):
        return [], {"cancelled": True}

    finalized = []
    monkeypatch.setattr(transcription, "run_transcription_phase", _cancelled_phase)
    monkeypatch.setattr(transcription, "run_diarization_phase", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(transcription, "finalize_transcription_result", lambda *_args, **_kwargs: finalized.append(True))
    monkeypatch.setattr(transcription, "cleanup_job_temp_files", lambda _job: None)

    transcription.process_job(job_id)

    assert finalized == []
