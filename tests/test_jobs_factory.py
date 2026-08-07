"""One factory means one shape, whatever kind of job it is.

Four separate constructors used to produce four different key sets, so every
reader had to guess with `.get()` and a default. These tests pin the property
that removed the guessing.
"""
import asyncio
import threading
from collections import deque

import pytest

import state
from core.job_status import JobStatus
from core.jobs import create_job, submit_threadsafe


def _shape(job_id: str) -> frozenset:
    return frozenset(state.jobs[job_id].keys())


def test_every_job_type_has_the_same_keys():
    """A translate job had no source_url; only an analysis job had analysis_id."""
    ids = [
        create_job(job_type="transcribe", recording_id="r1", file_path="/a.mp3"),
        create_job(job_type="download_transcribe", source_url="https://x/y", source_platform="youtube"),
        create_job(job_type="translate", recording_id="r2"),
        create_job(job_type="analysis", recording_id="r3", analysis_id="a1"),
    ]

    assert len({_shape(job_id) for job_id in ids}) == 1


def test_unused_fields_are_present_and_empty_rather_than_absent():
    """That is what lets a reader index instead of .get()."""
    job = state.jobs[create_job(job_type="translate", recording_id="r1")]

    assert job["source_url"] == ""
    assert job["source_platform"] == ""
    assert job["analysis_id"] == ""
    assert job["resumed"] is False
    assert job["auto_generated"] is False


def test_a_new_job_is_queued_and_carries_its_machinery():
    job = state.jobs[create_job()]

    assert job["status"] == JobStatus.QUEUED
    assert job["progress"] == 0.0
    assert job["result"] is None
    assert job["error"] is None
    assert isinstance(job["sse_queue"], asyncio.Queue)
    assert isinstance(job["cancel_flag"], threading.Event)
    assert isinstance(job["logs"], deque)
    assert job["temp_files"] == []


def test_options_are_copied_so_callers_cannot_mutate_a_live_job():
    opts = {"model": "small"}
    job = state.jobs[create_job(options=opts)]

    opts["model"] = "large"

    assert job["options"]["model"] == "small"


def test_an_explicit_job_id_is_honoured():
    """The upload route allocates the id before the job, to name the staging file."""
    assert create_job(job_id="chosen-id") == "chosen-id"
    assert state.jobs["chosen-id"]["id"] == "chosen-id"


# --- submitting --------------------------------------------------------------


def test_submit_threadsafe_reports_failure_when_there_is_no_loop(monkeypatch):
    """The caller has to know, so it can drop a job nothing will ever pick up."""
    monkeypatch.setattr(state, "event_loop", None)

    assert submit_threadsafe(create_job()) is False


def test_submit_threadsafe_hands_the_job_to_a_running_loop():
    async def _run():
        state._init_queue()
        state.event_loop = asyncio.get_running_loop()
        job_id = create_job()

        assert submit_threadsafe(job_id) is True
        # call_soon_threadsafe is deferred by one loop iteration.
        await asyncio.sleep(0)
        return job_id, state.JOB_QUEUE.get_nowait()

    job_id, queued = asyncio.run(_run())
    assert queued == job_id


@pytest.mark.parametrize("running", [True, False])
def test_the_sse_loop_is_resolved_from_the_calling_context(running, monkeypatch):
    """Routes run on the loop and can ask it directly; the worker thread cannot.

    Each old factory hard-coded one of the two answers, and was wrong in the
    other context.
    """
    sentinel = object()
    monkeypatch.setattr(state, "event_loop", sentinel)

    if not running:
        assert state.jobs[create_job()]["event_loop"] is sentinel
        return

    async def _run():
        return state.jobs[create_job()]["event_loop"] is asyncio.get_running_loop()

    assert asyncio.run(_run()) is True
