"""Putting an existing recording back on the transcription queue.

Two callers need this: startup recovery, which requeues anything a restart
interrupted, and the retry button, which lets a user re-run a transcription that
failed. Both start from the same place — a Recording row whose audio is still on
disk — so the job-building lives here rather than being written twice.
"""
from __future__ import annotations

import json
import os

import state
from core.job_status import ACTIVE as ACTIVE_STATUSES
from core.job_status import JobStatus
from core.jobs import create_job
from db import new_session
from models import Recording
from settings import _get_saved_hf_token


class RequeueError(RuntimeError):
    """Raised when a recording cannot be put back on the queue."""


def build_job(recording_id: str, filename: str, file_path: str, opts: dict,
              *, resumed: bool = False) -> str:
    """Create the in-memory job for *recording_id* and return its id.

    Does not enqueue — the caller decides, because the worker thread and the
    event loop submit differently.
    """
    return create_job(
        job_type="transcribe",
        recording_id=recording_id,
        original_filename=filename,
        file_path=file_path,
        options={**opts, "hf_token": opts.get("hf_token") or _get_saved_hf_token()},
        message="Requeued",
        resumed=resumed,
    )


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
            if job.get("recording_id") == recording_id and job.get("status") in ACTIVE_STATUSES:
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

        rec.status = JobStatus.QUEUED
        rec.status_detail = "Queued again at your request"
        rec.transcription_options = json.dumps(opts)
        session.add(rec)
        session.commit()

        filename, file_path = rec.filename, rec.file_path

    return build_job(recording_id, filename, file_path, opts)


