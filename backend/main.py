"""AmicoScript — FastAPI application entry point.

This module is intentionally thin: it wires up the FastAPI app, registers
all API routes, and delegates heavy lifting to the focused modules below.

  state.py    — shared mutable state (jobs dict, model cache, event loop)
  pipeline.py — Whisper transcription + pyannote diarization worker
  exports.py  — transcript format functions (JSON, SRT, TXT, Markdown)
  settings.py — persistent settings I/O (~/.amicoscript/settings.json)
  shims.py    — torchcodec compatibility shim for PyInstaller / Docker ARM
  releases.py — GitHub release fetching utilities
"""
import asyncio
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

# Must be set before torch is imported anywhere (even transitively).
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# ---------------------------------------------------------------------------
# stdio safety — PyInstaller windowed mode sets stdio streams to None.
# Some bundled libraries (tqdm, torchaudio) call stream.write() directly.
# ---------------------------------------------------------------------------
_STDIO_FALLBACK_HANDLES = []


def _ensure_standard_streams() -> None:
    if sys.stdin is None:
        h = open(os.devnull, "r", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(h)
        sys.stdin = h
    if sys.stdout is None:
        h = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(h)
        sys.stdout = h
    if sys.stderr is None:
        h = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(h)
        sys.stderr = h


_ensure_standard_streams()

import aiofiles
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

import state
from db import get_session, init_db, new_session
from exports import _format_json, _format_srt, _format_txt, _format_md
from models import Folder, Recording, RecordingTag, Tag, Transcript
from pipeline import _append_job_log, _cleanup_job_temp_files, _push_event, _worker_loop
from releases import _fetch_latest_release, _is_version_newer
from settings import _get_saved_hf_token, _load_settings, _save_settings
from storage import get_recording_audio_path, ingest_file

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

if hasattr(sys, "_MEIPASS"):
    # Running inside a PyInstaller bundle
    BASE_DIR = Path(sys._MEIPASS)
    EXE_DIR = Path(sys.executable).parent
    UPLOAD_DIR = EXE_DIR / "uploads"
else:
    BASE_DIR = Path(__file__).parent
    UPLOAD_DIR = BASE_DIR / "uploads"

UPLOAD_DIR.mkdir(exist_ok=True)

if (BASE_DIR / "frontend").exists():
    FRONTEND_DIR = BASE_DIR / "frontend"
else:
    FRONTEND_DIR = BASE_DIR.parent / "frontend"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}

MODELS_META = [
    {"id": "tiny",     "name": "Tiny",     "params": "~39M",   "ram": "~1 GB",  "speed": 5, "accuracy": 1},
    {"id": "base",     "name": "Base",     "params": "~74M",   "ram": "~1 GB",  "speed": 4, "accuracy": 2},
    {"id": "small",    "name": "Small",    "params": "~244M",  "ram": "~2 GB",  "speed": 3, "accuracy": 3},
    {"id": "medium",   "name": "Medium",   "params": "~769M",  "ram": "~5 GB",  "speed": 2, "accuracy": 4},
    {"id": "large-v2", "name": "Large v2", "params": "~1.5B",  "ram": "~10 GB", "speed": 1, "accuracy": 5},
    {"id": "large-v3", "name": "Large v3", "params": "~1.5B",  "ram": "~10 GB", "speed": 1, "accuracy": 5},
]

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="AmicoScript")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Start the single background worker thread (sequential job processing).
threading.Thread(target=_worker_loop, daemon=True).start()


@app.on_event("startup")
async def _startup() -> None:
    state.event_loop = asyncio.get_event_loop()
    init_db()
    _recover_interrupted_jobs()
    asyncio.create_task(_cleanup_loop())
    try:
        asyncio.create_task(_release_poller_loop())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Job recovery
# ---------------------------------------------------------------------------

def _recover_interrupted_jobs() -> None:
    """Mark any DB recordings that were in-flight as error (interrupted by restart)."""
    from sqlalchemy import text as _text
    try:
        with new_session() as session:
            interrupted = session.exec(
                select(Recording).where(
                    Recording.status.in_(["queued", "transcribing", "diarizing"])
                )
            ).all()
            for rec in interrupted:
                rec.status = "error"
                session.add(rec)
            session.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Release poller
# ---------------------------------------------------------------------------

def _get_local_version() -> str:
    try:
        return get_version().get("version", "") or ""
    except Exception:
        return ""


async def _release_poller_loop() -> None:
    owner = os.environ.get("GITHUB_OWNER", "")
    repo = os.environ.get("GITHUB_REPO", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not owner or not repo:
        return

    app.state.latest_release = {"tag_name": "", "html_url": "", "name": "", "body": ""}

    while True:
        try:
            info = _fetch_latest_release(owner, repo, token or None)
            if info and not info.get("error"):
                tag = info.get("tag_name", "")
                app.state.latest_release = {
                    "tag_name": tag,
                    "html_url": info.get("html_url", ""),
                    "name": info.get("name", ""),
                    "body": info.get("body", ""),
                }
                local = _get_local_version()
                try:
                    app.state.update_available = _is_version_newer(local, tag)
                    app.state.local_version = local
                except Exception:
                    app.state.update_available = False
            else:
                app.state.latest_release = {"error": info.get("error", "unknown")}
        except Exception:
            pass
        await asyncio.sleep(60 * 60 * 4)


# ---------------------------------------------------------------------------
# Cleanup loop
# ---------------------------------------------------------------------------

async def _cleanup_loop() -> None:
    from config import STORAGE_ROOT
    while True:
        await asyncio.sleep(3600)
        cutoff = time.time() - 3600
        for job_id in list(state.jobs.keys()):
            job = state.jobs[job_id]
            if job.get("created_at", 0) < cutoff:
                fp = job.get("file_path", "")
                if fp and os.path.exists(fp):
                    # Don't delete files that have been moved to managed storage.
                    try:
                        if not Path(fp).is_relative_to(STORAGE_ROOT):
                            os.remove(fp)
                    except OSError:
                        pass
                _cleanup_job_temp_files(job)
                state.jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_job(job_id: str) -> dict:
    job = state.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ---------------------------------------------------------------------------
# API: settings
# ---------------------------------------------------------------------------

@app.get("/api/settings")
def get_settings() -> dict:
    settings = _load_settings()
    return {"hf_token": settings.get("hf_token", "")}


@app.post("/api/settings")
async def save_settings(hf_token: str = Form("")) -> dict:
    settings = _load_settings()
    settings["hf_token"] = hf_token
    _save_settings(settings)
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: version / models / release
# ---------------------------------------------------------------------------

@app.get("/api/version")
def get_version() -> dict:
    try:
        candidate = BASE_DIR / ".." / "VERSION"
        candidate = candidate.resolve()
        if not candidate.exists():
            candidate = BASE_DIR / "VERSION"
        if not candidate.exists():
            candidate = Path(__file__).resolve().parents[2] / "VERSION"
        ver = candidate.read_text(encoding="utf-8").strip() if candidate.exists() else ""
    except Exception:
        ver = ""
    return {"version": ver}


@app.get("/api/models")
def get_models() -> list:
    return MODELS_META


@app.get("/api/latest-release")
def api_latest_release() -> dict:
    info = getattr(app.state, "latest_release", {}) or {}
    update = getattr(app.state, "update_available", False)
    local = getattr(app.state, "local_version", _get_local_version())
    return {"latest": info, "update_available": bool(update), "local_version": local}


# ---------------------------------------------------------------------------
# API: transcription jobs
# ---------------------------------------------------------------------------

@app.post("/api/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form("small"),
    language: str = Form(""),
    diarize: str = Form("false"),
    hf_token: str = Form(""),
    num_speakers: str = Form(""),
    min_speakers: str = Form(""),
    max_speakers: str = Form(""),
    folder_id: str = Form(""),
) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported file type: {ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    job_id = str(uuid.uuid4())
    staging = UPLOAD_DIR / f"{job_id}{ext}"

    async with aiofiles.open(staging, "wb") as f:
        await f.write(await file.read())

    # Create a Recording row and move the file to permanent managed storage.
    recording_id = str(uuid.uuid4())
    permanent_path = ingest_file(staging, recording_id)

    opts_dict = {
        "model": model,
        "language": language,
        "diarize": diarize.lower() == "true",
        "num_speakers": int(num_speakers) if num_speakers.isdigit() else None,
        "min_speakers": int(min_speakers) if min_speakers.isdigit() else None,
        "max_speakers": int(max_speakers) if max_speakers.isdigit() else None,
    }

    try:
        with new_session() as session:
            recording = Recording(
                id=recording_id,
                filename=file.filename or "audio",
                file_path=str(permanent_path),
                folder_id=folder_id or None,
                status="queued",
                transcription_options=json.dumps(opts_dict),
            )
            session.add(recording)
            session.commit()
    except Exception:
        pass  # DB failure must not block transcription

    job: dict = {
        "id": job_id,
        "recording_id": recording_id,
        "status": "queued",
        "progress": 0.0,
        "message": "Queued",
        "file_path": str(permanent_path),
        "original_filename": file.filename or "audio",
        "options": {
            **opts_dict,
            "hf_token": hf_token or _get_saved_hf_token(),
        },
        "result": None,
        "error": None,
        "created_at": time.time(),
        "sse_queue": asyncio.Queue(),
        "cancel_flag": threading.Event(),
        "logs": [],
        "temp_files": [],
    }
    state.jobs[job_id] = job
    _append_job_log(job_id, "INFO", f"Job created for file '{job['original_filename']}'")
    state.JOB_QUEUE.put(job_id)
    return {"job_id": job_id, "recording_id": recording_id}


@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    _get_job(job_id)

    async def event_generator():
        q = state.jobs[job_id]["sse_queue"]
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield {"data": json.dumps(event)}
                if event["status"] in ("done", "error", "cancelled"):
                    break
            except asyncio.TimeoutError:
                yield {"data": json.dumps({"heartbeat": True})}

    return EventSourceResponse(event_generator())


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    job = _get_job(job_id)
    job["cancel_flag"].set()
    return {"ok": True}


@app.get("/api/audio/{job_id}")
def get_audio(job_id: str):
    job = _get_job(job_id)
    fp = job.get("file_path", "")
    if not fp or not os.path.exists(fp):
        raise HTTPException(404, "Audio file not found (may have expired)")
    ext = Path(fp).suffix.lower()
    media_types = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }
    return FileResponse(fp, media_type=media_types.get(ext, "audio/mpeg"))


@app.get("/api/jobs/{job_id}/result")
def get_result(job_id: str) -> dict:
    job = _get_job(job_id)
    if job["status"] != "done":
        raise HTTPException(409, f"Job not complete (status: {job['status']})")
    return job["result"]


@app.get("/api/jobs/{job_id}/logs")
def get_job_logs(job_id: str, limit: int = 300) -> dict:
    job = _get_job(job_id)
    safe_limit = max(1, min(limit, 1000))
    logs = job.get("logs", [])
    return {
        "status": job.get("status"),
        "progress": job.get("progress"),
        "message": job.get("message"),
        "logs": logs[-safe_limit:],
    }


@app.post("/api/jobs/{job_id}/rename-speaker")
async def rename_speaker(
    job_id: str,
    old_name: str = Form(...),
    new_name: str = Form(...),
) -> dict:
    job = _get_job(job_id)
    if job["status"] != "done":
        raise HTTPException(409, "Job not complete")
    result = job["result"]
    if not result:
        raise HTTPException(404, "Result not found")

    if old_name in result["speakers"]:
        idx = result["speakers"].index(old_name)
        result["speakers"][idx] = new_name
        result["speakers"] = sorted(list(set(result["speakers"])))

    for seg in result["segments"]:
        if seg["speaker"] == old_name:
            seg["speaker"] = new_name

    return {"ok": True, "new_name": new_name}


@app.get("/api/jobs/{job_id}/export/{fmt}")
def export_job(job_id: str, fmt: str):
    job = _get_job(job_id)
    if job["status"] != "done":
        raise HTTPException(409, "Job not complete")
    result = job["result"]
    filename = Path(job["original_filename"]).stem

    formatters = {
        "json": (_format_json, "application/json", "json"),
        "srt":  (_format_srt,  "text/plain",       "srt"),
        "txt":  (_format_txt,  "text/plain",       "txt"),
        "md":   (_format_md,   "text/markdown",    "md"),
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


# ---------------------------------------------------------------------------
# API: library — recordings
# ---------------------------------------------------------------------------

AUDIO_MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


def _recording_with_tags(recording: Recording, session: Session) -> dict:
    """Build a serialisable dict for a Recording including its tags."""
    links = session.exec(
        select(RecordingTag).where(RecordingTag.recording_id == recording.id)
    ).all()
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


@app.get("/api/library")
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
        linked_ids = [
            r.recording_id
            for r in session.exec(
                select(RecordingTag).where(RecordingTag.tag_id == tag_id)
            ).all()
        ]
        stmt = stmt.where(Recording.id.in_(linked_ids))

    sort_col = {
        "filename": Recording.filename,
        "duration": Recording.duration,
    }.get(sort, Recording.created_at)

    stmt = stmt.order_by(
        sort_col.asc() if order == "asc" else sort_col.desc()
    ).offset(offset).limit(min(limit, 200))

    recordings = session.exec(stmt).all()
    return [_recording_with_tags(r, session) for r in recordings]


@app.get("/api/recordings/{recording_id}")
def get_recording(recording_id: str, session: Session = Depends(get_session)) -> dict:
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")
    return _recording_with_tags(rec, session)


@app.patch("/api/recordings/{recording_id}")
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


@app.delete("/api/recordings/{recording_id}")
def delete_recording(recording_id: str, session: Session = Depends(get_session)) -> dict:
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")

    # Remove physical file.
    try:
        fp = Path(rec.file_path)
        if fp.exists():
            fp.unlink()
        # Remove the per-recording directory if now empty.
        if fp.parent.exists() and not any(fp.parent.iterdir()):
            fp.parent.rmdir()
    except OSError:
        pass

    # Cascade: delete RecordingTag links and Transcript rows.
    for link in session.exec(
        select(RecordingTag).where(RecordingTag.recording_id == recording_id)
    ).all():
        session.delete(link)
    for tr in session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).all():
        session.delete(tr)

    session.delete(rec)
    session.commit()
    return {"ok": True}


@app.get("/api/recordings/{recording_id}/audio")
def get_recording_audio(recording_id: str, session: Session = Depends(get_session)):
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")
    audio_path = get_recording_audio_path(recording_id, rec.file_path)
    if not audio_path.exists():
        raise HTTPException(404, "Audio file not found on disk")
    ext = audio_path.suffix.lower()
    return FileResponse(str(audio_path), media_type=AUDIO_MEDIA_TYPES.get(ext, "audio/mpeg"))


@app.get("/api/recordings/{recording_id}/transcript")
def get_recording_transcript(
    recording_id: str, session: Session = Depends(get_session)
) -> dict:
    tr = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).first()
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


@app.get("/api/recordings/{recording_id}/export/{fmt}")
def export_recording(
    recording_id: str, fmt: str, session: Session = Depends(get_session)
):
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")
    tr = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).first()
    if not tr:
        raise HTTPException(404, "Transcript not found")

    result = json.loads(tr.json_data)
    filename = Path(rec.filename).stem

    formatters = {
        "json": (_format_json, "application/json", "json"),
        "srt":  (_format_srt,  "text/plain",       "srt"),
        "txt":  (_format_txt,  "text/plain",       "txt"),
        "md":   (_format_md,   "text/markdown",    "md"),
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


# ---------------------------------------------------------------------------
# API: segment editing
# ---------------------------------------------------------------------------

@app.patch("/api/recordings/{recording_id}/transcript/segments/{segment_index}")
async def edit_segment(
    recording_id: str,
    segment_index: int,
    text: str = Form(...),
    session: Session = Depends(get_session),
) -> dict:
    tr = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).first()
    if not tr:
        raise HTTPException(404, "Transcript not found")

    data = json.loads(tr.json_data)
    segments = data.get("segments", [])
    if segment_index < 0 or segment_index >= len(segments):
        raise HTTPException(400, f"Segment index {segment_index} out of range")

    segments[segment_index]["text"] = text
    segments[segment_index]["edited"] = True

    data["segments"] = segments
    tr.json_data = json.dumps(data)
    tr.full_text = " ".join(s.get("text", "") for s in segments)
    tr.updated_at = time.time()

    session.add(tr)
    session.commit()
    return {"ok": True, "segment_index": segment_index}


@app.post("/api/recordings/{recording_id}/transcript/rename-speaker")
async def rename_recording_speaker(
    recording_id: str,
    old_name: str = Form(...),
    new_name: str = Form(...),
    session: Session = Depends(get_session),
) -> dict:
    tr = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).first()
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


# ---------------------------------------------------------------------------
# API: folders
# ---------------------------------------------------------------------------

@app.get("/api/folders")
def list_folders(session: Session = Depends(get_session)) -> list:
    folders = session.exec(select(Folder)).all()
    return [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id,
            "created_at": f.created_at,
        }
        for f in folders
    ]


@app.post("/api/folders")
async def create_folder(
    name: str = Form(...),
    parent_id: str = Form(""),
    session: Session = Depends(get_session),
) -> dict:
    folder = Folder(name=name, parent_id=parent_id or None)
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "created_at": folder.created_at,
    }


@app.patch("/api/folders/{folder_id}")
async def update_folder(
    folder_id: str,
    name: str = Form(""),
    parent_id: str = Form("__unset__"),
    session: Session = Depends(get_session),
) -> dict:
    folder = session.get(Folder, folder_id)
    if not folder:
        raise HTTPException(404, "Folder not found")
    if name:
        folder.name = name
    if parent_id != "__unset__":
        folder.parent_id = parent_id or None
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "created_at": folder.created_at,
    }


@app.delete("/api/folders/{folder_id}")
def delete_folder(
    folder_id: str,
    delete_recordings: bool = False,
    session: Session = Depends(get_session),
) -> dict:
    folder = session.get(Folder, folder_id)
    if not folder:
        raise HTTPException(404, "Folder not found")

    recordings_in_folder = session.exec(
        select(Recording).where(Recording.folder_id == folder_id)
    ).all()

    if delete_recordings:
        for rec in recordings_in_folder:
            # Reuse delete logic inline.
            try:
                fp = Path(rec.file_path)
                if fp.exists():
                    fp.unlink()
                if fp.parent.exists() and not any(fp.parent.iterdir()):
                    fp.parent.rmdir()
            except OSError:
                pass
            for link in session.exec(
                select(RecordingTag).where(RecordingTag.recording_id == rec.id)
            ).all():
                session.delete(link)
            for tr in session.exec(
                select(Transcript).where(Transcript.recording_id == rec.id)
            ).all():
                session.delete(tr)
            session.delete(rec)
    else:
        for rec in recordings_in_folder:
            rec.folder_id = None
            session.add(rec)

    session.delete(folder)
    session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: tags
# ---------------------------------------------------------------------------

@app.get("/api/tags")
def list_tags(session: Session = Depends(get_session)) -> list:
    return [
        {"id": t.id, "name": t.name, "color_code": t.color_code}
        for t in session.exec(select(Tag)).all()
    ]


@app.post("/api/tags")
async def create_tag(
    name: str = Form(...),
    color_code: str = Form("#6c63ff"),
    session: Session = Depends(get_session),
) -> dict:
    tag = Tag(name=name, color_code=color_code)
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return {"id": tag.id, "name": tag.name, "color_code": tag.color_code}


@app.patch("/api/tags/{tag_id}")
async def update_tag(
    tag_id: str,
    name: str = Form(""),
    color_code: str = Form(""),
    session: Session = Depends(get_session),
) -> dict:
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(404, "Tag not found")
    if name:
        tag.name = name
    if color_code:
        tag.color_code = color_code
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return {"id": tag.id, "name": tag.name, "color_code": tag.color_code}


@app.delete("/api/tags/{tag_id}")
def delete_tag(tag_id: str, session: Session = Depends(get_session)) -> dict:
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(404, "Tag not found")
    for link in session.exec(
        select(RecordingTag).where(RecordingTag.tag_id == tag_id)
    ).all():
        session.delete(link)
    session.delete(tag)
    session.commit()
    return {"ok": True}


@app.post("/api/recordings/{recording_id}/tags/{tag_id}")
def add_recording_tag(
    recording_id: str, tag_id: str, session: Session = Depends(get_session)
) -> dict:
    if not session.get(Recording, recording_id):
        raise HTTPException(404, "Recording not found")
    if not session.get(Tag, tag_id):
        raise HTTPException(404, "Tag not found")
    existing = session.get(RecordingTag, (recording_id, tag_id))
    if not existing:
        session.add(RecordingTag(recording_id=recording_id, tag_id=tag_id))
        session.commit()
    return {"ok": True}


@app.delete("/api/recordings/{recording_id}/tags/{tag_id}")
def remove_recording_tag(
    recording_id: str, tag_id: str, session: Session = Depends(get_session)
) -> dict:
    link = session.get(RecordingTag, (recording_id, tag_id))
    if link:
        session.delete(link)
        session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: search
# ---------------------------------------------------------------------------

@app.get("/api/search")
def search_library(
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> list:
    if not q.strip():
        return []

    from sqlalchemy import text as _text
    from sqlalchemy.exc import OperationalError

    safe_limit = min(limit, 100)

    try:
        rows = session.exec(
            _text("""
                SELECT t.recording_id,
                       snippet(transcript_fts, 0, '<mark>', '</mark>', '…', 20) AS snippet
                FROM transcript_fts
                JOIN transcript t ON transcript_fts.rowid = t.rowid
                WHERE transcript_fts MATCH :q
                ORDER BY rank
                LIMIT :lim OFFSET :off
            """),
            {"q": q, "lim": safe_limit, "off": offset},
        ).all()
    except OperationalError:
        # Malformed FTS query — fall back to plain LIKE.
        rows = session.exec(
            _text("""
                SELECT id AS recording_id,
                       substr(full_text, 1, 200) AS snippet
                FROM transcript
                WHERE full_text LIKE :q
                LIMIT :lim OFFSET :off
            """),
            {"q": f"%{q}%", "lim": safe_limit, "off": offset},
        ).all()

    results = []
    for row in rows:
        rec = session.get(Recording, row.recording_id)
        if rec:
            results.append({
                "recording_id": row.recording_id,
                "filename": rec.filename,
                "duration": rec.duration,
                "snippet": row.snippet,
            })
    return results


# ---------------------------------------------------------------------------
# API: exit (localhost-only graceful shutdown)
# ---------------------------------------------------------------------------

@app.post("/api/exit")
async def api_exit(request: Request):
    try:
        client_host = request.client.host if request.client else ""
    except Exception:
        client_host = ""

    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return {"status": "ignored"}

    def _delayed_exit():
        time.sleep(0.1)
        os._exit(0)

    threading.Thread(target=_delayed_exit, daemon=True).start()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Static frontend (must be last so /api routes take priority)
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    changelog_path = BASE_DIR / "CHANGELOG.md"
    if changelog_path.exists():
        @app.get("/CHANGELOG.md")
        async def _serve_changelog():
            return FileResponse(str(changelog_path))
