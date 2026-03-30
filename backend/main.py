import asyncio
import gc
import json
import os
import re
# Prevent OpenMP deadlocks when transcribing completely on CPU or with multiple sequential tasks
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import shutil
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

import sys

# In PyInstaller windowed mode, stdio can be None; some libs call stream.write().
_STDIO_FALLBACK_HANDLES = []


def _ensure_standard_streams() -> None:
    if sys.stdin is None:
        stdin_fallback = open(os.devnull, "r", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(stdin_fallback)
        sys.stdin = stdin_fallback
    if sys.stdout is None:
        stdout_fallback = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(stdout_fallback)
        sys.stdout = stdout_fallback
    if sys.stderr is None:
        stderr_fallback = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(stderr_fallback)
        sys.stderr = stderr_fallback


_ensure_standard_streams()

# Settings directory for persistent config (survives app reinstalls)
SETTINGS_DIR = Path.home() / ".amicoscript"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

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


@app.post("/api/exit")
async def _api_exit(request: Request):
    """Shutdown the application when called from localhost.

    This endpoint is intentionally permissive for local UX (frontend will POST
    here when the browser window is closed). We only act on requests from
    loopback addresses to reduce accidental remote shutdowns.
    """
    try:
        client_host = request.client.host if request.client else ""
    except Exception:
        client_host = ""

    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return {"status": "ignored"}

    def _delayed_exit():
        # Give the request/response cycle a moment to finish, then exit.
        time.sleep(0.1)
        os._exit(0)

    threading.Thread(target=_delayed_exit, daemon=True).start()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    """Load settings from disk."""
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_settings(settings: dict) -> None:
    """Save settings to disk."""
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _get_saved_hf_token() -> str:
    """Get the HF token from saved settings, or env var."""
    settings = _load_settings()
    return settings.get("hf_token", "") or os.environ.get("HF_TOKEN", "")


@app.on_event("startup")
async def startup() -> None:
    app.state.loop = asyncio.get_event_loop()
    asyncio.create_task(_cleanup_loop())
    # Start release poller if configured via env vars
    try:
        asyncio.create_task(_release_poller_loop())
    except Exception:
        pass


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
    level = "ERROR" if status == "error" else "INFO"
    _append_job_log(job_id, level, f"{status}: {message}")
    asyncio.run_coroutine_threadsafe(
        job["sse_queue"].put(event),
        app.state.loop,
    )


def _append_job_log(job_id: str, level: str, message: str) -> None:
    job = jobs.get(job_id)
    if not job:
        return
    logs = job.setdefault("logs", [])
    logs.append({
        "ts": round(time.time(), 3),
        "level": level,
        "message": message,
    })
    # Keep memory bounded for very long jobs.
    if len(logs) > 1000:
        del logs[:-1000]


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


def _is_missing_cuda_runtime_error(exc: Exception) -> bool:
    """Detect common errors caused by missing CUDA runtime DLLs/libraries."""
    message = str(exc).lower()
    markers = (
        "cublas",
        "cudnn",
        "cudart",
        "cuda",
        "nvcuda",
        "libcublas",
    )
    return any(marker in message for marker in markers)


def _is_missing_vad_asset_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "silero_vad_v6.onnx" in message or (
        "onnxruntimeerror" in message and "file doesn't exist" in message
    )


def _cleanup_job_temp_files(job: dict) -> None:
    for temp_fp in job.get("temp_files", []):
        if temp_fp and os.path.exists(temp_fp):
            try:
                os.remove(temp_fp)
            except OSError:
                pass
    job["temp_files"] = []


def _convert_audio_for_transcription(job_id: str, input_path: str) -> str:
    """Normalize input audio via ffmpeg to improve decoder reliability."""
    ext = Path(input_path).suffix.lower()
    # WAV/FLAC are usually decoder-friendly already.
    if ext in {".wav", ".flac"}:
        return input_path

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        _append_job_log(job_id, "WARN", "ffmpeg not found in PATH; using original file")
        return input_path

    normalized_path = str(Path(input_path).with_name(f"{Path(input_path).stem}_norm.wav"))
    cmd = [
        ffmpeg_bin,
        "-y",
        "-v",
        "error",
        "-i",
        input_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        normalized_path,
    ]

    try:
        _append_job_log(job_id, "INFO", "Normalizing audio with ffmpeg (mono/16k PCM)")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            _append_job_log(job_id, "WARN", f"ffmpeg normalization failed: {stderr or f'code {proc.returncode}'}")
            return input_path

        job = jobs.get(job_id)
        if job is not None:
            temp_files = job.setdefault("temp_files", [])
            temp_files.append(normalized_path)
        _append_job_log(job_id, "INFO", f"Using normalized audio: {Path(normalized_path).name}")
        return normalized_path
    except Exception as exc:  # noqa: BLE001
        _append_job_log(job_id, "WARN", f"ffmpeg normalization exception: {exc}")
        return input_path


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

_cached_model = None
_cached_model_name = None
_cached_model_device = None

import queue
import threading

JOB_QUEUE = queue.Queue()

def _worker_loop():
    """Background thread that sequentially processes transcription jobs."""
    while True:
        job_id = JOB_QUEUE.get()
        if job_id is None:
            break
        try:
            _process_job(job_id)
        except Exception:
            pass
        finally:
            JOB_QUEUE.task_done()

# Start the single background worker thread
threading.Thread(target=_worker_loop, daemon=True).start()

def _get_whisper_model(model_name: str) -> tuple:
    """Return an instantiated WhisperModel and the device it's loaded on.
    Caches the model to prevent reloading penalties and GPU memory fragmentation.
    """
    global _cached_model, _cached_model_name, _cached_model_device
    from faster_whisper import WhisperModel

    if _cached_model is not None and _cached_model_name == model_name:
        return _cached_model, _cached_model_device

    # Evict old model properly
    if _cached_model is not None:
        del _cached_model
        _cached_model = None
        gc.collect()
        try:
            import torch
            if hasattr(torch, "cuda") and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    model_device = "auto"
    try:
        model = WhisperModel(model_name, device=model_device, compute_type="int8")
    except Exception as exc:
        if not _is_missing_cuda_runtime_error(exc):
            raise
        model_device = "cpu"
        model = WhisperModel(model_name, device=model_device, compute_type="int8")
        
    _cached_model = model
    _cached_model_name = model_name
    _cached_model_device = model_device
    return _cached_model, _cached_model_device

def _process_job(job_id: str) -> None:
    global _cached_model, _cached_model_device

    job = jobs[job_id]
    opts = job["options"]
    file_path: str = job["file_path"]
    model = None
    segments_gen = None
    info = None
    pipeline = None
    diarization = None
    stop_first_segment_watchdog = None

    # Job loop prevents concurrent transcribes simply by being a single thread
    try:
        _append_job_log(
            job_id,
            "INFO",
            f"Worker started. model={opts['model']}, language={opts['language'] or 'auto'}, diarize={opts['diarize']}",
        )
        # Phase 1: load model
        _push_event(job_id, "loading_model", 0.03, f"Loading model '{opts['model']}'…")

        try:
            model, model_device = _get_whisper_model(opts["model"])
        except Exception as exc:
            _append_job_log(job_id, "WARN", f"Model init failed: {exc}")
            raise

        # Phase 2: transcribe
        _push_event(
            job_id,
            "transcribing",
            0.05,
            "Starting transcription (first progress update may take time on long files/CPU)…",
        )

        lang = opts["language"] or None
        use_word_timestamps = os.environ.get("AMICO_WORD_TIMESTAMPS", "0") == "1"
        use_vad_filter = True
        _append_job_log(
            job_id,
            "INFO",
            f"Transcribe options: word_timestamps={use_word_timestamps}, vad_filter={use_vad_filter}",
        )
        whisper_input = _convert_audio_for_transcription(job_id, file_path)

        first_segment_event = threading.Event()
        stop_first_segment_watchdog = threading.Event()
        max_first_segment_wait_seconds = 600

        def _first_segment_watchdog() -> None:
            waited_seconds = 0
            while not stop_first_segment_watchdog.wait(10):
                if first_segment_event.is_set():
                    return
                waited_seconds += 10
                _push_event(
                    job_id,
                    "transcribing",
                    0.05,
                    f"Still transcribing… waiting for first segment ({waited_seconds}s)",
                )
                if waited_seconds >= max_first_segment_wait_seconds:
                    _append_job_log(
                        job_id,
                        "ERROR",
                        f"First segment timeout after {waited_seconds}s. Aborting job.",
                    )
                    _push_event(
                        job_id,
                        "error",
                        -1,
                        "Transcription timed out before first segment. Try a smaller model or split the audio.",
                    )
                    job["cancel_flag"].set()
                    stop_first_segment_watchdog.set()
                    return

        watchdog_thread = threading.Thread(target=_first_segment_watchdog, daemon=True)
        watchdog_thread.start()
        try:
            try:
                segments_gen, info = model.transcribe(
                    whisper_input,
                    language=lang,
                    word_timestamps=use_word_timestamps,
                    vad_filter=use_vad_filter,
                )
            except Exception as exc:
                if use_vad_filter and _is_missing_vad_asset_error(exc):
                    use_vad_filter = False
                    _append_job_log(
                        job_id,
                        "WARN",
                        "VAD model asset missing in package; retrying with vad_filter=False",
                    )
                    segments_gen, info = model.transcribe(
                        whisper_input,
                        language=lang,
                        word_timestamps=use_word_timestamps,
                        vad_filter=use_vad_filter,
                    )
                    duration = info.duration or 1.0  # avoid division by zero
                elif model_device == "cpu" or not _is_missing_cuda_runtime_error(exc):
                    raise
                else:
                    _append_job_log(job_id, "WARN", f"GPU transcription failed: {exc}")
                    _push_event(
                        job_id,
                        "transcribing",
                        0.05,
                        "GPU runtime unavailable. Retrying on CPU…",
                    )
                    model_device = "cpu"
                    from faster_whisper import WhisperModel
                    model = WhisperModel(opts["model"], device=model_device, compute_type="int8")
                    
                    _cached_model = model
                    _cached_model_device = model_device
                    try:
                        segments_gen, info = model.transcribe(
                            whisper_input,
                            language=lang,
                            word_timestamps=use_word_timestamps,
                            vad_filter=use_vad_filter,
                        )
                    except Exception as cpu_exc:
                        if use_vad_filter and _is_missing_vad_asset_error(cpu_exc):
                            use_vad_filter = False
                            _append_job_log(
                                job_id,
                                "WARN",
                                "VAD model asset missing after CPU fallback; retrying with vad_filter=False",
                            )
                            segments_gen, info = model.transcribe(
                                whisper_input,
                                language=lang,
                                word_timestamps=use_word_timestamps,
                                vad_filter=use_vad_filter,
                            )
                        else:
                            raise
                    duration = info.duration or 1.0  # avoid division by zero
            else:
                duration = info.duration or 1.0  # avoid division by zero

            # If watchdog already marked this job as failed, stop here.
            if job.get("status") == "error":
                return

            segments_list = []
            for seg in segments_gen:
                if not first_segment_event.is_set():
                    first_segment_event.set()
                    stop_first_segment_watchdog.set()
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
        finally:
            stop_first_segment_watchdog.set()

        # Phase 3: diarization (optional)
        speakers: list[str] = []
        if opts["diarize"] and opts.get("hf_token"):
            _push_event(job_id, "diarizing", 0.82, "Running speaker diarization…")

            # Inject a torchaudio-backed torchcodec shim into sys.modules
            # BEFORE importing pyannote.audio. This prevents pyannote from
            # loading the real torchcodec C extension (which fails when
            # FFmpeg shared libs or CUDA libs are unavailable — e.g. Docker
            # on ARM / PyInstaller bundles).
            import sys as _sys  # noqa: PLC0415
            import types as _types  # noqa: PLC0415
            import torchaudio as _ta  # noqa: PLC0415
            import torch as _torch  # noqa: PLC0415

            if "torchcodec" not in _sys.modules:
                _tc = _types.ModuleType("torchcodec")
                _tc_decoders = _types.ModuleType("torchcodec.decoders")

                class _AudioStreamMetadata:
                    """Mimics torchcodec.decoders.AudioStreamMetadata."""
                    def __init__(self, sample_rate, num_frames):
                        self.sample_rate = sample_rate
                        self.num_frames = num_frames
                        self.duration_seconds_from_header = num_frames / sample_rate if sample_rate else 0

                class _AudioSamples:
                    """Mimics torchcodec.AudioSamples."""
                    def __init__(self, data, sample_rate):
                        self.data = data
                        self.sample_rate = sample_rate

                class _AudioDecoder:
                    """torchaudio-backed replacement for torchcodec.decoders.AudioDecoder."""
                    def __init__(self, source):
                        self._source = source
                        info = _ta.info(source)
                        self.metadata = _AudioStreamMetadata(
                            sample_rate=info.sample_rate,
                            num_frames=info.num_frames,
                        )

                    def get_all_samples(self):
                        waveform, sr = _ta.load(self._source)
                        return _AudioSamples(waveform, sr)

                    def get_samples_played_in_range(self, start, end):
                        info = _ta.info(self._source)
                        sr = info.sample_rate
                        frame_offset = int(start * sr)
                        num_frames = int((end - start) * sr)
                        waveform, sr = _ta.load(
                            self._source, frame_offset=frame_offset, num_frames=num_frames
                        )
                        return _AudioSamples(waveform, sr)

                _tc_decoders.AudioDecoder = _AudioDecoder
                _tc_decoders.AudioStreamMetadata = _AudioStreamMetadata
                _tc.AudioSamples = _AudioSamples
                _tc.decoders = _tc_decoders
                _sys.modules["torchcodec"] = _tc
                _sys.modules["torchcodec.decoders"] = _tc_decoders

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
        _append_job_log(job_id, "INFO", "Worker finished successfully.")

    except Exception as exc:  # noqa: BLE001
        job["error"] = str(exc)
        _append_job_log(job_id, "ERROR", f"Worker failed: {exc}")
        _append_job_log(job_id, "ERROR", traceback.format_exc())
        _push_event(job_id, "error", -1, str(exc))
    finally:
        if stop_first_segment_watchdog is not None:
            stop_first_segment_watchdog.set()

        if segments_gen is not None:
            close_fn = getattr(segments_gen, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception:  # noqa: BLE001
                    pass

        _cleanup_job_temp_files(job)

        # Release local heavy references as soon as a job exits.
        # We do NOT deliberately set global model cache to None so it can be reused.
        segments_gen = None
        model = None
        info = None
        pipeline = None
        diarization = None

        try:
            import torch as _torch  # noqa: PLC0415
            if hasattr(_torch, "cuda") and _torch.cuda.is_available():
                _torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass

        import gc
        gc.collect()
        _append_job_log(job_id, "INFO", "Worker cleanup complete.")


# ---------------------------------------------------------------------------
# GitHub release poller
#
# Configure with environment variables:
# - GITHUB_OWNER: owner/org of the repo
# - GITHUB_REPO: repository name
# - GITHUB_TOKEN: optional token for higher rate limits
# Polls GitHub `releases/latest` and stores `app.state.latest_release`.
# ---------------------------------------------------------------------------

import urllib.request as _urlreq
import urllib.error as _urlerr


def _get_local_version() -> str:
    try:
        v = get_version().get("version", "")
        return v or ""
    except Exception:
        return ""


def _fetch_latest_release(owner: str, repo: str, token: Optional[str] = None) -> dict:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    req = _urlreq.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
    if token:
        req.add_header("Authorization", f"token {token}")
    try:
        with _urlreq.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8"))
    except _urlerr.HTTPError as e:
        try:
            # Try to parse error body
            body = e.read().decode("utf-8")
            return {"error": f"HTTP {e.code}", "body": body}
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except Exception as exc:
        return {"error": str(exc)}


def _is_version_newer(local: str, remote_tag: str) -> bool:
    def parse(v: str):
        s = re.sub(r"[^0-9.]", "", v or "").strip(".")
        return tuple(int(p) for p in s.split(".") if p.isdigit()) if s else ()

    return parse(remote_tag) > parse(local)


async def _release_poller_loop() -> None:
    owner = os.environ.get("GITHUB_OWNER", "")
    repo = os.environ.get("GITHUB_REPO", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not owner or not repo:
        # Not configured; skip poller
        return

    # initial state
    app.state.latest_release = {"tag_name": "", "html_url": "", "name": "", "body": ""}

    while True:
        try:
            info = _fetch_latest_release(owner, repo, token or None)
            if info and not info.get("error"):
                tag = info.get("tag_name", "")
                html = info.get("html_url", "")
                name = info.get("name", "")
                body = info.get("body", "")
                app.state.latest_release = {
                    "tag_name": tag,
                    "html_url": html,
                    "name": name,
                    "body": body,
                }
                local = _get_local_version()
                try:
                    app.state.update_available = _is_version_newer(local, tag)
                    app.state.local_version = local
                except Exception:
                    app.state.update_available = False
            else:
                # Store last error for diagnostics
                app.state.latest_release = {"error": info.get("error", "unknown")}
        except Exception:
            pass

        # Poll every 4 hours
        await asyncio.sleep(60 * 60 * 4)


@app.get("/api/latest-release")
def api_latest_release() -> dict:
    """Return last-seen GitHub release info and whether an update is available."""
    info = getattr(app.state, "latest_release", {}) or {}
    update = getattr(app.state, "update_available", False)
    local = getattr(app.state, "local_version", _get_local_version())
    return {"latest": info, "update_available": bool(update), "local_version": local}


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
                _cleanup_job_temp_files(job)
                jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/settings")
def get_settings() -> dict:
    """Return saved settings (HF token, etc.)."""
    settings = _load_settings()
    return {"hf_token": settings.get("hf_token", "")}


@app.get("/api/version")
def get_version() -> dict:
    """Return current project version from VERSION file if available."""
    try:
        # Prefer a top-level VERSION file near the repo root or BASE_DIR parent
        candidate = BASE_DIR / ".." / "VERSION"
        candidate = candidate.resolve()
        if not candidate.exists():
            candidate = BASE_DIR / "VERSION"
        if not candidate.exists():
            # fallback to repository root
            candidate = Path(__file__).resolve().parents[2] / "VERSION"
        if candidate.exists():
            ver = candidate.read_text(encoding="utf-8").strip()
        else:
            ver = ""
    except Exception:
        ver = ""
    return {"version": ver}


@app.post("/api/settings")
async def save_settings(hf_token: str = Form("")) -> dict:
    """Persist settings to disk."""
    settings = _load_settings()
    settings["hf_token"] = hf_token
    _save_settings(settings)
    return {"ok": True}


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
            "hf_token": hf_token or _get_saved_hf_token(),
            "num_speakers": int(num_speakers) if num_speakers.isdigit() else None,
        },
        "result": None,
        "error": None,
        "created_at": time.time(),
        "sse_queue": asyncio.Queue(),
        "cancel_flag": threading.Event(),
        "logs": [],
        "temp_files": [],
    }
    jobs[job_id] = job
    _append_job_log(job_id, "INFO", f"Job created for file '{job['original_filename']}'")

    JOB_QUEUE.put(job_id)

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
    # Serve the frontend directory (static files)
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    # If a changelog was bundled into the application root, serve it at /CHANGELOG.md
    changelog_path = BASE_DIR / "CHANGELOG.md"
    if changelog_path.exists():
        @app.get("/CHANGELOG.md")
        async def _serve_changelog():
            return FileResponse(str(changelog_path))
