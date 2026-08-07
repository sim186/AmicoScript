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
    from core import job_lifecycle

    audio = tmp_path / "meeting.mp3"
    audio.write_bytes(b"audio")
    recording_id = _make_interrupted(status, str(audio))

    async def _run():
        state._init_queue()
        state.jobs.clear()
        job_lifecycle.recover_interrupted_jobs()

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
    from core import job_lifecycle

    recording_id = _make_interrupted("downloading", str(tmp_path / "never-saved.mp3"))

    async def _run():
        state._init_queue()
        state.jobs.clear()
        job_lifecycle.recover_interrupted_jobs()

    asyncio.run(_run())

    status, detail = _status(recording_id)
    assert status == "interrupted"
    assert "import it again" in detail
    assert state.JOB_QUEUE.qsize() == 0


@pytest.mark.usefixtures("api_app")
def test_resume_can_be_disabled(tmp_path, monkeypatch):
    from core import job_lifecycle

    audio = tmp_path / "meeting.mp3"
    audio.write_bytes(b"audio")
    recording_id = _make_interrupted("transcribing", str(audio))
    monkeypatch.setenv("AMICOSCRIPT_RESUME_JOBS", "0")

    async def _run():
        state._init_queue()
        state.jobs.clear()
        job_lifecycle.recover_interrupted_jobs()

    asyncio.run(_run())

    assert _status(recording_id)[0] == "interrupted"
    assert state.JOB_QUEUE.qsize() == 0


@pytest.mark.usefixtures("api_app")
def test_finished_recordings_are_left_alone(tmp_path):
    from core import job_lifecycle

    audio = tmp_path / "done.mp3"
    audio.write_bytes(b"audio")
    recording_id = _make_interrupted("done", str(audio))

    async def _run():
        state._init_queue()
        state.jobs.clear()
        job_lifecycle.recover_interrupted_jobs()

    asyncio.run(_run())

    assert _status(recording_id)[0] == "done"


# --- job expiry -------------------------------------------------------------


def test_expiring_a_job_keeps_a_tombstone():
    from core import job_lifecycle

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
        job_lifecycle.expire_job("j1")
        tombstone = state.jobs["j1"]
        assert tombstone["expired"] is True
        assert tombstone["recording_id"] == "rec-9"
        assert tombstone["result"] is None
        assert tombstone["logs"] == []
    finally:
        state.jobs.pop("j1", None)


# --- download prefetch ------------------------------------------------------


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


# --- the sweep, now that it is not welded to an hour-long loop ---------------


def test_the_sweep_expires_only_what_is_finished_and_old():
    """The loop used to hold this logic, so it could only be read, not run."""
    import time

    from core import job_lifecycle
    from core.job_status import JobStatus

    old = time.time() - 7200
    state.jobs.clear()
    state.jobs["finished"] = {"id": "finished", "status": JobStatus.DONE, "created_at": old,
                              "temp_files": [], "logs": []}
    state.jobs["downloading"] = {"id": "downloading", "status": JobStatus.DOWNLOADING,
                                 "created_at": old, "temp_files": [], "logs": []}
    state.jobs["recent"] = {"id": "recent", "status": JobStatus.DONE,
                            "created_at": time.time(), "temp_files": [], "logs": []}
    try:
        assert job_lifecycle.sweep_expired_jobs(time.time() - 3600) == 1
        assert state.jobs["finished"]["expired"] is True
        assert "expired" not in state.jobs["downloading"]
        assert "expired" not in state.jobs["recent"]
    finally:
        state.jobs.clear()


def test_a_sweep_deletes_temp_files_but_not_managed_audio(tmp_path, monkeypatch):
    import time

    from core import job_lifecycle
    from core.job_status import JobStatus

    managed = tmp_path / "library" / "original.mp3"
    managed.parent.mkdir()
    managed.write_bytes(b"audio")
    scratch = tmp_path / "scratch.wav"
    scratch.write_bytes(b"wav")
    monkeypatch.setattr("config.STORAGE_ROOT", tmp_path / "library")

    state.jobs.clear()
    state.jobs["j"] = {
        "id": "j", "status": JobStatus.DONE, "created_at": 0.0,
        "file_path": str(managed), "temp_files": [str(scratch)], "logs": [],
    }
    try:
        job_lifecycle.sweep_expired_jobs(time.time())
        assert managed.exists(), "the library's copy is not the job's to delete"
        assert not scratch.exists()
    finally:
        state.jobs.clear()


# --- classifying what a restart left behind ---------------------------------


def test_a_recording_whose_audio_survived_is_requeued(tmp_path):
    from core.job_lifecycle import classify_interrupted
    from core.job_status import JobStatus
    from models import Recording

    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    rec = Recording(filename="a.mp3", file_path=str(audio), status="transcribing")

    status, detail = classify_interrupted(rec, resume=True)
    assert status == JobStatus.QUEUED
    assert "restart" in detail


def test_a_recording_whose_audio_is_gone_says_to_import_it_again():
    from core.job_lifecycle import classify_interrupted
    from core.job_status import JobStatus
    from models import Recording

    rec = Recording(filename="a.mp3", file_path="/gone/a.mp3", status="transcribing")

    status, detail = classify_interrupted(rec, resume=True)
    assert status == JobStatus.INTERRUPTED
    assert "import it again" in detail


def test_resume_disabled_still_explains_itself(tmp_path):
    """Opting out of recovery must not go back to a bare 'error'."""
    from core.job_lifecycle import classify_interrupted
    from core.job_status import JobStatus
    from models import Recording

    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    rec = Recording(filename="a.mp3", file_path=str(audio), status="transcribing")

    status, detail = classify_interrupted(rec, resume=False)
    assert status == JobStatus.INTERRUPTED
    assert detail
