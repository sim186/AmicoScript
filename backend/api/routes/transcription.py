"""Transcription and job endpoints."""

import asyncio
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import aiofiles
import state
from core.job_helpers import _append_job_log
from core.source_downloader import DownloadCandidate, is_supported_source_url, resolve_source_candidates
from core.transcription_config import TranscriptionConfig
from db import get_session, new_session
from exports import _format_json, _format_md, _format_srt, _format_txt
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from models import Recording, RecordingTag, Tag, Transcript
from settings import _get_saved_hf_token
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool
from storage import ingest_file

router = APIRouter()

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".mov", ".mkv", ".opus"}

PLATFORM_TAG_COLORS = {
    "youtube": "#ff0000",
    "x": "#111111",
    "facebook": "#1877f2",
    "instagram": "#e1306c",
    "tiktok": "#25f4ee",
    "vimeo": "#1ab7ea",
    "twitch": "#9146ff",
}


def _get_job(job_id: str) -> dict:
    job = state.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _upload_dir() -> Path:
    from config import STORAGE_ROOT
    upload_dir = STORAGE_ROOT / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _to_bool(value: str, default: bool = False) -> bool:
    text = (value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _build_transcription_options(
    model: str,
    language: str,
    diarize: str,
    colab_url: str,
    num_speakers: str,
    min_speakers: str,
    max_speakers: str,
    compute_type: str,
    device: str,
    device_index: str,
    vad_filter: str,
    word_timestamps: str,
    beam_size: str,
    best_of: str,
    force_normalize_audio: str,
) -> dict[str, Any]:
    return TranscriptionConfig(
        model=model,
        language=language,
        diarize=_to_bool(diarize),
        colab_url=colab_url,
        num_speakers=int(num_speakers) if num_speakers.isdigit() else None,
        min_speakers=int(min_speakers) if min_speakers.isdigit() else None,
        max_speakers=int(max_speakers) if max_speakers.isdigit() else None,
        compute_type=(compute_type or "int8"),
        device=(device or "auto"),
        device_index=int(device_index) if str(device_index).isdigit() else 0,
        vad_filter=_to_bool(vad_filter, default=True),
        word_timestamps=_to_bool(word_timestamps),
        beam_size=int(beam_size) if str(beam_size).isdigit() else 5,
        best_of=int(best_of) if str(best_of).isdigit() else 5,
        force_normalize_audio=_to_bool(force_normalize_audio),
    ).model_dump()


def _create_recording_row(
    recording_id: str,
    filename: str,
    file_path: str,
    folder_id: str,
    opts_dict: dict[str, Any],
) -> None:
    try:
        with new_session() as session:
            recording = Recording(
                id=recording_id,
                filename=filename or "audio",
                file_path=file_path,
                folder_id=folder_id or None,
                status="queued",
                transcription_options=json.dumps(opts_dict),
            )
            session.add(recording)
            session.commit()
    except Exception:
        pass


def _create_job(
    *,
    job_id: str,
    recording_id: str,
    original_filename: str,
    file_path: str,
    opts_dict: dict[str, Any],
    hf_token: str,
    job_type: str = "transcribe",
    source_url: str = "",
    source_platform: str = "",
) -> None:
    state.jobs[job_id] = {
        "id": job_id,
        "type": job_type,
        "recording_id": recording_id,
        "status": "queued",
        "progress": 0.0,
        "message": "Queued",
        "file_path": file_path,
        "original_filename": original_filename,
        "options": {**opts_dict, "hf_token": hf_token or _get_saved_hf_token()},
        "source_url": source_url,
        "source_platform": source_platform,
        "result": None,
        "error": None,
        "created_at": time.time(),
        "sse_queue": asyncio.Queue(),
        "event_loop": asyncio.get_running_loop(),
        "cancel_flag": threading.Event(),
        "logs": [],
        "temp_files": [],
    }
    _append_job_log(job_id, "INFO", f"Job created for source '{original_filename}'")
    state.JOB_QUEUE.put_nowait(job_id)


def _ensure_recording_platform_tag(recording_id: str, platform: str) -> None:
    """Attach a platform tag (e.g., youtube, tiktok) to a recording when provided."""
    platform_name = (platform or "").strip().lower()
    if not platform_name or platform_name == "web":
        return

    try:
        with new_session() as session:
            desired_color = PLATFORM_TAG_COLORS.get(platform_name, "#60a5fa")
            tag = session.exec(select(Tag).where(Tag.name == platform_name)).first()
            if not tag:
                tag = Tag(name=platform_name, color_code=desired_color)
                session.add(tag)
                session.commit()
                session.refresh(tag)
            elif tag.color_code != desired_color and platform_name in PLATFORM_TAG_COLORS:
                tag.color_code = desired_color
                session.add(tag)
                session.commit()

            existing = session.get(RecordingTag, (recording_id, tag.id))
            if not existing:
                session.add(RecordingTag(recording_id=recording_id, tag_id=tag.id))
                session.commit()
    except Exception:
        # Tagging should not fail the transcription flow.
        pass


@router.post("/api/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form("small"),
    language: str = Form(""),
    diarize: str = Form("false"),
    colab_url: str = Form(""),
    hf_token: str = Form(""),
    num_speakers: str = Form(""),
    min_speakers: str = Form(""),
    max_speakers: str = Form(""),
    compute_type: str = Form("int8"),
    device: str = Form("auto"),
    device_index: str = Form("0"),
    vad_filter: str = Form("true"),
    word_timestamps: str = Form("false"),
    beam_size: str = Form("5"),
    best_of: str = Form("5"),
    force_normalize_audio: str = Form("false"),
    folder_id: str = Form(""),
) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    job_id = str(uuid.uuid4())
    staging = _upload_dir() / f"{job_id}{ext}"

    async with aiofiles.open(staging, "wb") as f:
        await f.write(await file.read())

    recording_id = str(uuid.uuid4())
    permanent_path = ingest_file(staging, recording_id)

    opts_dict = _build_transcription_options(
        model=model,
        language=language,
        diarize=diarize,
        colab_url=colab_url,
        num_speakers=num_speakers,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
        compute_type=compute_type,
        device=device,
        device_index=device_index,
        vad_filter=vad_filter,
        word_timestamps=word_timestamps,
        beam_size=beam_size,
        best_of=best_of,
        force_normalize_audio=force_normalize_audio,
    )

    _create_recording_row(
        recording_id=recording_id,
        filename=file.filename or "audio",
        file_path=str(permanent_path),
        folder_id=folder_id,
        opts_dict=opts_dict,
    )

    _create_job(
        job_id=job_id,
        recording_id=recording_id,
        original_filename=file.filename or "audio",
        file_path=str(permanent_path),
        opts_dict=opts_dict,
        hf_token=hf_token,
    )
    return {"job_id": job_id, "recording_id": recording_id}


@router.post("/api/transcribe/url")
async def transcribe_from_url(
    source_url: str = Form(...),
    allow_playlist: str = Form("true"),
    model: str = Form("small"),
    language: str = Form(""),
    diarize: str = Form("false"),
    colab_url: str = Form(""),
    hf_token: str = Form(""),
    num_speakers: str = Form(""),
    min_speakers: str = Form(""),
    max_speakers: str = Form(""),
    compute_type: str = Form("int8"),
    device: str = Form("auto"),
    device_index: str = Form("0"),
    vad_filter: str = Form("true"),
    word_timestamps: str = Form("false"),
    beam_size: str = Form("5"),
    best_of: str = Form("5"),
    force_normalize_audio: str = Form("false"),
    folder_id: str = Form(""),
) -> dict:
    normalized_url = (source_url or "").strip()
    if not normalized_url:
        raise HTTPException(400, "A source URL is required")
    if not is_supported_source_url(normalized_url):
        raise HTTPException(400, "Unsupported source URL. Please provide a valid http(s) URL.")

    include_playlist = _to_bool(allow_playlist, default=True)

    try:
        candidates: list[DownloadCandidate] = resolve_source_candidates(normalized_url, include_playlist=include_playlist)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Failed to inspect URL: {exc}") from exc

    if not candidates:
        raise HTTPException(400, "No downloadable entries found for this URL")

    opts_dict = _build_transcription_options(
        model=model,
        language=language,
        diarize=diarize,
        colab_url=colab_url,
        num_speakers=num_speakers,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
        compute_type=compute_type,
        device=device,
        device_index=device_index,
        vad_filter=vad_filter,
        word_timestamps=word_timestamps,
        beam_size=beam_size,
        best_of=best_of,
        force_normalize_audio=force_normalize_audio,
    )

    jobs: list[dict[str, str]] = []
    for candidate in candidates:
        recording_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        display_name = candidate.title or "Online audio"

        _create_recording_row(
            recording_id=recording_id,
            filename=display_name,
            file_path="",
            folder_id=folder_id,
            opts_dict=opts_dict,
        )
        _ensure_recording_platform_tag(recording_id, candidate.platform)

        _create_job(
            job_id=job_id,
            recording_id=recording_id,
            original_filename=display_name,
            file_path="",
            opts_dict=opts_dict,
            hf_token=hf_token,
            job_type="download_transcribe",
            source_url=candidate.url,
            source_platform=candidate.platform,
        )
        jobs.append(
            {
                "job_id": job_id,
                "recording_id": recording_id,
                "title": display_name,
                "source_url": candidate.url,
                "platform": candidate.platform,
            }
        )

    return {
        "count": len(jobs),
        "jobs": jobs,
        "first_job_id": jobs[0]["job_id"],
        "first_recording_id": jobs[0]["recording_id"],
    }


@router.get("/api/jobs/{job_id}/stream")
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


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    _get_job(job_id)["cancel_flag"].set()
    return {"ok": True}


@router.get("/api/audio/{job_id}")
def get_audio(job_id: str):
    job = _get_job(job_id)
    fp = job.get("file_path", "")
    if not fp or not os.path.exists(fp):
        raise HTTPException(404, "Audio file not found (may have expired)")
    ext = Path(fp).suffix.lower()
    media_types = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".flac": "audio/flac"}
    return FileResponse(fp, media_type=media_types.get(ext, "audio/mpeg"))


@router.get("/api/jobs/{job_id}/result")
def get_result(job_id: str) -> dict:
    job = _get_job(job_id)
    if job["status"] != "done":
        raise HTTPException(409, f"Job not complete (status: {job['status']})")
    return job["result"]


@router.get("/api/jobs/{job_id}/logs")
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


@router.post("/api/jobs/{job_id}/rename-speaker")
async def rename_speaker(job_id: str, old_name: str = Form(...), new_name: str = Form(...)) -> dict:
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


@router.get("/api/jobs/{job_id}/export/{fmt}")
def export_job(job_id: str, fmt: str):
    job = _get_job(job_id)
    if job["status"] != "done":
        raise HTTPException(409, "Job not complete")
    result = job["result"]
    filename = Path(job["original_filename"]).stem

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


@router.post("/api/recordings/{recording_id}/transcript/segments/{segment_index}/translate")
async def translate_segment_api(recording_id: str, segment_index: int, session: Session = Depends(get_session)) -> dict:
    from core.translation import _translate_audio_chunk

    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")

    tr = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    if not tr:
        raise HTTPException(404, "Transcript not found")

    data = json.loads(tr.json_data)
    segments = data.get("segments", [])
    if segment_index < 0 or segment_index >= len(segments):
        raise HTTPException(400, f"Segment index {segment_index} out of range")

    seg = segments[segment_index]
    opts = json.loads(rec.transcription_options or "{}")
    model_name = opts.get("model", "small")

    translated_text = await run_in_threadpool(_translate_audio_chunk, rec.file_path, seg["start"], seg["end"], model_name)

    seg["translation"] = translated_text
    data["segments"] = segments
    tr.json_data = json.dumps(data)
    tr.updated_at = time.time()
    session.add(tr)
    session.commit()
    return {"ok": True, "translation": translated_text}


@router.post("/api/recordings/{recording_id}/transcript/translate-all")
async def translate_all_api(recording_id: str, session: Session = Depends(get_session)) -> dict:
    rec = session.get(Recording, recording_id)
    if not rec:
        raise HTTPException(404, "Recording not found")

    opts = json.loads(rec.transcription_options or "{}")
    model_name = opts.get("model", "small")
    job_id = str(uuid.uuid4())

    state.jobs[job_id] = {
        "id": job_id,
        "type": "translate",
        "recording_id": recording_id,
        "status": "queued",
        "progress": 0.0,
        "message": "Queued",
        "file_path": rec.file_path,
        "original_filename": rec.filename,
        "options": {"model": model_name},
        "result": None,
        "error": None,
        "created_at": time.time(),
        "sse_queue": asyncio.Queue(),
        "event_loop": asyncio.get_running_loop(),
        "cancel_flag": threading.Event(),
        "logs": [],
        "temp_files": [],
    }
    _append_job_log(job_id, "INFO", f"Bulk translation job created for recording '{rec.filename}'")
    state.JOB_QUEUE.put_nowait(job_id)
    return {"ok": True, "job_id": job_id}
