import asyncio
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

import sys

# Fix for PyInstaller paths
if hasattr(sys, '_MEIPASS'):
    # Running in a bundle
    BASE_DIR = Path(sys._MEIPASS)
    # Uploads should be in a persistent location, e.g., user home or near the exe
    # For now, let's keep it near the exe (one level up from _MEIPASS if onedir)
    EXE_DIR = Path(sys.executable).parent
    UPLOAD_DIR = EXE_DIR / "uploads"
else:
    # Running in normal Python
    BASE_DIR = Path(__file__).parent
    UPLOAD_DIR = BASE_DIR / "uploads"

UPLOAD_DIR.mkdir(exist_ok=True)
if (BASE_DIR / "frontend").exists():
    FRONTEND_DIR = BASE_DIR / "frontend"
else:
    FRONTEND_DIR = BASE_DIR.parent / "frontend"

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}

MODELS_META = [
    {"id": "tiny",     "name": "Tiny",     "params": "~39M",   "ram": "~1 GB",  "speed": 5, "accuracy": 1},
    {"id": "base",     "name": "Base",     "params": "~74M",   "ram": "~1 GB",  "speed": 4, "accuracy": 2},
    {"id": "small",    "name": "Small",    "params": "~244M",  "ram": "~2 GB",  "speed": 3, "accuracy": 3},
    {"id": "medium",   "name": "Medium",   "params": "~769M",  "ram": "~5 GB",  "speed": 2, "accuracy": 4},
    {"id": "large-v2", "name": "Large v2", "params": "~1.5B",  "ram": "~10 GB", "speed": 1, "accuracy": 5},
    {"id": "large-v3", "name": "Large v3", "params": "~1.5B",  "ram": "~10 GB", "speed": 1, "accuracy": 5},
]

# job_id -> job dict
jobs: dict[str, dict] = {}

app = FastAPI(title="AmicoScript")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    app.state.loop = asyncio.get_event_loop()
    asyncio.create_task(_cleanup_loop())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _push_event(job_id: str, status: str, progress: float, message: str, data: Optional[dict] = None) -> None:
    """Thread-safe: push an SSE event onto the job's asyncio queue."""
    job = jobs.get(job_id)
    if not job:
        return
    job["status"] = status
    job["progress"] = progress
    job["message"] = message
    event = {"status": status, "progress": progress, "message": message}
    if data:
        event["data"] = data
    asyncio.run_coroutine_threadsafe(
        job["sse_queue"].put(event),
        app.state.loop,
    )


def _ms(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm for SRT."""
    ms = int(round(seconds * 1000))
    h = ms // 3_600_000
    ms %= 3_600_000
    m = ms // 60_000
    ms %= 60_000
    s = ms // 1_000
    ms %= 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts(seconds: float) -> str:
    """Format seconds as M:SS for display."""
    total = int(seconds)
    m = total // 60
    s = total % 60
    return f"{m}:{s:02d}"


def _assign_speaker(seg_start: float, seg_end: float, diarization) -> str:
    best_speaker = "SPEAKER_00"
    best_overlap = 0.0
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        overlap = max(0.0, min(seg_end, turn.end) - max(seg_start, turn.start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = speaker
    return best_speaker


# ---------------------------------------------------------------------------
# Export formatters
# ---------------------------------------------------------------------------

def _format_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)


def _format_srt(result: dict) -> str:
    lines = []
    for i, seg in enumerate(result["segments"], 1):
        speaker_prefix = f"[{seg['speaker']}] " if seg.get("speaker") else ""
        lines.append(str(i))
        lines.append(f"{_ms(seg['start'])} --> {_ms(seg['end'])}")
        lines.append(f"{speaker_prefix}{seg['text']}")
        lines.append("")
    return "\n".join(lines)


def _format_txt(result: dict) -> str:
    lines = []
    prev_speaker = None
    for seg in result["segments"]:
        speaker = seg.get("speaker", "")
        if speaker and speaker != prev_speaker:
            if lines:
                lines.append("")
            lines.append(f"{speaker}:")
            prev_speaker = speaker
        ts = _ts(seg["start"])
        prefix = f"[{ts}] " if not speaker else f"  [{ts}] "
        lines.append(f"{prefix}{seg['text']}")
    return "\n".join(lines)


def _format_md(result: dict) -> str:
    lang = result.get("language", "").upper()
    dur = _ts(result.get("duration", 0))
    lines = [
        "# AmicoScript Transcript",
        "",
        f"**Language:** {lang or 'auto'} | **Duration:** {dur} | **Segments:** {result.get('num_segments', 0)}",
        "",
        "---",
        "",
    ]
    prev_speaker = None
    for seg in result["segments"]:
        speaker = seg.get("speaker", "")
        if speaker and speaker != prev_speaker:
            lines.append(f"**{speaker}**")
            prev_speaker = speaker
        ts_start = _ts(seg["start"])
        ts_end = _ts(seg["end"])
        lines.append(f"> `{ts_start} – {ts_end}` {seg['text']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _worker(job_id: str) -> None:
    job = jobs[job_id]
    opts = job["options"]
    file_path: str = job["file_path"]

    try:
        # Phase 1: load model
        _push_event(job_id, "loading_model", 0.03, f"Loading model '{opts['model']}'…")

        from faster_whisper import WhisperModel  # noqa: PLC0415
        model = WhisperModel(opts["model"], device="auto", compute_type="int8")

        # Phase 2: transcribe
        _push_event(job_id, "transcribing", 0.05, "Starting transcription…")

        lang = opts["language"] or None
        segments_gen, info = model.transcribe(
            file_path,
            language=lang,
            word_timestamps=True,
        )
        duration = info.duration or 1.0  # avoid division by zero

        segments_list = []
        for seg in segments_gen:
            if job["cancel_flag"].is_set():
                _push_event(job_id, "cancelled", 0.0, "Cancelled.")
                return

            progress = 0.05 + 0.75 * min(seg.end / duration, 1.0)
            _push_event(
                job_id,
                "transcribing",
                progress,
                f"Transcribing… {_ts(seg.end)} / {_ts(duration)}",
            )

            segments_list.append({
                "id": len(segments_list),
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg.text.strip(),
                "speaker": "",
                "words": [
                    {
                        "word": w.word,
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "probability": round(w.probability, 4),
                    }
                    for w in (seg.words or [])
                ],
            })

        # Phase 3: diarization (optional)
        speakers: list[str] = []
        if opts["diarize"] and opts.get("hf_token"):
            _push_event(job_id, "diarizing", 0.82, "Running speaker diarization…")

            from pyannote.audio import Pipeline  # noqa: PLC0415
            pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=opts["hf_token"],
            )
            diarization = pipeline(file_path)

            for seg in segments_list:
                seg["speaker"] = _assign_speaker(seg["start"], seg["end"], diarization)

            speakers = sorted(set(s["speaker"] for s in segments_list))

        # Phase 4: done
        result = {
            "language": info.language or "",
            "duration": round(duration, 3),
            "num_segments": len(segments_list),
            "speakers": speakers,
            "segments": segments_list,
        }
        job["result"] = result

        _push_event(job_id, "done", 1.0, "Transcription complete.", data=result)

    except Exception as exc:  # noqa: BLE001
        _push_event(job_id, "error", -1, str(exc))


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        cutoff = time.time() - 3600
        for job_id in list(jobs.keys()):
            job = jobs[job_id]
            if job.get("created_at", 0) < cutoff:
                fp = job.get("file_path", "")
                if fp and os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
                jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/models")
def get_models() -> list:
    return MODELS_META


@app.post("/api/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form("small"),
    language: str = Form(""),
    diarize: str = Form("false"),
    hf_token: str = Form(""),
    num_speakers: str = Form(""),
) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    job_id = str(uuid.uuid4())
    dest = UPLOAD_DIR / f"{job_id}{ext}"

    async with aiofiles.open(dest, "wb") as f:
        content = await file.read()
        await f.write(content)

    job: dict = {
        "id": job_id,
        "status": "queued",
        "progress": 0.0,
        "message": "Queued",
        "file_path": str(dest),
        "original_filename": file.filename or "audio",
        "options": {
            "model": model,
            "language": language,
            "diarize": diarize.lower() == "true",
            "hf_token": hf_token or os.environ.get("HF_TOKEN", ""),
            "num_speakers": int(num_speakers) if num_speakers.isdigit() else None,
        },
        "result": None,
        "error": None,
        "created_at": time.time(),
        "sse_queue": asyncio.Queue(),
        "cancel_flag": threading.Event(),
    }
    jobs[job_id] = job

    t = threading.Thread(target=_worker, args=(job_id,), daemon=True)
    t.start()

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    _get_job(job_id)

    async def event_generator():
        q = jobs[job_id]["sse_queue"]
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


@app.post("/api/jobs/{job_id}/rename-speaker")
async def rename_speaker(job_id: str, old_name: str = Form(...), new_name: str = Form(...)) -> dict:
    job = _get_job(job_id)
    if job["status"] != "done":
        raise HTTPException(409, "Job not complete")

    result = job["result"]
    if not result:
        raise HTTPException(404, "Result not found")

    # Update speakers list
    if old_name in result["speakers"]:
        idx = result["speakers"].index(old_name)
        result["speakers"][idx] = new_name
        result["speakers"] = sorted(list(set(result["speakers"])))

    # Update segments
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

    if fmt == "json":
        content = _format_json(result)
        media_type = "application/json"
        ext = "json"
    elif fmt == "srt":
        content = _format_srt(result)
        media_type = "text/plain"
        ext = "srt"
    elif fmt == "txt":
        content = _format_txt(result)
        media_type = "text/plain"
        ext = "txt"
    elif fmt == "md":
        content = _format_md(result)
        media_type = "text/markdown"
        ext = "md"
    else:
        raise HTTPException(400, f"Unknown format: {fmt}. Use json, srt, txt, or md.")

    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}.{ext}"'},
    )


# ---------------------------------------------------------------------------
# Serve frontend (must be last so /api routes take priority)
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
