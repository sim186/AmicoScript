"""What happens to a job before the worker sees it, and after it is done.

Two ends of the same story, and neither is application wiring, which is why
they no longer live in main.py: recovery puts back the work a restart
interrupted, and expiry releases the memory a finished job is still holding.

Both turn on the question "is this job still working?", and both used to answer
it with a status set of their own invention — see core/job_status.py for what
that cost.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import config
import state
from core.job_helpers import cleanup_job_temp_files
from core.job_status import ACTIVE, RESUMABLE, JobStatus
from core.jobs import submit
from core.runtime_config import resume_interrupted_jobs
from core.requeue import build_job
from db import new_session
from models import Recording
from sqlmodel import select
from utils.logging_utils import get_logger

logger = get_logger("amicoscript.lifecycle")

#: How long a finished job stays addressable before it is reduced to a
#: tombstone, and how often the sweep runs.
JOB_TTL_SECONDS = 3600
CLEANUP_INTERVAL_SECONDS = 3600


# ---------------------------------------------------------------------------
# Recovery — what a restart interrupted
# ---------------------------------------------------------------------------


def classify_interrupted(recording: Recording, *, resume: bool) -> tuple[str, str]:
    """Decide what an interrupted *recording* becomes: (status, status_detail).

    Split out from the sweep so the rule can be read and tested on its own —
    it is the part that decides whether someone's two-hour meeting is retried
    or written off.
    """
    audio_exists = bool(recording.file_path) and os.path.exists(recording.file_path)
    if resume and audio_exists:
        return JobStatus.QUEUED, "Requeued after the app restarted"
    if not audio_exists:
        return JobStatus.INTERRUPTED, (
            "Interrupted by an app restart before the audio was saved — "
            "please import it again"
        )
    return JobStatus.INTERRUPTED, "Interrupted by an app restart"


def recover_interrupted_jobs() -> None:
    """Requeue work that a restart interrupted instead of failing it.

    The old behaviour flipped every in-flight recording to 'error' with no
    explanation, so a two-hour meeting that was 90% transcribed was simply lost
    and the user had to re-upload it with no idea why. Now anything whose audio
    is still on disk goes back into the queue, and anything that is not
    resumable is marked 'interrupted' with a reason the UI can display.
    """
    resume = resume_interrupted_jobs()
    requeued: list[tuple[str, str, str, dict]] = []

    try:
        with new_session() as session:
            interrupted = session.exec(
                select(Recording).where(Recording.status.in_(sorted(RESUMABLE)))
            ).all()
            for rec in interrupted:
                status, detail = classify_interrupted(rec, resume=resume)
                rec.status, rec.status_detail = status, detail
                if status == JobStatus.QUEUED:
                    requeued.append(
                        (rec.id, rec.filename, rec.file_path, _stored_options(rec))
                    )
                session.add(rec)
            session.commit()
    except Exception:
        logger.exception("Could not recover interrupted jobs")
        return

    for recording_id, filename, file_path, opts in requeued:
        try:
            requeue_after_restart(recording_id, filename, file_path, opts)
        except Exception:
            logger.exception("Could not requeue recording %s", recording_id)

    if requeued:
        logger.info("Requeued %d interrupted transcription job(s)", len(requeued))


def _stored_options(recording: Recording) -> dict:
    try:
        return json.loads(recording.transcription_options or "{}")
    except (TypeError, ValueError):
        return {}


def requeue_after_restart(
    recording_id: str, filename: str, file_path: str, opts: dict
) -> None:
    """Recreate the in-memory job for a recording and put it back on the queue."""
    job_id = build_job(recording_id, filename, file_path, opts, resumed=True)
    state.jobs[job_id]["message"] = "Requeued after restart"
    submit(job_id)


# ---------------------------------------------------------------------------
# Expiry — releasing what is finished
# ---------------------------------------------------------------------------


def should_expire(job: dict, cutoff: float) -> bool:
    """True when *job* is old enough to release and is no longer working.

    Age alone is not enough. A playlist import or a chunked analysis of a long
    meeting can easily outlive the hour, and expiring one of those mid-flight
    detaches the worker from its own SSE queue — see core/job_status.py.
    """
    if job.get("expired") or job.get("status") in ACTIVE:
        return False
    return job.get("created_at", 0) < cutoff


def expire_job(job_id: str) -> None:
    """Release a finished job's memory but keep a tombstone.

    Dropping the entry outright made every /api/jobs/{id}/… route answer 404,
    which is indistinguishable from "that job never existed" — the UI could not
    tell the user their transcript was safe in the library. The tombstone keeps
    the status and the recording id so routes can redirect there instead.
    """
    job = state.jobs.get(job_id)
    if not job:
        return
    state.jobs[job_id] = {
        "id": job_id,
        "type": job.get("type", "transcribe"),
        "recording_id": job.get("recording_id"),
        "status": job.get("status", JobStatus.DONE),
        "progress": job.get("progress", 1.0),
        "message": job.get("message", ""),
        "original_filename": job.get("original_filename", ""),
        "created_at": job.get("created_at", 0.0),
        "expired": True,
        "result": None,
        "logs": [],
        "temp_files": [],
    }


def sweep_expired_jobs(cutoff: float) -> int:
    """Expire every job older than *cutoff* that is no longer working.

    Returns how many were expired. Separate from the loop so a test does not
    have to wait out an hour of `asyncio.sleep` to exercise it.
    """

    expired = 0
    for job_id in list(state.jobs.keys()):
        job = state.jobs[job_id]
        if not should_expire(job, cutoff):
            continue
        file_path = job.get("file_path", "")
        if file_path and os.path.exists(file_path):
            try:
                # Managed storage belongs to the library, not to the job.
                if not Path(file_path).is_relative_to(config.STORAGE_ROOT):
                    os.remove(file_path)
            except OSError:
                pass
        cleanup_job_temp_files(job)
        expire_job(job_id)
        expired += 1
    return expired


async def cleanup_loop() -> None:
    """Sweep expired jobs once an hour, for as long as the app runs."""

    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        sweep_expired_jobs(time.time() - JOB_TTL_SECONDS)
