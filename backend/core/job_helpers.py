"""Shared job state helpers used by background processing modules."""
import asyncio
import json
import logging
import os
import time
from typing import Optional

import state
from db import new_session
from models import Recording, Transcript
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from utils.logging_utils import get_logger


logger = get_logger("amicoscript.worker")


def _append_job_log(job_id: str, level: str, message: str) -> None:
    """Append an in-memory log line and emit a structured logger event."""
    job = state.jobs.get(job_id)
    if not job:
        return

    logs = job.setdefault("logs", [])
    logs.append({"ts": round(time.time(), 3), "level": level, "message": message})
    if len(logs) > 1000:
        del logs[:-1000]

    log_level = getattr(logging, level.upper(), None)
    if isinstance(log_level, int):
        logger.log(log_level, message, extra={"job_id": job_id})
    else:
        logger.info(message, extra={"job_id": job_id})


def _push_event(
    job_id: str,
    status: str,
    progress: float,
    message: str,
    data: Optional[dict] = None,
) -> None:
    """Push an SSE event into the job queue, thread-safe when needed."""
    job = state.jobs.get(job_id)
    if not job:
        return

    event: dict = {"status": status, "progress": progress, "message": message}
    if data is not None:
        event["data"] = data

    job["status"] = status
    job["progress"] = progress
    job["message"] = message

    level = "ERROR" if status == "error" else "INFO"
    _append_job_log(job_id, level, f"{status}: {message}")

    queue = job.get("sse_queue")
    if queue is None:
        return

    loop = job.get("event_loop")
    if loop and loop.is_running():
        loop.call_soon_threadsafe(queue.put_nowait, event)
    else:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


def _cleanup_job_temp_files(job: dict) -> None:
    """Delete all temporary files associated with a job."""
    for temp_fp in job.get("temp_files", []):
        if temp_fp and os.path.exists(temp_fp):
            try:
                os.remove(temp_fp)
            except OSError:
                pass
    job["temp_files"] = []


def _sync_job_to_db(job_id: str, retries: int = 3) -> None:
    """Persist final job state/result into SQLite with basic retry."""
    job = state.jobs.get(job_id)
    if not job:
        return

    recording_id = job.get("recording_id")
    if not recording_id:
        return

    retry_delay = 0.2
    for attempt in range(1, retries + 1):
        try:
            with new_session() as session:
                rec = session.get(Recording, recording_id)
                if not rec:
                    return

                rec.status = job.get("status", rec.status)
                result = job.get("result")
                if result:
                    rec.duration = result.get("duration")

                    existing = session.exec(
                        select(Transcript).where(Transcript.recording_id == recording_id)
                    ).first()

                    full_text = " ".join(s.get("text", "") for s in result.get("segments", []))
                    json_data = json.dumps(result)
                    now = time.time()

                    if existing:
                        existing.full_text = full_text
                        existing.json_data = json_data
                        existing.updated_at = now
                        session.add(existing)
                    else:
                        session.add(
                            Transcript(
                                recording_id=recording_id,
                                full_text=full_text,
                                json_data=json_data,
                            )
                        )

                session.add(rec)
                session.commit()
                return
        except (SQLAlchemyError, OSError, RuntimeError, TypeError, ValueError):
            if attempt == retries:
                logger.exception(
                    "Failed to sync job to database",
                    extra={"job_id": job_id},
                )
                return
            time.sleep(retry_delay * attempt)


def _handle_job_error(job_id: str, exc: Exception) -> None:
    """Centralized job error handling with traceback logging and DB sync."""
    job = state.jobs.get(job_id)
    if job is not None:
        job["error"] = str(exc)
    logger.exception("Job failed", extra={"job_id": job_id})
    _push_event(job_id, "error", -1, str(exc))
    _sync_job_to_db(job_id)
