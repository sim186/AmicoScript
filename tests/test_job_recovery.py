"""Tests for restart recovery, job expiry, and the download prefetch stage."""
import asyncio
import json

import pytest

import state


# --- restart recovery -------------------------------------------------------


def _make_interrupted(status: str, file_path: str) -> str:
    import uuid

    from db import new_session
    from models import Recording

    recording_id = str(uuid.uuid4())
    with new_session() as session:
        session.add(
            Recording(
                id=recording_id,
                filename="long-meeting.mp3",
                file_path=file_path,
                status=status,
                transcription_options=json.dumps({"model": "medium", "diarize": True}),
            )
        )
        session.commit()
    return recording_id


def _status(recording_id: str):
    from db import new_session
    from models import Recording

    with new_session() as session:
        rec = session.get(Recording, recording_id)
        return rec.status, rec.status_detail


@pytest.mark.usefixtures("api_app")
@pytest.mark.parametrize("status", ["queued", "transcribing", "diarizing", "loading_model"])
def test_interrupted_job_with_audio_is_requeued(status, tmp_path, monkeypatch):
    """A restart used to destroy 90%-finished work with no explanation."""
    import main

    audio = tmp_path / "meeting.mp3"
    audio.write_bytes(b"audio")
    recording_id = _make_interrupted(status, str(audio))

    async def _run():
        state._init_queue()
        state.jobs.clear()
        main._recover_interrupted_jobs()

    asyncio.run(_run())

    assert _status(recording_id)[0] == "queued"
    assert "restart" in _status(recording_id)[1]

    queued = [j for j in state.jobs.values() if j.get("recording_id") == recording_id]
    assert len(queued) == 1
    assert queued[0]["resumed"] is True
    # Transcription options survive the round trip, so it resumes with the same
    # model and diarization setting the user picked.
    assert queued[0]["options"]["model"] == "medium"
    assert queued[0]["options"]["diarize"] is True
    assert state.JOB_QUEUE.qsize() == 1


@pytest.mark.usefixtures("api_app")
def test_interrupted_job_without_audio_is_marked_not_errored(tmp_path):
    import main

    recording_id = _make_interrupted("downloading", str(tmp_path / "never-saved.mp3"))

    async def _run():
        state._init_queue()
        state.jobs.clear()
        main._recover_interrupted_jobs()

    asyncio.run(_run())

    status, detail = _status(recording_id)
    assert status == "interrupted"
    assert "import it again" in detail
    assert state.JOB_QUEUE.qsize() == 0


@pytest.mark.usefixtures("api_app")
def test_resume_can_be_disabled(tmp_path, monkeypatch):
    import main

    audio = tmp_path / "meeting.mp3"
    audio.write_bytes(b"audio")
    recording_id = _make_interrupted("transcribing", str(audio))
    monkeypatch.setenv("AMICOSCRIPT_RESUME_JOBS", "0")

    async def _run():
        state._init_queue()
        state.jobs.clear()
        main._recover_interrupted_jobs()

    asyncio.run(_run())

    assert _status(recording_id)[0] == "interrupted"
    assert state.JOB_QUEUE.qsize() == 0


@pytest.mark.usefixtures("api_app")
def test_finished_recordings_are_left_alone(tmp_path):
    import main

    audio = tmp_path / "done.mp3"
    audio.write_bytes(b"audio")
    recording_id = _make_interrupted("done", str(audio))

    async def _run():
        state._init_queue()
        state.jobs.clear()
        main._recover_interrupted_jobs()

    asyncio.run(_run())

    assert _status(recording_id)[0] == "done"


# --- job expiry -------------------------------------------------------------


def test_expiring_a_job_keeps_a_tombstone():
    import main

    state.jobs["j1"] = {
        "id": "j1",
        "type": "transcribe",
        "recording_id": "rec-9",
        "status": "done",
        "progress": 1.0,
        "message": "Complete",
        "original_filename": "a.mp3",
        "created_at": 1.0,
        "result": {"segments": []},
        "logs": [{"msg": "x"}],
        "temp_files": [],
    }
    try:
        main._expire_job("j1")
        tombstone = state.jobs["j1"]
        assert tombstone["expired"] is True
        assert tombstone["recording_id"] == "rec-9"
        assert tombstone["result"] is None
        assert tombstone["logs"] == []
    finally:
        state.jobs.pop("j1", None)


# --- download prefetch ------------------------------------------------------


def test_download_concurrency_is_read_from_the_environment(monkeypatch):
    from core import transcription

    monkeypatch.setenv("AMICOSCRIPT_DOWNLOAD_CONCURRENCY", "5")
    assert transcription._download_concurrency() == 5
    monkeypatch.setenv("AMICOSCRIPT_DOWNLOAD_CONCURRENCY", "0")
    assert transcription._download_concurrency() == 1
    monkeypatch.setenv("AMICOSCRIPT_DOWNLOAD_CONCURRENCY", "99")
    assert transcription._download_concurrency() == 8
    monkeypatch.setenv("AMICOSCRIPT_DOWNLOAD_CONCURRENCY", "nonsense")
    assert transcription._download_concurrency() == 2


def test_downloads_of_separate_jobs_overlap(monkeypatch):
    """Two URL imports must not serialize behind each other."""
    import threading

    from core import transcription

    monkeypatch.setattr(transcription, "_download_semaphore", None)
    monkeypatch.setenv("AMICOSCRIPT_DOWNLOAD_CONCURRENCY", "2")

    in_flight = 0
    peak = 0
    lock = threading.Lock()
    both_started = threading.Barrier(2, timeout=5)

    def fake_download(job_id):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        both_started.wait()
        with lock:
            in_flight -= 1
        return False

    monkeypatch.setattr(transcription, "_run_download_phase", fake_download)

    async def _run():
        for job_id in ("a", "b"):
            state.jobs[job_id] = {
                "id": job_id, "type": "download_transcribe",
                "cancel_flag": None, "logs": [],
            }
            transcription.start_download_prefetch(job_id)
        await asyncio.gather(
            transcription._await_download_prefetch("a"),
            transcription._await_download_prefetch("b"),
        )

    try:
        asyncio.run(_run())
        assert peak == 2
    finally:
        state.jobs.clear()


def test_prefetch_is_bounded_by_the_semaphore(monkeypatch):
    import threading

    from core import transcription

    monkeypatch.setattr(transcription, "_download_semaphore", None)
    monkeypatch.setenv("AMICOSCRIPT_DOWNLOAD_CONCURRENCY", "1")

    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def fake_download(job_id):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        import time
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return False

    monkeypatch.setattr(transcription, "_run_download_phase", fake_download)

    async def _run():
        for job_id in ("a", "b", "c"):
            state.jobs[job_id] = {
                "id": job_id, "type": "download_transcribe",
                "cancel_flag": None, "logs": [],
            }
            transcription.start_download_prefetch(job_id)
        await asyncio.gather(
            *(transcription._await_download_prefetch(j) for j in ("a", "b", "c"))
        )

    try:
        asyncio.run(_run())
        assert peak == 1
    finally:
        state.jobs.clear()


def test_a_prefetch_failure_is_raised_on_the_worker(monkeypatch):
    """Download errors must still surface through the job's normal error path."""
    from core import transcription

    monkeypatch.setattr(transcription, "_download_semaphore", None)

    def boom(job_id):
        raise RuntimeError("video unavailable")

    monkeypatch.setattr(transcription, "_run_download_phase", boom)

    async def _run():
        state.jobs["j"] = {
            "id": "j", "type": "download_transcribe", "cancel_flag": None, "logs": [],
        }
        transcription.start_download_prefetch("j")
        await transcription._await_download_prefetch("j")

    try:
        asyncio.run(_run())
        with pytest.raises(RuntimeError, match="video unavailable"):
            transcription._consume_download_phase("j")
    finally:
        state.jobs.clear()


def test_a_job_that_was_not_prefetched_downloads_inline(monkeypatch):
    """Resumed jobs have no prefetch task; they must still work."""
    from core import transcription

    called = []
    monkeypatch.setattr(
        transcription, "_run_download_phase", lambda job_id: called.append(job_id) or False
    )
    state.jobs["j"] = {"id": "j", "type": "download_transcribe", "logs": []}
    try:
        assert transcription._consume_download_phase("j") is False
        assert called == ["j"]
    finally:
        state.jobs.clear()


def test_prefetch_is_skipped_for_non_download_jobs():
    from core import transcription

    state.jobs["j"] = {"id": "j", "type": "transcribe", "logs": []}
    try:
        asyncio.run(_noop_prefetch(transcription, "j"))
        assert "download_task" not in state.jobs["j"]
    finally:
        state.jobs.clear()


async def _noop_prefetch(transcription, job_id):
    transcription.start_download_prefetch(job_id)
