"""Building and submitting background jobs.

A job is a plain dict in ``state.jobs``, read and written by routes, the worker
thread and the SSE stream alike. It used to be *built* in four places — the
upload route, the bulk-translate route, requeue, and the analysis entrypoint —
and the four disagreed about which keys existed. A translate job had no
``source_url``; only an analysis job had ``analysis_id``; only a requeued one
had ``resumed``.

Since no reader could rely on a key, every reader defended itself with
``.get()`` and a default, and the log handler went further and repaired the
type of ``logs`` on first use because three of the factories seeded it with a
list where a bounded deque was wanted. None of that is robustness — it is
interest paid on a record with no definition.

``create_job`` is that definition. Every job carries every key, so the fields a
job type does not use are present and empty rather than absent.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import deque
from enum import StrEnum
from typing import Any

import state
from core.job_status import JobStatus

#: How many log lines a job keeps in memory. Old lines fall off the front —
#: the full history goes to the logger, this ring is only what the logs panel
#: can ask for.
JOB_LOG_LIMIT = 1000


class JobType(StrEnum):
    """What a job is for.

    The values are the strings already stored in ``job["type"]`` and published
    by ``GET /api/jobs``, so a member compares equal to the plain string the
    worker dispatches on.
    """

    #: Transcribe a file that is already on disk.
    TRANSCRIBE = "transcribe"
    #: Fetch audio from a URL first, then transcribe it.
    DOWNLOAD_TRANSCRIBE = "download_transcribe"
    #: Translate an existing transcript's segments, in bulk.
    TRANSLATE = "translate"
    #: Run an LLM over an existing transcript. Writes an Analysis row.
    ANALYSIS = "analysis"


#: Job types whose status *is* the recording's status, and which may therefore
#: write it.
#:
#: A transcription defines the recording: while it runs the row reads
#: `transcribing`, and if it fails the recording really has failed. The other
#: two only read a recording that is already finished — a translation and an
#: analysis both need a transcript to exist before they can start — so neither
#: has anything to say about the row's state. Letting them say it anyway meant
#: a summary the LLM refused left the recording reading `error` with its
#: transcript intact, and the UI offering to transcribe it all over again.
#:
#: An analysis reports its own outcome on the Analysis row, which has a
#: `status` column of its own.
OWNS_RECORDING_STATUS: frozenset[str] = frozenset({
    JobType.TRANSCRIBE,
    JobType.DOWNLOAD_TRANSCRIBE,
})


def _sse_target_loop() -> asyncio.AbstractEventLoop | None:
    """The loop that SSE events have to be handed back to.

    A route is already running on it and can just ask. The worker thread and
    startup recovery are not, and fall back to the loop main.py recorded at
    startup. Previously each factory picked one of the two and was wrong in the
    other context.
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return state.event_loop


def create_job(
    *,
    job_type: str = JobType.TRANSCRIBE,
    recording_id: str = "",
    original_filename: str = "",
    file_path: str = "",
    options: dict[str, Any] | None = None,
    message: str = "Queued",
    source_url: str = "",
    source_platform: str = "",
    analysis_id: str = "",
    resumed: bool = False,
    auto_generated: bool = False,
    job_id: str = "",
) -> str:
    """Register a new job in ``state.jobs`` and return its id.

    Does not enqueue it — ``submit`` and ``submit_threadsafe`` do that, and
    which one is correct depends on the caller's thread.
    """
    job_id = job_id or str(uuid.uuid4())
    state.jobs[job_id] = {
        "id": job_id,
        "type": job_type,
        "recording_id": recording_id,
        # Only analysis jobs write a row of their own; "" for everything else.
        "analysis_id": analysis_id,
        "status": JobStatus.QUEUED,
        "progress": 0.0,
        "message": message,
        "file_path": file_path,
        "original_filename": original_filename,
        "options": dict(options or {}),
        "source_url": source_url,
        "source_platform": source_platform,
        "result": None,
        "error": None,
        "created_at": time.time(),
        "sse_queue": asyncio.Queue(),
        "event_loop": _sse_target_loop(),
        "cancel_flag": threading.Event(),
        "logs": deque(maxlen=JOB_LOG_LIMIT),
        "temp_files": [],
        # True when a restart interrupted this recording and startup put it
        # back, rather than a person asking for it.
        "resumed": resumed,
        # True when AmicoScript started this itself (the auto-summary of a
        # captured meeting).
        "auto_generated": auto_generated,
    }
    return job_id


def submit(job_id: str) -> None:
    """Put *job_id* on the worker queue. Call from the event loop."""
    state.JOB_QUEUE.put_nowait(job_id)


def submit_threadsafe(job_id: str) -> bool:
    """Put *job_id* on the worker queue from a thread that is not the loop.

    Returns False when there is no running loop to hand it to — during startup,
    or at shutdown — so the caller can drop the job rather than leave it
    registered and never picked up.
    """
    loop = state.event_loop
    if loop is None or not loop.is_running():
        return False
    loop.call_soon_threadsafe(state.JOB_QUEUE.put_nowait, job_id)
    return True
