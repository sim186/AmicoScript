"""Library, recording, and transcript editing endpoints."""

import datetime
import json
import time
from pathlib import Path

from http_utils import content_disposition_attachment

from db import get_session, new_session

from exports import format_md_bulk, render_export
from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import BaseModel
from fastapi.responses import FileResponse, StreamingResponse
from models import Analysis, Folder, Recording, RecordingTag, Tag, Transcript, TranscriptChunk
from sqlmodel import Session, select
from utils.logging_utils import get_logger

router = APIRouter()

_index_logger = get_logger("amicoscript.library")

AUDIO_MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


def _reindex_for_chat(recording_id: str, session: Session) -> None:
    """Rebuild this recording's chat chunks after its transcript changed.

    Editing a segment or renaming a speaker rewrites the transcript, and a
    stale chunk would keep the old wording findable — and quotable in an
    answer. Never fatal: the edit itself has already been saved.
    """
    from core.library_index import index_recording

    try:
        index_recording(recording_id, session)
    except Exception:
        _index_logger.exception("Could not reindex %s for library chat", recording_id)


def _export_meta(recording: Recording, session: Session) -> dict:
    """The facts a Markdown export needs that are not in the transcript itself.

    Tags and folder live on the recording, and the model name is buried inside
    the stored ``transcription_options`` JSON.
    """
    links = session.exec(
        select(RecordingTag).where(RecordingTag.recording_id == recording.id)
    ).all()
    tag_ids = [lnk.tag_id for lnk in links]
    tags = []
    if tag_ids:
        tags = [t.name for t in session.exec(select(Tag).where(Tag.id.in_(tag_ids))).all()]

    folder = ""
    if recording.folder_id:
        found = session.get(Folder, recording.folder_id)
        folder = found.name if found else ""

    model = ""
    if recording.transcription_options:
        try:
            opts = json.loads(recording.transcription_options)
            if isinstance(opts, dict):
                model = str(opts.get("model") or "")
        except (json.JSONDecodeError, ValueError):
            model = ""

    return {
        "tags": sorted(tags),
        "folder": folder,
        "source": recording.source or "",
        "model": model,
        "duration": recording.duration,
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
        "alias": recording.alias,
        "file_path": recording.file_path,
        "duration": recording.duration,
        "folder_id": recording.folder_id,
        "status": recording.status,
        "status_detail": recording.status_detail,
        "source": recording.source,
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
    safe_limit = max(1, min(limit, 200))
    stmt = stmt.order_by(sort_col.asc() if order == "asc" else sort_col.desc()).offset(offset).limit(safe_limit)

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
    alias: str = Form("__unset__"),
    folder_id: str = Form("__unset__"),
    session: Session = Depends(get_session),
) -> dict:
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")
    if filename:
        rec.filename = filename
    if alias != "__unset__":
        rec.alias = alias.strip() or None
    if folder_id != "__unset__":
        rec.folder_id = folder_id or None
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return _recording_with_tags(rec, session)


@router.delete("/api/recordings/{recording_id}")
def delete_recording(recording_id: str, session: Session = Depends(get_session)) -> dict:
    import state as _state
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")

    # Refuse if an active job is processing this recording
    for job in _state.jobs.values():
        if job.get("recording_id") == recording_id and job.get("status") in (
            "queued", "transcribing", "diarizing", "loading_model", "translating"
        ):
            raise HTTPException(409, "Recording is currently being processed; cancel the job first")

    file_path_to_delete = rec.file_path

    for link in session.exec(select(RecordingTag).where(RecordingTag.recording_id == recording_id)).all():
        session.delete(link)
    for tr in session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).all():
        session.delete(tr)
    for an in session.exec(select(Analysis).where(Analysis.recording_id == recording_id)).all():
        session.delete(an)
    # A leftover chunk would be retrievable, and an answer could cite a
    # recording that no longer opens.
    for chunk in session.exec(
        select(TranscriptChunk).where(TranscriptChunk.recording_id == recording_id)
    ).all():
        session.delete(chunk)

    session.delete(rec)
    session.commit()

    # Delete the file after successful DB commit
    try:
        fp = Path(file_path_to_delete)
        if fp.exists():
            fp.unlink()
        if fp.parent.exists() and not any(fp.parent.iterdir()):
            fp.parent.rmdir()
    except OSError:
        pass

    return {"ok": True}


@router.post("/api/recordings/{recording_id}/retry")
def retry_recording(recording_id: str) -> dict:
    """Transcribe an existing recording again.

    Until now a failed transcription was a dead end: the audio was still on
    disk, but the only way to try again was to delete the recording and
    re-import the file. This reuses the stored options, so a retry runs with the
    same model, language and diarization settings as the original attempt.
    """
    from core.job_status import RETRYABLE as RETRYABLE_STATUSES
    from core.jobs import submit
    from core.requeue import RequeueError, requeue_recording

    with new_session() as session:
        rec = session.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Recording not found")
        status = rec.status

    if status not in RETRYABLE_STATUSES:
        raise HTTPException(409, f"This recording is {status}; wait for it to finish first.")

    try:
        job_id = requeue_recording(recording_id)
    except RequeueError as exc:
        raise HTTPException(409, str(exc)) from exc

    submit(job_id)
    return {"ok": True, "job_id": job_id, "recording_id": recording_id}


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
def export_recording(
    recording_id: str,
    fmt: str,
    wikilinks: bool = False,
    session: Session = Depends(get_session),
):
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")
    tr = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    if not tr:
        raise HTTPException(404, "Transcript not found")

    try:
        result = json.loads(tr.json_data)
        if not isinstance(result, dict):
            raise ValueError("json_data is not an object")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(500, f"Transcript data is corrupt: {exc}") from exc

    filename = Path(rec.filename).stem
    title = rec.alias or filename
    date_str = datetime.datetime.fromtimestamp(rec.created_at).strftime("%Y-%m-%d")

    try:
        content, media_type, ext = render_export(
            fmt,
            result,
            title=title,
            date=date_str,
            meta=_export_meta(rec, session),
            wikilinks=wikilinks,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": content_disposition_attachment(f"{filename}.{ext}")},
    )


class BulkExportRequest(BaseModel):
    ids: list[str]
    wikilinks: bool = False


@router.post("/api/recordings/bulk-export/md")
def bulk_export_md(body: BulkExportRequest, session: Session = Depends(get_session)):
    recordings = []
    for rec_id in body.ids:
        rec = session.get(Recording, rec_id)
        if not rec:
            continue
        tr = session.exec(select(Transcript).where(Transcript.recording_id == rec_id)).first()
        if not tr:
            continue
        try:
            result = json.loads(tr.json_data)
            if not isinstance(result, dict):
                continue
        except (json.JSONDecodeError, ValueError):
            continue
        recordings.append({
            "title": rec.alias or Path(rec.filename).stem,
            "date": datetime.datetime.fromtimestamp(rec.created_at).strftime("%Y-%m-%d"),
            "result": result,
            "meta": _export_meta(rec, session),
        })
    if not recordings:
        raise HTTPException(404, "No valid transcripts found for provided IDs")
    content = format_md_bulk(recordings, wikilinks=body.wikilinks)
    filename = "transcripts" if len(recordings) > 1 else recordings[0]["title"]
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="text/markdown",
        headers={"Content-Disposition": content_disposition_attachment(f"{filename}.md")},
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
    _reindex_for_chat(recording_id, session)
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
    _reindex_for_chat(recording_id, session)
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
    _reindex_for_chat(recording_id, session)
    return {"ok": True, "new_name": new_name}


@router.post("/api/recordings/{recording_id}/transcript/assign-speaker")
async def assign_speaker(
    recording_id: str,
    segment_indices: str = Form(...),
    speaker_name: str = Form(...),
    session: Session = Depends(get_session),
) -> dict:
    tr = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    if not tr:
        raise HTTPException(404, "Transcript not found")

    data = json.loads(tr.json_data)
    segments = data.get("segments", [])

    indices = []
    for part in segment_indices.split(","):
        part = part.strip()
        if part:
            try:
                idx = int(part)
                if 0 <= idx < len(segments):
                    indices.append(idx)
            except ValueError:
                continue

    if not indices:
        raise HTTPException(400, "No valid segment indices provided")

    speaker_name = speaker_name.strip()
    if not speaker_name:
        raise HTTPException(400, "Speaker name is required")

    for idx in indices:
        segments[idx]["speaker"] = speaker_name

    speakers = data.get("speakers", [])
    if speaker_name not in speakers:
        speakers.append(speaker_name)
        speakers.sort()
    data["speakers"] = speakers

    data["segments"] = segments
    tr.json_data = json.dumps(data)
    tr.full_text = " ".join(s.get("text", "") for s in segments)
    tr.updated_at = time.time()

    session.add(tr)
    session.commit()
    _reindex_for_chat(recording_id, session)
    return {"ok": True, "speakers": speakers}
