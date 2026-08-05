"""Putting an existing recording back on the transcription queue.

Two callers need this: startup recovery, which requeues anything a restart
interrupted, and the retry button, which lets a user re-run a transcription that
failed. Both start from the same place — a Recording row whose audio is still on
disk — so the job-building lives here rather than being written twice.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid

import state
from db import new_session
from models import Recording
from settings import _get_saved_hf_token

# A recording in one of these states is finished with, one way or another, so
# re-running it is meaningful. Anything else is already in flight.
RETRYABLE_STATUSES = {"error", "interrupted", "cancelled", "done"}


class RequeueError(RuntimeError):
    """Raised when a recording cannot be put back on the queue."""


def build_job(recording_id: str, filename: str, file_path: str, opts: dict,
              *, resumed: bool = False) -> str:
    """Create the in-memory job for *recording_id* and return its id.

    Does not enqueue — the caller decides, because the worker thread and the
    event loop submit differently.
    """
    job_id = str(uuid.uuid4())
    state.jobs[job_id] = {
        "id": job_id,
        "type": "transcribe",
        "recording_id": recording_id,
        "status": "queued",
        "progress": 0.0,
        "message": "Requeued",
        "file_path": file_path,
        "original_filename": filename,
        "options": {**opts, "hf_token": opts.get("hf_token") or _get_saved_hf_token()},
        "source_url": "",
        "source_platform": "",
        "result": None,
        "error": None,
        "created_at": time.time(),
        "sse_queue": asyncio.Queue(),
        "event_loop": state.event_loop,
        "cancel_flag": threading.Event(),
        "logs": [],
        "temp_files": [],
        "resumed": resumed,
    }
    return job_id


def requeue_recording(recording_id: str, *, options: dict | None = None) -> str:
    """Queue *recording_id* for transcription again. Returns the new job id.

    Raises RequeueError with a message meant for the user: the recording is
    already being processed, or its audio is gone and only a fresh import can
    recover it.
    """
    with new_session() as session:
        rec = session.get(Recording, recording_id)
        if rec is None:
            raise RequeueError("Recording not found")

        for job in state.jobs.values():
            if job.get("recording_id") == recording_id and job.get("status") in (
                "queued", "downloading", "preparing", "loading_model",
                "transcribing", "diarizing", "translating",
            ):
                raise RequeueError("This recording is already being processed.")

        if not rec.file_path or not os.path.exists(rec.file_path):
            raise RequeueError(
                "The audio for this recording is no longer on disk, so it cannot "
                "be transcribed again — please import the file once more."
            )

        try:
            stored = json.loads(rec.transcription_options or "{}")
        except (TypeError, ValueError):
            stored = {}
        opts = {**stored, **(options or {})}

        rec.status = "queued"
        rec.status_detail = "Queued again at your request"
        rec.transcription_options = json.dumps(opts)
        session.add(rec)
        session.commit()

        filename, file_path = rec.filename, rec.file_path

    return build_job(recording_id, filename, file_path, opts)


def enqueue_from_loop(job_id: str) -> None:
    state.JOB_QUEUE.put_nowait(job_id)


def enqueue_from_thread(job_id: str) -> bool:
    """Submit from a non-loop thread (startup recovery, the worker)."""
    loop = state.event_loop
    if loop is None or not loop.is_running():
        return False
    loop.call_soon_threadsafe(state.JOB_QUEUE.put_nowait, job_id)
    return True
