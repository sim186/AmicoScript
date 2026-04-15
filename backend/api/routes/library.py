"""Library, recording, and transcript editing endpoints."""

import json
import time
from pathlib import Path

from db import get_session
from exports import _format_json, _format_md, _format_srt, _format_txt
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from models import Analysis, Folder, Recording, RecordingTag, Tag, Transcript
from sqlmodel import Session, select

router = APIRouter()

AUDIO_MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


def _recording_with_tags(recording: Recording, session: Session) -> dict:
    links = session.exec(select(RecordingTag).where(RecordingTag.recording_id == recording.id)).all()
    tag_ids = [lnk.tag_id for lnk in links]
    tags = []
    if tag_ids:
        tags = [
            {"id": t.id, "name": t.name, "color_code": t.color_code}
            for t in session.exec(select(Tag).where(Tag.id.in_(tag_ids))).all()
        ]
    return {
        "id": recording.id,
        "filename": recording.filename,
        "file_path": recording.file_path,
        "duration": recording.duration,
        "folder_id": recording.folder_id,
        "status": recording.status,
        "created_at": recording.created_at,
        "transcription_options": json.loads(recording.transcription_options or "{}"),
        "tags": tags,
    }


@router.get("/api/library")
def get_library(
    folder_id: str = "",
    tag_id: str = "",
    status: str = "",
    sort: str = "created_at",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> list:
    stmt = select(Recording)
    if folder_id:
        stmt = stmt.where(Recording.folder_id == folder_id)
    if status:
        stmt = stmt.where(Recording.status == status)
    if tag_id:
        linked_ids = [r.recording_id for r in session.exec(select(RecordingTag).where(RecordingTag.tag_id == tag_id)).all()]
        stmt = stmt.where(Recording.id.in_(linked_ids))

    sort_col = {"filename": Recording.filename, "duration": Recording.duration}.get(sort, Recording.created_at)
    stmt = stmt.order_by(sort_col.asc() if order == "asc" else sort_col.desc()).offset(offset).limit(min(limit, 200))

    recordings = session.exec(stmt).all()
    return [_recording_with_tags(r, session) for r in recordings]


@router.get("/api/recordings/{recording_id}")
def get_recording(recording_id: str, session: Session = Depends(get_session)) -> dict:
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")
    return _recording_with_tags(rec, session)


@router.patch("/api/recordings/{recording_id}")
async def update_recording(
    recording_id: str,
    filename: str = Form(""),
    folder_id: str = Form("__unset__"),
    session: Session = Depends(get_session),
) -> dict:
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")
    if filename:
        rec.filename = filename
    if folder_id != "__unset__":
        rec.folder_id = folder_id or None
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return _recording_with_tags(rec, session)


@router.delete("/api/recordings/{recording_id}")
def delete_recording(recording_id: str, session: Session = Depends(get_session)) -> dict:
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")

    try:
        fp = Path(rec.file_path)
        if fp.exists():
            fp.unlink()
        if fp.parent.exists() and not any(fp.parent.iterdir()):
            fp.parent.rmdir()
    except OSError:
        pass

    for link in session.exec(select(RecordingTag).where(RecordingTag.recording_id == recording_id)).all():
        session.delete(link)
    for tr in session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).all():
        session.delete(tr)
    for an in session.exec(select(Analysis).where(Analysis.recording_id == recording_id)).all():
        session.delete(an)

    session.delete(rec)
    session.commit()
    return {"ok": True}


@router.get("/api/recordings/{recording_id}/audio")
def get_recording_audio(recording_id: str, session: Session = Depends(get_session)):
    from storage import get_recording_audio_path

    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")
    audio_path = get_recording_audio_path(recording_id, rec.file_path)
    if not audio_path.exists():
        raise HTTPException(404, "Audio file not found on disk")
    ext = audio_path.suffix.lower()
    return FileResponse(str(audio_path), media_type=AUDIO_MEDIA_TYPES.get(ext, "audio/mpeg"))


@router.get("/api/recordings/{recording_id}/transcript")
def get_recording_transcript(recording_id: str, session: Session = Depends(get_session)) -> dict:
    tr = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    if not tr:
        raise HTTPException(404, "Transcript not found")
    return {
        "id": tr.id,
        "recording_id": tr.recording_id,
        "full_text": tr.full_text,
        "json_data": json.loads(tr.json_data),
        "created_at": tr.created_at,
        "updated_at": tr.updated_at,
    }


@router.get("/api/recordings/{recording_id}/export/{fmt}")
def export_recording(recording_id: str, fmt: str, session: Session = Depends(get_session)):
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")
    tr = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    if not tr:
        raise HTTPException(404, "Transcript not found")

    result = json.loads(tr.json_data)
    filename = Path(rec.filename).stem

    formatters = {
        "json": (_format_json, "application/json", "json"),
        "srt": (_format_srt, "text/plain", "srt"),
        "txt": (_format_txt, "text/plain", "txt"),
        "md": (_format_md, "text/markdown", "md"),
    }
    if fmt not in formatters:
        raise HTTPException(400, f"Unknown format: {fmt}. Use json, srt, txt, or md.")

    fn, media_type, ext = formatters[fmt]
    content = fn(result)
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}.{ext}"'},
    )


@router.patch("/api/recordings/{recording_id}/transcript/segments/{segment_index}")
async def edit_segment(recording_id: str, segment_index: int, text: str = Form(...), session: Session = Depends(get_session)) -> dict:
    tr = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    if not tr:
        raise HTTPException(404, "Transcript not found")

    data = json.loads(tr.json_data)
    segments = data.get("segments", [])
    if segment_index < 0 or segment_index >= len(segments):
        raise HTTPException(400, f"Segment index {segment_index} out of range")

    seg = segments[segment_index]
    if "original_text" not in seg:
        seg["original_text"] = seg.get("text", "")

    seg["text"] = text
    seg["edited"] = True

    data["segments"] = segments
    tr.json_data = json.dumps(data)
    tr.full_text = " ".join(s.get("text", "") for s in segments)
    tr.updated_at = time.time()

    session.add(tr)
    session.commit()
    return {"ok": True, "segment_index": segment_index}


@router.post("/api/recordings/{recording_id}/transcript/segments/{segment_index}/reset")
async def reset_segment(recording_id: str, segment_index: int, session: Session = Depends(get_session)) -> dict:
    tr = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    if not tr:
        raise HTTPException(404, "Transcript not found")

    data = json.loads(tr.json_data)
    segments = data.get("segments", [])
    if segment_index < 0 or segment_index >= len(segments):
        raise HTTPException(400, f"Segment index {segment_index} out of range")

    seg = segments[segment_index]
    if "original_text" in seg:
        seg["text"] = seg["original_text"]
        seg["edited"] = False

    data["segments"] = segments
    tr.json_data = json.dumps(data)
    tr.full_text = " ".join(s.get("text", "") for s in segments)
    tr.updated_at = time.time()

    session.add(tr)
    session.commit()
    return {"ok": True, "segment_index": segment_index, "text": seg["text"]}


@router.post("/api/recordings/{recording_id}/transcript/rename-speaker")
async def rename_recording_speaker(
    recording_id: str,
    old_name: str = Form(...),
    new_name: str = Form(...),
    session: Session = Depends(get_session),
) -> dict:
    tr = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    if not tr:
        raise HTTPException(404, "Transcript not found")

    data = json.loads(tr.json_data)
    if old_name in data.get("speakers", []):
        idx = data["speakers"].index(old_name)
        data["speakers"][idx] = new_name
        data["speakers"] = sorted(list(set(data["speakers"])))
    for seg in data.get("segments", []):
        if seg.get("speaker") == old_name:
            seg["speaker"] = new_name

    tr.json_data = json.dumps(data)
    tr.updated_at = time.time()
    session.add(tr)
    session.commit()
    return {"ok": True, "new_name": new_name}
