"""Analysis endpoints."""

from core.analysis_jobs import create_analysis_job
from llm_providers import refusal_reason
from db import get_session
from fastapi import APIRouter, Depends, Form, HTTPException
from models import Analysis, Recording, Transcript
from settings import get_llm_settings
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

    # Refuse before queueing rather than failing the job later: a hosted
    # provider receives the whole transcript, which is the one thing this app
    # promises not to do unless asked.
    cfg = get_llm_settings()
    refusal = refusal_reason(cfg)
    if refusal:
        raise HTTPException(400, refusal)

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

    job_id, analysis_id = create_analysis_job(
        recording_id=recording_id,
        analysis_type=analysis_type,
        transcript_full_text=tr.full_text,
        filename=rec.filename,
        file_path=rec.file_path,
        target_language=target_language,
        custom_prompt=custom_prompt,
        output_language=output_language,
    )
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
            "auto_generated": bool(a.auto_generated),
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
        "auto_generated": bool(a.auto_generated),
    }


@router.delete("/api/recordings/{recording_id}/analyses/{analysis_id}")
def delete_analysis(recording_id: str, analysis_id: str, session: Session = Depends(get_session)) -> dict:
    a = session.get(Analysis, analysis_id)
    if not a or a.recording_id != recording_id:
        raise HTTPException(404, "Analysis not found")
    session.delete(a)
    session.commit()
    return {"ok": True}
