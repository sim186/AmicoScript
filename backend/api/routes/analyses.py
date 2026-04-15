"""Analysis endpoints."""

import asyncio
import threading
import time
import uuid

import state
from db import get_session
from fastapi import APIRouter, Depends, Form, HTTPException
from models import Analysis, Recording, Transcript
from settings import _get_llm_settings
from sqlmodel import Session, select

router = APIRouter()


@router.post("/api/recordings/{recording_id}/analyses")
async def create_analysis(
    recording_id: str,
    analysis_type: str = Form(...),
    target_language: str = Form(""),
    custom_prompt: str = Form(""),
    output_language: str = Form(""),
    session: Session = Depends(get_session),
) -> dict:
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")
    tr = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    if not tr:
        raise HTTPException(404, "Transcript not found — complete transcription first")

    cfg = _get_llm_settings()
    if not cfg["llm_model_name"]:
        raise HTTPException(400, "No LLM model configured. Set it in AI Analysis settings.")

    analysis_type = analysis_type.strip()
    target_language = target_language.strip()
    custom_prompt = custom_prompt.strip()
    output_language = output_language.strip()

    supported_analysis_types = {"summary", "action_items", "translate", "custom"}
    if analysis_type not in supported_analysis_types:
        raise HTTPException(400, "Invalid analysis_type. Supported values are: summary, action_items, translate, custom.")
    if analysis_type == "custom" and not custom_prompt:
        raise HTTPException(400, "custom_prompt is required when analysis_type is 'custom'.")
    if analysis_type == "translate" and not target_language:
        raise HTTPException(400, "target_language is required when analysis_type is 'translate'.")

    analysis_id = str(uuid.uuid4())
    analysis = Analysis(
        id=analysis_id,
        recording_id=recording_id,
        analysis_type=analysis_type,
        target_language=target_language or None,
        model_name=cfg["llm_model_name"],
        llm_base_url=cfg["llm_base_url"],
        status="pending",
    )
    session.add(analysis)
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
        "file_path": rec.file_path,
        "original_filename": rec.filename,
        "options": {
            "analysis_type": analysis_type,
            "target_language": target_language,
            "custom_prompt": custom_prompt,
            "output_language": output_language,
            "transcript_full_text": tr.full_text,
            **cfg,
        },
        "result": None,
        "error": None,
        "created_at": time.time(),
        "sse_queue": asyncio.Queue(),
        "event_loop": asyncio.get_running_loop(),
        "cancel_flag": threading.Event(),
        "logs": [],
        "temp_files": [],
    }
    state.JOB_QUEUE.put_nowait(job_id)
    return {"job_id": job_id, "analysis_id": analysis_id}


@router.get("/api/recordings/{recording_id}/analyses")
def list_analyses(recording_id: str, session: Session = Depends(get_session)) -> list:
    rows = session.exec(
        select(Analysis).where(Analysis.recording_id == recording_id).order_by(Analysis.created_at.desc())
    ).all()
    return [
        {
            "id": a.id,
            "analysis_type": a.analysis_type,
            "result_text": a.result_text,
            "target_language": a.target_language,
            "model_name": a.model_name,
            "status": a.status,
            "created_at": a.created_at,
        }
        for a in rows
    ]


@router.get("/api/recordings/{recording_id}/analyses/{analysis_id}")
def get_analysis(recording_id: str, analysis_id: str, session: Session = Depends(get_session)) -> dict:
    a = session.get(Analysis, analysis_id)
    if not a or a.recording_id != recording_id:
        raise HTTPException(404, "Analysis not found")
    return {
        "id": a.id,
        "analysis_type": a.analysis_type,
        "result_text": a.result_text,
        "target_language": a.target_language,
        "model_name": a.model_name,
        "status": a.status,
        "created_at": a.created_at,
    }


@router.delete("/api/recordings/{recording_id}/analyses/{analysis_id}")
def delete_analysis(recording_id: str, analysis_id: str, session: Session = Depends(get_session)) -> dict:
    a = session.get(Analysis, analysis_id)
    if not a or a.recording_id != recording_id:
        raise HTTPException(404, "Analysis not found")
    session.delete(a)
    session.commit()
    return {"ok": True}
