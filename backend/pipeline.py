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
import os
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

import state
from exports import _ts
from shims import inject_torchcodec_shim


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
        _append_job_log(
            job_id, "INFO",
            f"Worker started. model={opts['model']}, "
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
                    return

                progress = 0.05 + 0.75 * min(seg.end / duration, 1.0)
                _push_event(
                    job_id, "transcribing", progress,
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

        # ------------------------------------------------------------------
        # Phase 3: diarization (optional)
        # ------------------------------------------------------------------
        speakers: list[str] = []

        if opts["diarize"] and opts.get("hf_token"):
            _push_event(job_id, "diarizing", 0.82, "Running speaker diarization…")

            # Inject shim before pyannote import so the real torchcodec C
            # extension is never attempted (fails in Docker/PyInstaller).
            inject_torchcodec_shim()

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
