"""Creating analysis jobs — from the API, and automatically after a meeting.

Both entry points need the same thing: an Analysis row, a job dict shaped like
every other job, and a slot on the shared queue. The only real difference is
that the API runs on the event loop while the auto-summary hook runs on the
transcription worker thread, so enqueueing has to be done thread-safely.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid

import state
from db import new_session
from llm_providers import refusal_reason
from models import Analysis, Recording, Transcript
from settings import _get_auto_summarize_meetings, _get_llm_settings
from sqlmodel import select
from utils.logging_utils import get_logger

logger = get_logger("amicoscript.analysis")


def create_analysis_job(
    *,
    recording_id: str,
    analysis_type: str,
    transcript_full_text: str,
    filename: str = "",
    file_path: str = "",
    target_language: str = "",
    custom_prompt: str = "",
    output_language: str = "",
    auto_generated: bool = False,
    enqueue: bool = True,
) -> tuple[str, str]:
    """Create the Analysis row + job. Returns (job_id, analysis_id).

    ``enqueue=False`` leaves the job on the shelf for the caller to submit —
    used by the worker thread, which cannot touch the loop's queue directly.
    """
    cfg = _get_llm_settings()
    analysis_id = str(uuid.uuid4())

    with new_session() as session:
        session.add(
            Analysis(
                id=analysis_id,
                recording_id=recording_id,
                analysis_type=analysis_type,
                target_language=target_language or None,
                model_name=cfg["llm_model_name"],
                llm_base_url=cfg["llm_base_url"],
                status="pending",
                auto_generated=auto_generated,
            )
        )
        session.commit()

    job_id = str(uuid.uuid4())
    state.jobs[job_id] = {
        "id": job_id,
        "type": "analysis",
        "recording_id": recording_id,
        "analysis_id": analysis_id,
        "status": "queued",
        "progress": 0.0,
        "message": "Queued",
        "file_path": file_path,
        "original_filename": filename,
        "options": {
            "analysis_type": analysis_type,
            "target_language": target_language,
            "custom_prompt": custom_prompt,
            "output_language": output_language,
            "transcript_full_text": transcript_full_text,
            **cfg,
        },
        "result": None,
        "error": None,
        "created_at": time.time(),
        "sse_queue": asyncio.Queue(),
        "event_loop": state.event_loop,
        "cancel_flag": threading.Event(),
        "logs": [],
        "temp_files": [],
        "auto_generated": auto_generated,
    }

    if enqueue:
        state.JOB_QUEUE.put_nowait(job_id)
    return job_id, analysis_id


def _enqueue_from_worker_thread(job_id: str) -> bool:
    """Put *job_id* on the queue from a non-loop thread."""
    loop = state.event_loop
    if loop is None or not loop.is_running():
        return False
    loop.call_soon_threadsafe(state.JOB_QUEUE.put_nowait, job_id)
    return True


def maybe_queue_auto_summary(recording_id: str) -> str:
    """Summarise a finished meeting capture, if the user asked for that.

    Called from the transcription worker once a transcript exists. Returns the
    job id, or "" when nothing was queued. Never raises: a failure to summarise
    must not fail the transcription that just succeeded.
    """
    try:
        if not _get_auto_summarize_meetings():
            return ""

        cfg = _get_llm_settings()
        refusal = refusal_reason(cfg)
        if refusal:
            logger.info("Auto-summary skipped: %s", refusal)
            return ""

        with new_session() as session:
            rec = session.get(Recording, recording_id)
            if rec is None or rec.source != "meeting":
                return ""
            transcript = session.exec(
                select(Transcript).where(Transcript.recording_id == recording_id)
            ).first()
            if transcript is None or not transcript.full_text.strip():
                return ""
            # Don't pile up summaries if this recording is transcribed twice.
            existing = session.exec(
                select(Analysis)
                .where(Analysis.recording_id == recording_id)
                .where(Analysis.analysis_type == "summary")
                .where(Analysis.auto_generated == True)  # noqa: E712 — SQL comparison
            ).first()
            if existing is not None:
                return ""
            filename, file_path = rec.filename, rec.file_path
            full_text = transcript.full_text

        job_id, _ = create_analysis_job(
            recording_id=recording_id,
            analysis_type="summary",
            transcript_full_text=full_text,
            filename=filename,
            file_path=file_path,
            auto_generated=True,
            enqueue=False,
        )
        if not _enqueue_from_worker_thread(job_id):
            state.jobs.pop(job_id, None)
            logger.warning("Auto-summary skipped: event loop unavailable")
            return ""
        logger.info("Queued automatic summary for meeting %s", recording_id)
        return job_id
    except Exception:
        logger.exception("Auto-summary could not be queued")
        return ""
