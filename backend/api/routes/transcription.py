"""Transcription and job endpoints."""

import asyncio
import datetime
import json
import os
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from http_utils import content_disposition_attachment

import aiofiles
import shutil

import config
import state
from core.job_helpers import append_job_log, push_event, sync_job_to_db
from core.translation import translate_audio_chunk
from core.job_status import ACTIVE as ACTIVE_STATUSES
from core.job_status import JobStatus
from core.jobs import JobType, create_job, submit
from core.runtime_config import word_timestamps_default
from core.source_downloader import DownloadCandidate, is_supported_source_url, resolve_source_candidates
from core.transcription import start_download_prefetch
from core.transcription_config import TranscriptionConfig
from db import get_session, new_session
from exports import render_export
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from models import Recording, RecordingTag, Tag, Transcript
from settings import get_saved_hf_token, get_whisper_settings
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool
from storage import ingest_file

router = APIRouter()

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".mov", ".mkv", ".opus"}

# Where a recording came from. 'meeting' marks an auto-captured call, which is
# what the auto-summary feature keys off.
_VALID_SOURCES = {"upload", "url", "meeting"}

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


def _get_live_job(job_id: str) -> dict:
    """Like _get_job, but rejects tombstones left by the hourly cleanup.

    An expired job answers 410 with the recording id, so the client knows the
    transcript is still available under /api/recordings/{id} rather than being
    told the job never existed.
    """
    job = _get_job(job_id)
    if job.get("expired"):
        raise HTTPException(
            status_code=410,
            detail={
                "message": "This job has expired; its transcript lives in the library.",
                "recording_id": job.get("recording_id"),
            },
        )
    return job


def _upload_dir() -> Path:
    upload_dir = config.STORAGE_ROOT / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _to_bool(value: str, default: bool = False) -> bool:
    text = (value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _positive_int(value: str, default: int | None) -> int | None:
    try:
        parsed = int(value)
    except (ValueError, TypeError):
        return default
    return parsed if parsed > 0 else default


class TranscriptionForm:
    """The transcription options a client may send, declared once.

    /api/transcribe and /api/transcribe/url accept exactly the same set, and
    used to spell all sixteen out separately — so adding an option meant
    editing four lists in the right order, with nothing but the uniform `str`
    typing to catch a mis-ordered argument.

    Every field arrives as a string because these are multipart form fields;
    ``to_options`` does the coercion and merges in the saved defaults.

    The Form markers live in the annotations rather than in the defaults, so
    the defaults stay ordinary strings and the class can be constructed
    directly — in a test, or anywhere else that is not handling a request.

    Eight engine-tuning options used to be declared here too — ``compute_type``,
    ``device``, ``device_index``, ``vad_filter``, ``word_timestamps``,
    ``beam_size``, ``best_of`` and ``force_normalize_audio``. No client ever
    sent one: the web UI and the TUI both left them out and the route fell back
    to the saved Whisper settings, so they were surface with nothing behind it.
    They are now set from those settings and from the defaults in
    ``TranscriptionConfig``, which is where a job's engine configuration
    belongs. Choosing a device or a precision is a setting, not a per-request
    argument.
    """

    def __init__(
        self,
        model: Annotated[str, Form()] = "small",
        language: Annotated[str, Form()] = "",
        diarize: Annotated[str, Form()] = "false",
        colab_url: Annotated[str, Form()] = "",
        hf_token: Annotated[str, Form()] = "",
        num_speakers: Annotated[str, Form()] = "",
        min_speakers: Annotated[str, Form()] = "",
        max_speakers: Annotated[str, Form()] = "",
        folder_id: Annotated[str, Form()] = "",
    ) -> None:
        self.model = model
        self.language = language
        self.diarize = diarize
        self.colab_url = colab_url
        self.hf_token = hf_token
        self.num_speakers = num_speakers
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.folder_id = folder_id

    def to_options(self) -> dict[str, Any]:
        """Coerce the submitted strings into the options a job runs with."""
        # Device and precision come from the saved Whisper settings, which is
        # the only place they are set. Before, a client could name them per
        # request and these were merely the fallback — but no client did, and
        # without the fallback the stored values were write-only: the settings
        # page offered them, the TUI could set them, and no job ever read them.
        saved = get_whisper_settings()

        return TranscriptionConfig(
            model=self.model,
            language=self.language,
            diarize=_to_bool(self.diarize),
            colab_url=self.colab_url,
            num_speakers=_positive_int(self.num_speakers, None),
            min_speakers=_positive_int(self.min_speakers, None),
            max_speakers=_positive_int(self.max_speakers, None),
            compute_type=saved["whisper_compute"] or "auto",
            device=saved["whisper_device"] or "auto",
            # Per-word timing is an environment knob, not a per-job one. It was
            # unreachable while the form always wrote this key: the reader's
            # `opts.get("word_timestamps", word_timestamps_default())` could
            # never reach its own default, so AMICO_WORD_TIMESTAMPS did nothing.
            word_timestamps=word_timestamps_default(),
            # device_index, vad_filter, beam_size, best_of and
            # force_normalize_audio take TranscriptionConfig's defaults, which
            # are the values every job was already getting.
        ).model_dump()


def _create_recording_row(
    recording_id: str,
    filename: str,
    file_path: str,
    folder_id: str,
    opts_dict: dict[str, Any],
    source: str = "upload",
) -> None:
    """Insert the Recording row a job will later write its transcript against.

    Not best-effort. This row is the job's destination: without it the worker
    transcribes the whole file and sync_job_to_db finds nothing to attach the
    transcript to and returns quietly, so the user waits out a full run and
    ends up with nothing and no error. Better to refuse the upload.
    """
    with new_session() as session:
        recording = Recording(
            id=recording_id,
            filename=filename or "audio",
            file_path=file_path,
            folder_id=folder_id or None,
            status=JobStatus.QUEUED,
            source=source if source in _VALID_SOURCES else "upload",
            transcription_options=json.dumps(opts_dict),
        )
        session.add(recording)
        session.commit()


def _discard_ingested_audio(recording_id: str) -> None:
    """Remove audio that was ingested for a recording that never came to exist."""

    shutil.rmtree(config.RECORDINGS_DIR / recording_id, ignore_errors=True)


def _create_job(
    *,
    job_id: str,
    recording_id: str,
    original_filename: str,
    file_path: str,
    opts_dict: dict[str, Any],
    hf_token: str,
    job_type: str = JobType.TRANSCRIBE,
    source_url: str = "",
    source_platform: str = "",
) -> None:
    create_job(
        job_id=job_id,
        job_type=job_type,
        recording_id=recording_id,
        original_filename=original_filename,
        file_path=file_path,
        options={**opts_dict, "hf_token": hf_token or get_saved_hf_token()},
        source_url=source_url,
        source_platform=source_platform,
    )
    append_job_log(job_id, "INFO", f"Job created for source '{original_filename}'")
    submit(job_id)
    # URL imports start fetching immediately instead of waiting for the model
    # stage to reach them; see core/transcription.start_download_prefetch.
    start_download_prefetch(job_id)


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
    source: str = Form("upload"),
    form: TranscriptionForm = Depends(),
) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    job_id = str(uuid.uuid4())
    staging = _upload_dir() / f"{job_id}{ext}"

    async with aiofiles.open(staging, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            await f.write(chunk)

    recording_id = str(uuid.uuid4())
    permanent_path = ingest_file(staging, recording_id)
    opts_dict = form.to_options()
    filename = file.filename or "audio"

    try:
        _create_recording_row(
            recording_id=recording_id,
            filename=filename,
            file_path=str(permanent_path),
            folder_id=form.folder_id,
            opts_dict=opts_dict,
            source=source,
        )
    except Exception as exc:
        # The audio is already in managed storage but nothing will ever point
        # at it, so take it back out rather than leave an orphan directory.
        _discard_ingested_audio(recording_id)
        raise HTTPException(
            500, f"Could not save this recording to the library: {exc}"
        ) from exc

    _create_job(
        job_id=job_id,
        recording_id=recording_id,
        original_filename=filename,
        file_path=str(permanent_path),
        opts_dict=opts_dict,
        hf_token=form.hf_token,
    )
    return {"job_id": job_id, "recording_id": recording_id}


@router.post("/api/transcribe/url")
async def transcribe_from_url(
    source_url: str = Form(...),
    allow_playlist: str = Form("true"),
    form: TranscriptionForm = Depends(),
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

    opts_dict = form.to_options()

    jobs: list[dict[str, str]] = []
    for candidate in candidates:
        recording_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        display_name = candidate.title or "Online audio"

        _create_recording_row(
            recording_id=recording_id,
            filename=display_name,
            file_path="",
            folder_id=form.folder_id,
            opts_dict=opts_dict,
            source="url",
        )
        _ensure_recording_platform_tag(recording_id, candidate.platform)

        _create_job(
            job_id=job_id,
            recording_id=recording_id,
            original_filename=display_name,
            file_path="",
            opts_dict=opts_dict,
            hf_token=form.hf_token,
            job_type=JobType.DOWNLOAD_TRANSCRIBE,
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
    _get_live_job(job_id)

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


@router.get("/api/jobs")
def list_jobs() -> dict:
    """Return all non-terminal jobs with queue position for the UI queue strip."""
    rows: list[dict] = []
    for jid, j in state.jobs.items():
        st = j.get("status")
        if st not in ACTIVE_STATUSES:
            continue
        cf = j.get("cancel_flag")
        if cf and cf.is_set():
            continue
        rows.append({
            "id": jid,
            "type": j.get("type", JobType.TRANSCRIBE),
            "status": st,
            "progress": j.get("progress", 0.0),
            "message": j.get("message", ""),
            "filename": j.get("original_filename") or j.get("source_url") or "",
            "source_url": j.get("source_url", ""),
            "source_platform": j.get("source_platform", ""),
            "created_at": j.get("created_at", 0.0),
        })
    rows.sort(key=lambda r: r["created_at"])
    for idx, r in enumerate(rows):
        r["position"] = idx
    return {"jobs": rows}


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    job = _get_live_job(job_id)
    job["cancel_flag"].set()
    # Terminalize immediately so the UI (queue widget, transcript view)
    # reflects the cancelled state without waiting for the worker to reach
    # its next cancel check. The worker may still spend a bit of CPU until
    # the current blocking call (e.g. model load, pyannote step) returns,
    # but its further phases will see cancel_flag and bail.
    push_event(job_id, "cancelled", 0.0, "Job cancelled")
    sync_job_to_db(job_id)
    return {"ok": True}


@router.get("/api/audio/{job_id}")
def get_audio(job_id: str):
    job = _get_live_job(job_id)
    fp = job.get("file_path", "")
    if not fp or not os.path.exists(fp):
        raise HTTPException(404, "Audio file not found (may have expired)")
    try:
        if not Path(fp).resolve().is_relative_to(config.STORAGE_ROOT.resolve()):
            raise HTTPException(403, "Access denied")
    except ValueError:
        raise HTTPException(403, "Access denied")
    ext = Path(fp).suffix.lower()
    media_types = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".flac": "audio/flac"}
    return FileResponse(fp, media_type=media_types.get(ext, "audio/mpeg"))


@router.get("/api/jobs/{job_id}/result")
def get_result(job_id: str) -> dict:
    job = _get_live_job(job_id)
    if job["status"] != "done":
        raise HTTPException(409, f"Job not complete (status: {job['status']})")
    return job["result"]


@router.get("/api/jobs/{job_id}/logs")
def get_job_logs(job_id: str, limit: int = 300) -> dict:
    job = _get_live_job(job_id)
    safe_limit = max(1, min(limit, 1000))
    # A deque, so it has to be materialised before it can be sliced.
    logs = list(job["logs"])
    return {
        "status": job.get("status"),
        "progress": job.get("progress"),
        "message": job.get("message"),
        "logs": logs[-safe_limit:],
    }


@router.post("/api/jobs/{job_id}/rename-speaker")
async def rename_speaker(job_id: str, old_name: str = Form(...), new_name: str = Form(...)) -> dict:
    job = _get_live_job(job_id)
    if job["status"] != "done":
        raise HTTPException(409, "Job not complete")
    result = job["result"]
    if not result:
        raise HTTPException(404, "Result not found")

    if old_name in result.get("speakers", []):
        idx = result["speakers"].index(old_name)
        result["speakers"][idx] = new_name
        result["speakers"] = sorted(list(set(result["speakers"])))

    for seg in result.get("segments", []):
        if seg.get("speaker") == old_name:
            seg["speaker"] = new_name

    sync_job_to_db(job_id)
    return {"ok": True, "new_name": new_name}


@router.get("/api/jobs/{job_id}/export/{fmt}")
def export_job(job_id: str, fmt: str, wikilinks: bool = False):
    job = _get_live_job(job_id)
    if job["status"] != "done":
        raise HTTPException(409, "Job not complete")
    result = job["result"]
    if not result:
        raise HTTPException(404, "Result not available")
    filename = Path(job["original_filename"]).stem
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    # A live job predates the library, so it has no tags or folder yet. Only
    # the model name is read out of the options — the rest of that dict holds
    # the Hugging Face token.
    meta = {
        "model": str((job.get("options") or {}).get("model") or ""),
        "source": job.get("source_platform") or ("link" if job.get("source_url") else "upload"),
    }

    try:
        content, media_type, ext = render_export(
            fmt, result, title=filename, date=date_str, meta=meta, wikilinks=wikilinks
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": content_disposition_attachment(f"{filename}.{ext}")},
    )


@router.post("/api/recordings/{recording_id}/transcript/segments/{segment_index}/translate")
async def translate_segment_api(recording_id: str, segment_index: int, session: Session = Depends(get_session)) -> dict:
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

    translated_text = await run_in_threadpool(translate_audio_chunk, rec.file_path, seg["start"], seg["end"], model_name)

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

    job_id = create_job(
        job_type=JobType.TRANSLATE,
        recording_id=recording_id,
        original_filename=rec.filename,
        file_path=rec.file_path,
        options={"model": opts.get("model", "small")},
    )
    append_job_log(job_id, "INFO", f"Bulk translation job created for recording '{rec.filename}'")
    submit(job_id)
    return {"ok": True, "job_id": job_id}
