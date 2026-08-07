"""The shared job-status vocabulary, and the two bugs its absence caused.

Before core/job_status.py existed, five modules each spelled out their own idea
of "this job is still running" and no two agreed. The tests here pin the two
divergences that were user-visible.
"""
import time

import pytest

import state
from core.job_status import ACTIVE, RESUMABLE, RETRYABLE, TERMINAL, JobStatus


# --- the vocabulary itself ---------------------------------------------------


#: Statuses that only ever appear on a Recording row, never on a job.
_RECORDING_ONLY = {JobStatus.PENDING, JobStatus.INTERRUPTED}


def test_every_status_is_either_active_or_terminal():
    """A new status must be classified, or the cleanup loop will guess wrong.

    This is the assertion that would have caught the original bug: `running`
    and `streaming` were introduced for analysis jobs and never added to any of
    the five "still working" sets.
    """
    unclassified = {
        status for status in JobStatus
        if status not in _RECORDING_ONLY and status not in ACTIVE and status not in TERMINAL
    }
    assert unclassified == set()


def test_active_and_terminal_do_not_overlap():
    assert ACTIVE & TERMINAL == frozenset()


def test_resumable_excludes_the_analysis_states():
    """Recovery rebuilds a *transcription* job.

    If `running`/`streaming` leaked in here, a recording whose analysis was
    interrupted would be transcribed a second time to recover it.
    """
    assert JobStatus.RUNNING not in RESUMABLE
    assert JobStatus.STREAMING not in RESUMABLE
    assert RESUMABLE < ACTIVE


def test_retryable_is_disjoint_from_active():
    """Nothing may be retried while it is still in flight."""
    assert RETRYABLE & ACTIVE == frozenset()


def test_members_compare_equal_to_the_wire_strings():
    """Clients send plain strings; the enum has to interoperate with them."""
    assert JobStatus.TRANSCRIBING == "transcribing"
    assert "transcribing" in ACTIVE
    assert JobStatus.TRANSCRIBING in ACTIVE


# --- regression: jobs vanished from the queue strip --------------------------


def _put_job(job_id: str, status: str) -> None:
    state.jobs[job_id] = {
        "id": job_id,
        "type": "transcribe",
        "status": status,
        "progress": 0.2,
        "message": "",
        "original_filename": "meeting.mp3",
        "created_at": time.time(),
    }


@pytest.mark.parametrize(
    "status",
    [
        JobStatus.LOADING_MODEL,
        JobStatus.TRANSLATING,
        JobStatus.RUNNING,
        JobStatus.STREAMING,
    ],
)
def test_queue_endpoint_lists_jobs_that_used_to_disappear(status, client):
    """GET /api/jobs omitted these four, so a job blinked out of the UI.

    Loading a model is the slowest step of a cold start, and running/streaming
    span an entire LLM analysis — exactly the windows a user wants to watch.
    """
    _put_job("vanishing", status)
    try:
        listed = client.get("/api/jobs").json()["jobs"]
        assert [j["id"] for j in listed] == ["vanishing"]
    finally:
        state.jobs.pop("vanishing", None)


def test_queue_endpoint_still_omits_finished_jobs(client):
    _put_job("finished", JobStatus.DONE)
    try:
        assert client.get("/api/jobs").json()["jobs"] == []
    finally:
        state.jobs.pop("finished", None)


# --- regression: the cleanup loop tombstoned live jobs -----------------------


@pytest.mark.parametrize(
    "status",
    [
        JobStatus.DOWNLOADING,
        JobStatus.RUNNING,
        JobStatus.STREAMING,
        JobStatus.LOADING_MODEL,
    ],
)
def test_a_working_job_is_never_expired_however_old(status):
    """An hour is not long for a playlist import or a chunked summary.

    Expiring one replaced its entry with a tombstone that has no sse_queue, so
    the worker went on pushing events into a record nobody was listening to and
    every /api/jobs/{id}/… route started answering 410.
    """
    import main

    job = {"status": status, "created_at": 0.0}
    assert main._should_expire(job, cutoff=time.time()) is False


def test_a_finished_job_past_the_cutoff_is_expired():
    import main

    job = {"status": JobStatus.DONE, "created_at": 0.0}
    assert main._should_expire(job, cutoff=time.time()) is True


def test_a_finished_job_inside_the_cutoff_is_kept():
    import main

    job = {"status": JobStatus.DONE, "created_at": time.time()}
    assert main._should_expire(job, cutoff=time.time() - 3600) is False


def test_an_already_expired_job_is_not_expired_twice():
    import main

    job = {"status": JobStatus.DONE, "created_at": 0.0, "expired": True}
    assert main._should_expire(job, cutoff=time.time()) is False
