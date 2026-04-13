"""Transcription and diarization pipeline for AmicoScript.

This module owns the background worker thread, the Whisper model cache,
audio normalisation helpers, and the full _process_job() implementation.

Diarization fixes applied here:
  1. _convert_audio_for_diarization() always produces a mono 16 kHz WAV so
     pyannote and Whisper decode from the same codec path, eliminating
     timestamp drift and the need for torchcodec on arbitrary formats.
  2. num_speakers is forwarded to pipeline() so the user's speaker-count
     hint is actually honoured.
  3. A "warning" SSE event is emitted when diarize=True but no HF token
     was provided, instead of silently skipping speaker identification.
"""
import asyncio
import gc
import logging
import os
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
from sqlmodel import select

import state
from exports import _ts
from shims import inject_torchcodec_shim
import ffmpeg_helper


# ---------------------------------------------------------------------------
# DB sync helper
# ---------------------------------------------------------------------------

def _sync_job_to_db(job_id: str) -> None:
    """Write terminal job state to the SQLite DB (called from the worker thread)."""
    job = state.jobs.get(job_id)
    if not job:
        return
    recording_id = job.get("recording_id")
    if not recording_id:
        return  # pre-v2 job or DB unavailable

    try:
        import json as _json
        import time as _time
        from db import new_session
        from models import Recording, Transcript
        from sqlmodel import select

        with new_session() as session:
            rec = session.get(Recording, recording_id)
            if not rec:
                return

            rec.status = job.get("status", rec.status)
            result = job.get("result")
            if result:
                rec.duration = result.get("duration")

                # Create or replace the Transcript row.
                existing = session.exec(
                    select(Transcript).where(Transcript.recording_id == recording_id)
                ).first()

                full_text = " ".join(
                    s.get("text", "") for s in result.get("segments", [])
                )
                json_data = _json.dumps(result)
                now = _time.time()

                if existing:
                    existing.full_text = full_text
                    existing.json_data = json_data
                    existing.updated_at = now
                    session.add(existing)
                else:
                    session.add(Transcript(
                        recording_id=recording_id,
                        full_text=full_text,
                        json_data=json_data,
                    ))

            session.add(rec)
            session.commit()

    except Exception:
        pass  # DB failure must never crash the transcription worker


# ---------------------------------------------------------------------------
# SSE / logging helpers
# ---------------------------------------------------------------------------

def _push_event(
    job_id: str,
    status: str,
    progress: float,
    message: str,
    data: Optional[dict] = None,
) -> None:
    """Thread-safe: push an SSE event onto the job's asyncio queue."""
    job = state.jobs.get(job_id)
    if not job:
        return
    job["status"] = status
    job["progress"] = progress
    job["message"] = message
    event: dict = {"status": status, "progress": progress, "message": message}
    if data:
        event["data"] = data
    level = "ERROR" if status == "error" else "INFO"
    _append_job_log(job_id, level, f"{status}: {message}")
    if state.event_loop is not None:
        asyncio.run_coroutine_threadsafe(
            job["sse_queue"].put(event),
            state.event_loop,
        )


def _append_job_log(job_id: str, level: str, message: str) -> None:
    job = state.jobs.get(job_id)
    if not job:
        return
    logs = job.setdefault("logs", [])
    logs.append({"ts": round(time.time(), 3), "level": level, "message": message})
    if len(logs) > 1000:
        del logs[:-1000]


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _cleanup_job_temp_files(job: dict) -> None:
    for temp_fp in job.get("temp_files", []):
        if temp_fp and os.path.exists(temp_fp):
            try:
                os.remove(temp_fp)
            except OSError:
                pass
    job["temp_files"] = []


def _convert_audio_for_transcription(job_id: str, input_path: str) -> str:
    """Normalize input audio via ffmpeg for Whisper.

    Skips conversion when the source is already WAV or FLAC (decoder-friendly
    formats that faster-whisper handles natively).
    """
    ext = Path(input_path).suffix.lower()
    if ext in {".wav", ".flac"}:
        return input_path

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        _append_job_log(job_id, "WARN", "ffmpeg not found in PATH; using original file")
        return input_path

    normalized_path = str(
        Path(input_path).with_name(f"{Path(input_path).stem}_norm.wav")
    )
    cmd = [
        ffmpeg_bin, "-y", "-v", "error",
        "-i", input_path,
        "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
        normalized_path,
    ]
    try:
        _append_job_log(job_id, "INFO", "Normalizing audio with ffmpeg (mono/16k PCM)")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            _append_job_log(
                job_id, "WARN",
                f"ffmpeg normalization failed: {stderr or f'code {proc.returncode}'}",
            )
            return input_path
        job = state.jobs.get(job_id)
        if job is not None:
            job.setdefault("temp_files", []).append(normalized_path)
        _append_job_log(job_id, "INFO", f"Using normalized audio: {Path(normalized_path).name}")
        return normalized_path
    except Exception as exc:
        _append_job_log(job_id, "WARN", f"ffmpeg normalization exception: {exc}")
        return input_path


def _convert_audio_for_diarization(job_id: str, input_path: str) -> str:
    """Always produce a fresh mono 16 kHz WAV for pyannote.

    Unlike _convert_audio_for_transcription this never skips conversion,
    even for WAV/FLAC sources.  Reasons:

    * Ensures pyannote and Whisper decode the exact same PCM stream,
      preventing timestamp drift caused by differing codec seek behaviour.
    * Guarantees the torchaudio shim receives a plain WAV it can load
      without the real torchcodec C extension, removing the need for
      format-specific code paths in the shim.
    * Multi-channel or high-sample-rate WAV/FLAC files are down-mixed and
      resampled here rather than inside pyannote's own audio loader.
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        _append_job_log(job_id, "WARN", "ffmpeg not found; diarization will use original file")
        return input_path

    diar_path = str(
        Path(input_path).with_name(f"{Path(input_path).stem}_diar.wav")
    )
    cmd = [
        ffmpeg_bin, "-y", "-v", "error",
        "-i", input_path,
        "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
        diar_path,
    ]
    try:
        _append_job_log(job_id, "INFO", "Normalizing audio for diarization (mono/16k PCM)")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            _append_job_log(
                job_id, "WARN",
                f"ffmpeg diarization normalization failed: "
                f"{stderr or f'code {proc.returncode}'}; using original file",
            )
            return input_path
        job = state.jobs.get(job_id)
        if job is not None:
            job.setdefault("temp_files", []).append(diar_path)
        _append_job_log(
            job_id, "INFO",
            f"Using normalized audio for diarization: {Path(diar_path).name}",
        )
        return diar_path
    except Exception as exc:
        _append_job_log(job_id, "WARN", f"ffmpeg diarization normalization exception: {exc}")
        return input_path


# ---------------------------------------------------------------------------
# Error classifiers
# ---------------------------------------------------------------------------

def _is_missing_cuda_runtime_error(exc: Exception) -> bool:
    """Detect common errors caused by missing CUDA runtime DLLs/libraries."""
    message = str(exc).lower()
    markers = ("cublas", "cudnn", "cudart", "cuda", "nvcuda", "libcublas")
    return any(marker in message for marker in markers)


def _is_missing_vad_asset_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "silero_vad_v6.onnx" in message or (
        "onnxruntimeerror" in message and "file doesn't exist" in message
    )


# ---------------------------------------------------------------------------
# Speaker assignment
# ---------------------------------------------------------------------------

def _assign_speaker(seg_start: float, seg_end: float, diarization) -> str:
    """Return the speaker label whose diarization track maximally overlaps the segment.

    When no turn overlaps (e.g. the segment falls in a gap between turns),
    fall back to the nearest turn by time distance rather than always
    returning SPEAKER_00.
    """
    best_speaker = None
    best_overlap = 0.0
    best_dist = float("inf")
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        overlap = max(0.0, min(seg_end, turn.end) - max(seg_start, turn.start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = speaker
        elif best_overlap == 0.0:
            # nearest-neighbour fallback: pick the closest turn boundary
            dist = min(abs(seg_start - turn.end), abs(seg_end - turn.start))
            if dist < best_dist:
                best_dist = dist
                best_speaker = speaker
    return best_speaker or "SPEAKER_00"


# ---------------------------------------------------------------------------
# Whisper model cache
# ---------------------------------------------------------------------------

def _get_whisper_model(model_name: str) -> tuple:
    """Return a (WhisperModel, device) pair, reusing the cached instance when possible."""
    from faster_whisper import WhisperModel
    try:
        from backend import resource_downloader
    except ImportError:
        import resource_downloader

    if (
        state._cached_model is not None
        and state._cached_model_name == model_name
    ):
        return state._cached_model, state._cached_model_device

    # Evict old model before loading a new one.
    if state._cached_model is not None:
        del state._cached_model
        state._cached_model = None
        gc.collect()
        try:
            import torch
            if hasattr(torch, "cuda") and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    model_device = "auto"
    try:
            # Ensure model assets are present (downloads on demand when needed)
            try:
                resource_downloader.ensure_whisper_model(model_name)
            except Exception:
                # If download is unavailable, continue — WhisperModel may handle remote fetch
                pass
            model = WhisperModel(model_name, device=model_device, compute_type="int8")
    except Exception as exc:
        if not _is_missing_cuda_runtime_error(exc):
            raise
        model_device = "cpu"
        model = WhisperModel(model_name, device=model_device, compute_type="int8")

    state._cached_model = model
    state._cached_model_name = model_name
    state._cached_model_device = model_device
    return state._cached_model, state._cached_model_device


# ---------------------------------------------------------------------------
# Colab proxy
# ---------------------------------------------------------------------------

def _process_colab_job(job_id: str, colab_url: str) -> None:
    import requests
    import json
    job = state.jobs[job_id]
    opts = job["options"]
    file_path = job["file_path"]
    
    _append_job_log(job_id, "INFO", f"Forwarding job to Colab Engine at {colab_url}")
    _push_event(job_id, "transcribing", 0.05, "Uploading file to Google Colab...")

    colab_url = colab_url.rstrip("/")
    try:
        with open(file_path, "rb") as f:
            files = {"file": (job.get("original_filename", "audio.wav"), f)}
            data = {
                "model": opts.get("model", "small"),
                "language": opts.get("language", ""),
                "diarize": "true" if opts.get("diarize") else "false",
                "hf_token": opts.get("hf_token", ""),
                "num_speakers": opts.get("num_speakers", "") or "",
                "min_speakers": opts.get("min_speakers", "") or "",
                "max_speakers": opts.get("max_speakers", "") or "",
            }
            resp = requests.post(f"{colab_url}/api/transcribe", files=files, data=data, timeout=3600)
            resp.raise_for_status()
            colab_job_id = resp.json()["job_id"]

        _append_job_log(job_id, "INFO", f"Colab job created: {colab_job_id}. Proxying SSE stream...")
        
        with requests.get(f"{colab_url}/api/jobs/{colab_job_id}/stream", stream=True, timeout=86400) as sse_resp:
            for line in sse_resp.iter_lines():
                if job["cancel_flag"].is_set():
                    try:
                        requests.post(f"{colab_url}/api/jobs/{colab_job_id}/cancel", timeout=10)
                    except Exception:
                        pass
                    _push_event(job_id, "cancelled", 0.0, "Cancelled.")
                    _sync_job_to_db(job_id)
                    return

                if line:
                    decoded = line.decode('utf-8')
                    if decoded.startswith("data: "):
                        event_data = json.loads(decoded[6:])
                        if "heartbeat" in event_data:
                            continue
                        st = event_data.get("status")
                        pr = event_data.get("progress", 0.0)
                        msg = event_data.get("message", "")
                        
                        if st == "done":
                            res_resp = requests.get(f"{colab_url}/api/jobs/{colab_job_id}/result", timeout=60)
                            res_resp.raise_for_status()
                            job["result"] = res_resp.json()
                            _push_event(job_id, "done", 1.0, "Transcription complete.", data=job["result"])
                            _sync_job_to_db(job_id)
                            return
                        elif st in ("error", "cancelled"):
                            job["error"] = msg
                            _push_event(job_id, st, pr, msg)
                            _sync_job_to_db(job_id)
                            return
                        else:
                            _push_event(job_id, st, pr, msg, data=event_data.get("data"))

    except Exception as exc:
        job["error"] = str(exc)
        _append_job_log(job_id, "ERROR", f"Colab proxy failed: {exc}")
        _push_event(job_id, "error", -1, f"Colab engine error: {exc}")
        _sync_job_to_db(job_id)

# ---------------------------------------------------------------------------
# Job processor
# ---------------------------------------------------------------------------

def _process_job(job_id: str) -> None:  # noqa: C901 — complex by necessity
    job = state.jobs[job_id]
    opts = job["options"]
    file_path: str = job["file_path"]

    model = None
    segments_gen = None
    info = None
    pipeline = None
    diarization = None
    stop_first_segment_watchdog = None

    try:
        job_type = job.get("type", "transcribe")

        if job_type == "translate":
            _process_translation_job(job_id)
            return

        if job_type == "analysis":
            _process_analysis_job(job_id)
            return

        if job_type == "semantic_index":
            _process_semantic_index_job(job_id)
            return

        colab_url = opts.get("colab_url")
        if colab_url:
            _process_colab_job(job_id, colab_url)
            return

        _append_job_log(
            job_id, "INFO",
            f"Worker started (transcribe). model={opts['model']}, "
            f"language={opts['language'] or 'auto'}, diarize={opts['diarize']}",
        )

        # ------------------------------------------------------------------
        # Phase 1: load Whisper model
        # ------------------------------------------------------------------
        _push_event(job_id, "loading_model", 0.03, f"Loading model '{opts['model']}'…")
        try:
            model, model_device = _get_whisper_model(opts["model"])
        except Exception as exc:
            _append_job_log(job_id, "WARN", f"Model init failed: {exc}")
            raise

        # ------------------------------------------------------------------
        # Phase 2: transcribe
        # ------------------------------------------------------------------
        _push_event(
            job_id, "transcribing", 0.05,
            "Starting transcription (first progress update may take time on long files/CPU)…",
        )

        # Ensure ffmpeg is available. For diarization we enforce this
        # synchronously because the pipeline relies on ffmpeg to produce a WAV
        # that the torchcodec shim can load reliably.
        if opts.get("diarize"):
            try:
                ffmpeg_path = ffmpeg_helper.get_ffmpeg_path()
            except Exception as exc:
                raise RuntimeError(
                    "FFmpeg is required for diarization but could not be downloaded. "
                    "Check your internet connection, firewall settings, or install ffmpeg manually."
                ) from exc

            if ffmpeg_path is not None:
                os.environ["PATH"] = (
                    str(Path(ffmpeg_path).parent)
                    + os.pathsep
                    + os.environ.get("PATH", "")
                )

            if not shutil.which("ffmpeg"):
                raise RuntimeError(
                    "FFmpeg is required for diarization but was not found. "
                    "Install ffmpeg or allow the app to download it."
                )
        else:
            # Non-diarization jobs can start immediately; ffmpeg will be
            # downloaded in the background for later conversions.
            try:
                ffmpeg_helper.start_background_download()
            except Exception:
                pass

        lang = opts["language"] or None
        use_word_timestamps = os.environ.get("AMICO_WORD_TIMESTAMPS", "0") == "1"
        use_vad_filter = True
        _append_job_log(
            job_id, "INFO",
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
                    job_id, "transcribing", 0.05,
                    f"Still transcribing… waiting for first segment ({waited_seconds}s)",
                )
                if waited_seconds >= max_first_segment_wait_seconds:
                    _append_job_log(
                        job_id, "ERROR",
                        f"First segment timeout after {waited_seconds}s. Aborting job.",
                    )
                    _push_event(
                        job_id, "error", -1,
                        "Transcription timed out before first segment. "
                        "Try a smaller model or split the audio.",
                    )
                    job["cancel_flag"].set()
                    stop_first_segment_watchdog.set()
                    return

        threading.Thread(target=_first_segment_watchdog, daemon=True).start()

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
                        job_id, "WARN",
                        "VAD model asset missing in package; retrying with vad_filter=False",
                    )
                    segments_gen, info = model.transcribe(
                        whisper_input,
                        language=lang,
                        word_timestamps=use_word_timestamps,
                        vad_filter=use_vad_filter,
                    )
                    duration = info.duration or 1.0
                elif model_device == "cpu" or not _is_missing_cuda_runtime_error(exc):
                    raise
                else:
                    _append_job_log(job_id, "WARN", f"GPU transcription failed: {exc}")
                    _push_event(
                        job_id, "transcribing", 0.05,
                        "GPU runtime unavailable. Retrying on CPU…",
                    )
                    model_device = "cpu"
                    from faster_whisper import WhisperModel
                    model = WhisperModel(opts["model"], device=model_device, compute_type="int8")
                    state._cached_model = model
                    state._cached_model_device = model_device
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
                                job_id, "WARN",
                                "VAD model asset missing after CPU fallback; "
                                "retrying with vad_filter=False",
                            )
                            segments_gen, info = model.transcribe(
                                whisper_input,
                                language=lang,
                                word_timestamps=use_word_timestamps,
                                vad_filter=use_vad_filter,
                            )
                        else:
                            raise
                    duration = info.duration or 1.0
            else:
                duration = info.duration or 1.0

            if job.get("status") == "error":
                return

            segments_list: list[dict] = []
            for seg in segments_gen:
                if not first_segment_event.is_set():
                    first_segment_event.set()
                    stop_first_segment_watchdog.set()
                if job["cancel_flag"].is_set():
                    _push_event(job_id, "cancelled", 0.0, "Cancelled.")
                    _sync_job_to_db(job_id)
                    return

                progress = 0.05 + 0.75 * min(seg.end / duration, 1.0)
                seg_dict = {
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
                }
                segments_list.append(seg_dict)
                _push_event(
                    job_id, "transcribing", progress,
                    f"Transcribing… {_ts(seg.end)} / {_ts(duration)}",
                    data={"segment": {
                        "id": seg_dict["id"],
                        "start": seg_dict["start"],
                        "end": seg_dict["end"],
                        "text": seg_dict["text"],
                    }},
                )
        finally:
            stop_first_segment_watchdog.set()

        # ------------------------------------------------------------------
        # Phase 3: diarization (optional)
        # ------------------------------------------------------------------
        speakers: list[str] = []

        if opts["diarize"] and opts.get("hf_token"):
            _push_event(job_id, "diarizing", 0.82, "Running speaker diarization…")

            # Inject shim before pyannote import so the real torchcodec C
            # extension is never attempted (fails in Docker/PyInstaller).
            inject_torchcodec_shim()

            # Ensure pyannote model assets are cached (will raise clear
            # errors if Hugging Face token or downloader is unavailable).
            try:
                try:
                    from backend import resource_downloader as _rd
                except ImportError:
                    import resource_downloader as _rd
                _rd.ensure_pyannote_model("pyannote/speaker-diarization-3.1", opts.get("hf_token"))
            except Exception:
                # Defer to pyannote's own error handling if download isn't possible
                pass

            from pyannote.audio import Pipeline as _Pipeline  # noqa: PLC0415

            pipeline = _Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=opts["hf_token"],
            )

            # Fix 1: always produce a dedicated mono 16 kHz WAV for pyannote
            # so both decode paths (Whisper + diarization) are identical.
            diarization_input = _convert_audio_for_diarization(job_id, file_path)

            # Fix 2: forward the user's speaker-count hints to pyannote.
            num_speakers = opts.get("num_speakers")
            min_speakers = opts.get("min_speakers")
            max_speakers = opts.get("max_speakers")
            diarization = pipeline(
                diarization_input,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )

            # pyannote >= 3.3 may return a wrapper object (DiarizeOutput,
            # Output, etc.) instead of a bare Annotation.  Rather than
            # hard-coding an attribute name that changes across versions, we
            # probe for the first field that actually has itertracks().
            if not hasattr(diarization, "itertracks"):
                annotation = None
                # Named-tuple path (covers both dataclasses and NamedTuples)
                for field in getattr(diarization, "_fields", []):
                    val = getattr(diarization, field, None)
                    if hasattr(val, "itertracks"):
                        annotation = val
                        break
                # Dataclass / regular object path
                if annotation is None:
                    for val in getattr(diarization, "__dict__", {}).values():
                        if hasattr(val, "itertracks"):
                            annotation = val
                            break
                if annotation is None:
                    raise RuntimeError(
                        f"pyannote returned {type(diarization).__name__} "
                        f"with no itertracks-capable field; attributes: "
                        f"{list(getattr(diarization, '_fields', None) or getattr(diarization, '__dict__', {}).keys())}"
                    )
                diarization = annotation

            for seg in segments_list:
                seg["speaker"] = _assign_speaker(seg["start"], seg["end"], diarization)

            speakers = sorted(set(s["speaker"] for s in segments_list))

        elif opts["diarize"] and not opts.get("hf_token"):
            # Fix 3: warn the user explicitly instead of silently skipping.
            _push_event(
                job_id, "warning", 0.82,
                "Diarization skipped: no Hugging Face token provided. "
                "Add your token in Settings to enable speaker identification.",
            )
            _append_job_log(
                job_id, "WARN",
                "Diarization requested but hf_token is missing; skipping.",
            )

        # ------------------------------------------------------------------
        # Phase 4: done
        # ------------------------------------------------------------------
        result = {
            "language": info.language or "",
            "duration": round(duration, 3),
            "num_segments": len(segments_list),
            "speakers": speakers,
            "segments": segments_list,
        }
        job["result"] = result
        _push_event(job_id, "done", 1.0, "Transcription complete.", data=result)
        _sync_job_to_db(job_id)
        _append_job_log(job_id, "INFO", "Worker finished successfully.")

    except Exception as exc:  # noqa: BLE001
        job["error"] = str(exc)
        _append_job_log(job_id, "ERROR", f"Worker failed: {exc}")
        _append_job_log(job_id, "ERROR", traceback.format_exc())
        _push_event(job_id, "error", -1, str(exc))
        _sync_job_to_db(job_id)
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

        gc.collect()
        _append_job_log(job_id, "INFO", "Worker cleanup complete.")


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------

def _translate_audio_chunk(audio_path: str, start: float, end: float, model_name: str, job_id: str = "internal") -> str:
    """Extract an audio chunk and translate it to English using Whisper."""
    import subprocess
    import shutil
    from faster_whisper import WhisperModel
    from pathlib import Path

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg not found; cannot perform audio translation")

    chunk_path = str(Path(audio_path).with_name(f"chunk_{int(time.time())}_{round(start, 2)}.wav"))
    
    # Extract segment (mono, 16k)
    duration = end - start
    cmd = [
        ffmpeg_bin, "-y", "-v", "error",
        "-ss", str(start), "-t", str(duration),
        "-i", audio_path,
        "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
        chunk_path,
    ]
    
    try:
        subprocess.run(cmd, check=True, timeout=30)
        
        model, _ = _get_whisper_model(model_name)
        segments, _ = model.transcribe(chunk_path, task="translate")
        
        translated_text = " ".join(s.text.strip() for s in segments).strip()
        
        if os.path.exists(chunk_path):
            os.remove(chunk_path)
            
        return translated_text
    except Exception as exc:
        if os.path.exists(chunk_path):
            os.remove(chunk_path)
        return f"Translation error: {exc}"


def _process_translation_job(job_id: str) -> None:
    """Background task to translate all segments in a transcript."""
    from models import Recording, Transcript
    import json as _json
    from db import new_session

    job = state.jobs[job_id]
    recording_id = job["recording_id"]
    model_name = job["options"].get("model", "small")

    try:
        _append_job_log(job_id, "INFO", f"Translation worker started for recording {recording_id}")
        _push_event(job_id, "loading_model", 0.05, f"Loading model '{model_name}'…")
        
        with new_session() as session:
            rec = session.get(Recording, recording_id)
            tr = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
            if not rec or not tr:
                raise ValueError("Recording or Transcript not found")

            data = _json.loads(tr.json_data)
            segments = data.get("segments", [])
            total = len(segments)
            
            if total == 0:
                _push_event(job_id, "done", 1.0, "No segments to translate.")
                return

            _push_event(job_id, "translating", 0.1, f"Found {total} segments. Starting bulk translation…")

            # We pre-load model to cache it.
            _get_whisper_model(model_name)

            translated_count = 0
            for idx, seg in enumerate(segments):
                # Check for cancellation
                if job["cancel_flag"].is_set():
                    _push_event(job_id, "cancelled", 0.0, "Translation cancelled by user.")
                    _append_job_log(job_id, "INFO", "Translation job cancelled.")
                    return

                if not seg.get("edited") and not seg.get("translation"):
                    res = _translate_audio_chunk(rec.file_path, seg["start"], seg["end"], model_name, job_id=job_id)
                    seg["translation"] = res
                    translated_count += 1
                
                # Progress from 0.1 to 0.9
                prog = 0.1 + 0.8 * ((idx + 1) / total)
                _push_event(job_id, "translating", prog, f"Translated {idx+1}/{total} segments…")

            tr.json_data = _json.dumps(data)
            tr.updated_at = time.time()
            session.add(tr)
            session.commit()
            
            _push_event(job_id, "done", 1.0, f"Translation complete. {translated_count} new translations added.")
            _append_job_log(job_id, "INFO", "Translation job finished successfully.")

    except Exception as exc:
        _append_job_log(job_id, "ERROR", f"Translation job failed: {exc}")
        _push_event(job_id, "error", -1, f"Translation failed: {str(exc)}")


# ---------------------------------------------------------------------------
# LLM analysis helpers
# ---------------------------------------------------------------------------

def _build_analysis_prompt(
    analysis_type: str,
    full_text: str,
    target_language: str = "",
    custom_prompt: str = "",
    output_language: str = "",
) -> str:
    """Build the prompt string for a given analysis type."""
    text_block = f"<transcript>\n{full_text}\n</transcript>"
    lang_suffix = f"\n\nPlease respond in {output_language}." if output_language.strip() else ""

    if analysis_type == "summary":
        return (
            "You are a helpful assistant. Provide a clear, concise summary of the "
            "following audio transcript. Focus on the main topics, decisions, and key points.\n\n"
            + text_block + lang_suffix
        )
    elif analysis_type == "action_items":
        return (
            "You are a helpful assistant. Extract all action items, tasks, and to-dos from "
            "the following audio transcript. Format them as a bulleted list. "
            "If there are no action items, say so explicitly.\n\n"
            + text_block + lang_suffix
        )
    elif analysis_type == "translate":
        lang = target_language.strip() or "English"
        return (
            f"You are a professional translator. Translate the following audio transcript "
            f"into {lang}. Preserve the meaning and tone faithfully. "
            f"Output only the translated text, no explanations.\n\n"
            + text_block
        )
    elif analysis_type == "custom":
        return f"{custom_prompt}\n\n{text_block}{lang_suffix}"
    else:
        raise ValueError(f"Unknown analysis_type: {analysis_type!r}")


def _build_suggest_tags_prompt(
    full_text: str,
    existing_tag_names: list,
    applied_tag_names: list,
) -> str:
    """Build the prompt for automatic tag suggestion."""
    existing = ", ".join(existing_tag_names) if existing_tag_names else "none"
    applied = ", ".join(applied_tag_names) if applied_tag_names else "none"
    snippet = full_text[:4000]
    return (
        "You are a tagging assistant. Suggest 3 to 5 concise tags for the transcript below.\n\n"
        f"Existing tags in the library (reuse these exact names when relevant):\n{existing}\n\n"
        f"Already applied to this recording (do NOT suggest these again):\n{applied}\n\n"
        "Rules:\n"
        "- Prefer reusing existing tag names exactly as shown\n"
        "- New tag names must be lowercase and hyphenated (e.g. 'action-items', 'q4-planning')\n"
        "- Output ONLY a JSON array of strings, no markdown fences, no explanation\n"
        "- Example output: [\"meeting\", \"q4-planning\", \"action-items\"]\n\n"
        f"<transcript>\n{snippet}\n</transcript>"
    )


def _suggest_tags_llm(prompt: str, cfg: dict) -> list:
    """Call the LLM (non-streaming) and return a list of suggested tag name strings."""
    import json as _json
    import re as _re
    import requests as _req

    base_url = cfg["llm_base_url"].rstrip("/")
    model_name = cfg["llm_model_name"]
    api_key = cfg.get("llm_api_key", "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = f"{base_url}/v1/chat/completions"
    logger.info("suggest-tags: POST %s model=%s", url, model_name)

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": 200,
    }
    try:
        resp = _req.post(url, json=payload, headers=headers, timeout=30)
    except Exception as exc:
        logger.error("suggest-tags: request failed — %s", exc)
        raise

    logger.info("suggest-tags: response status %s", resp.status_code)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    logger.info("suggest-tags: raw LLM output: %r", content[:300])

    # Robustly extract the first [...] JSON array from the response
    match = _re.search(r'\[.*?\]', content, _re.DOTALL)
    if not match:
        logger.error("suggest-tags: no JSON array in response: %r", content[:200])
        raise ValueError(f"LLM did not return a JSON array. Got: {content[:200]!r}")
    candidates = _json.loads(match.group())
    result = [str(s).strip().lower() for s in candidates if isinstance(s, str) and s.strip()][:5]
    logger.info("suggest-tags: parsed tags: %s", result)
    return result


def _get_embedding(text: str, cfg: dict) -> list:
    """Call Ollama's embedding endpoint and return the embedding vector.

    Tries the newer /api/embed endpoint first (Ollama ≥ 0.5.0); falls back to
    the legacy /api/embeddings endpoint for older installations.
    """
    import requests as _req

    base_url = cfg["llm_base_url"].rstrip("/")
    model_name = cfg.get("embedding_model_name", "nomic-embed-text")
    api_key = cfg.get("llm_api_key", "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def _raise_friendly(r):
        """Raise a clear error, surfacing Ollama's own message when available."""
        try:
            msg = r.json().get("error") or r.text
        except Exception:
            msg = r.text
        if r.status_code == 404 and "not found" in msg.lower():
            raise RuntimeError(
                f"Embedding model '{model_name}' not found in Ollama. "
                f"Run: ollama pull {model_name}"
            )
        r.raise_for_status()

    resp = _req.post(
        f"{base_url}/api/embed",
        json={"model": model_name, "input": text},
        headers=headers,
        timeout=30,
    )

    if resp.status_code == 404:
        body = resp.json() if resp.content else {}
        if "not found" in body.get("error", "").lower():
            _raise_friendly(resp)
        # Endpoint not found — try legacy /api/embeddings (Ollama < 0.5.0)
        resp = _req.post(
            f"{base_url}/api/embeddings",
            json={"model": model_name, "prompt": text},
            headers=headers,
            timeout=30,
        )
        _raise_friendly(resp)
        return resp.json()["embedding"]

    _raise_friendly(resp)
    return resp.json()["embeddings"][0]


def _cosine_similarity(a: list, b: list) -> float:
    """Pure-Python cosine similarity between two equal-length float vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _process_analysis_job(job_id: str) -> None:
    """Background task: call a local LLM and stream the result via SSE."""
    import json as _json
    import requests as _req
    from db import new_session
    from models import Analysis

    job = state.jobs[job_id]
    opts = job["options"]
    analysis_id = job["analysis_id"]

    try:
        _append_job_log(job_id, "INFO", f"Analysis worker started (type={opts['analysis_type']})")
        _push_event(job_id, "running", 0.05, "Building prompt…")

        prompt = _build_analysis_prompt(
            analysis_type=opts["analysis_type"],
            full_text=opts["transcript_full_text"],
            target_language=opts.get("target_language", ""),
            custom_prompt=opts.get("custom_prompt", ""),
            output_language=opts.get("output_language", ""),
        )

        with new_session() as session:
            a = session.get(Analysis, analysis_id)
            if a:
                a.prompt_used = prompt
                session.add(a)
                session.commit()

        _push_event(job_id, "running", 0.10, "Connecting to LLM…")

        base_url = opts["llm_base_url"].rstrip("/")
        model_name = opts["llm_model_name"]
        api_key = opts.get("llm_api_key", "")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }

        collected: list[str] = []

        with _req.post(
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers=headers,
            stream=True,
            timeout=120,
        ) as resp:
            resp.raise_for_status()

            for raw_line in resp.iter_lines():
                if job["cancel_flag"].is_set():
                    _push_event(job_id, "cancelled", 0.0, "Cancelled by user.")
                    _append_job_log(job_id, "INFO", "Analysis job cancelled.")
                    with new_session() as session:
                        a = session.get(Analysis, analysis_id)
                        if a:
                            a.status = "error"
                            a.result_text = "".join(collected)
                            session.add(a)
                            session.commit()
                    return

                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if line.startswith("data: "):
                    line = line[6:]
                if line.strip() == "[DONE]":
                    break

                try:
                    chunk = _json.loads(line)
                except Exception:
                    continue

                delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if delta:
                    collected.append(delta)
                    _push_event(
                        job_id, "streaming", 0.5, "Generating…",
                        data={"chunk": delta, "partial": "".join(collected)},
                    )

        full_result = "".join(collected)

        with new_session() as session:
            a = session.get(Analysis, analysis_id)
            if a:
                a.result_text = full_result
                a.status = "done"
                session.add(a)
                session.commit()

        _push_event(
            job_id, "done", 1.0, "Analysis complete.",
            data={"result_text": full_result, "analysis_id": analysis_id},
        )
        _append_job_log(job_id, "INFO", "Analysis job finished successfully.")

    except Exception as exc:
        _append_job_log(job_id, "ERROR", f"Analysis job failed: {exc}")
        _push_event(job_id, "error", -1, str(exc))
        try:
            from db import new_session
            from models import Analysis as _Analysis
            with new_session() as session:
                a = session.get(_Analysis, analysis_id)
                if a:
                    a.status = "error"
                    session.add(a)
                    session.commit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Semantic index job
# ---------------------------------------------------------------------------

def _process_semantic_index_job(job_id: str) -> None:
    """Background task: embed each transcript segment and store in TranscriptEmbedding."""
    import json as _json
    from db import new_session
    from models import Transcript, TranscriptEmbedding
    from sqlmodel import delete as _delete, select as _select

    job = state.jobs[job_id]
    recording_id = job["recording_id"]
    cfg = job["options"]["llm_cfg"]

    try:
        _push_event(job_id, "indexing", 0.02, "Loading transcript…")

        with new_session() as session:
            tr = session.exec(
                _select(Transcript).where(Transcript.recording_id == recording_id)
            ).first()
            if not tr:
                raise ValueError("Transcript not found")
            data = _json.loads(tr.json_data)
            segments = data.get("segments", [])

        if not segments:
            _push_event(job_id, "done", 1.0, "No segments to index.")
            job["status"] = "done"
            return

        # Delete any stale embeddings for this recording before re-indexing
        with new_session() as session:
            session.exec(
                _delete(TranscriptEmbedding).where(
                    TranscriptEmbedding.recording_id == recording_id
                )
            )
            session.commit()

        total = len(segments)
        model_name = cfg.get("embedding_model_name", "nomic-embed-text")
        embeddings_batch = []

        for idx, seg in enumerate(segments):
            if job["cancel_flag"].is_set():
                _push_event(job_id, "cancelled", 0.0, "Indexing cancelled.")
                job["status"] = "cancelled"
                return

            text = seg.get("text", "").strip()
            if not text:
                continue

            vec = _get_embedding(text, cfg)
            embeddings_batch.append(TranscriptEmbedding(
                recording_id=recording_id,
                segment_index=idx,
                chunk_text=text,
                embedding=_json.dumps(vec),
                model_name=model_name,
            ))

            progress = 0.05 + 0.90 * ((idx + 1) / total)
            _push_event(job_id, "indexing", progress, f"Indexed {idx + 1}/{total} segments…")

        with new_session() as session:
            for emb in embeddings_batch:
                session.add(emb)
            session.commit()

        _push_event(job_id, "done", 1.0, f"Indexed {len(embeddings_batch)} segments.")
        _append_job_log(job_id, "INFO", "Semantic index job finished successfully.")
        job["status"] = "done"

    except Exception as exc:
        _append_job_log(job_id, "ERROR", f"Semantic index job failed: {exc}")
        _push_event(job_id, "error", -1, str(exc))
        job["status"] = "error"
        job["error"] = str(exc)


# ---------------------------------------------------------------------------
# Background worker loop
# ---------------------------------------------------------------------------

def _worker_loop() -> None:
    """Sequentially process jobs from JOB_QUEUE (single thread = no concurrent transcriptions)."""
    while True:
        job_id = state.JOB_QUEUE.get()
        if job_id is None:
            break
        try:
            _process_job(job_id)
        except Exception:  # noqa: BLE001
            pass
        finally:
            state.JOB_QUEUE.task_done()
