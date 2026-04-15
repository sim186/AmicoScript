"""Core transcription worker and phase orchestration."""
import asyncio
import gc
import os
import shutil
import threading
from pathlib import Path
from typing import Any

import ffmpeg_helper
import state
from core.audio_utils import _convert_audio_for_transcription
from core.colab_proxy import _handle_colab_job
from core.diarization import _run_diarization_phase
from core.job_helpers import (
    _append_job_log,
    _cleanup_job_temp_files,
    _handle_job_error,
    _push_event,
    _sync_job_to_db,
)
from core.messages import (
    COLAB_UPLOADING,
    TRANSCRIPTION_CANCELLED,
    TRANSCRIPTION_COMPLETE,
    TRANSCRIPTION_GPU_FALLBACK,
    TRANSCRIPTION_LOADING_MODEL,
    TRANSCRIPTION_STARTING,
    TRANSCRIPTION_TIMEOUT_FIRST_SEGMENT,
    TRANSCRIPTION_WAITING_FIRST_SEGMENT,
)
from exports import _ts as format_timestamp


def _is_missing_cuda_runtime_error(exc: Exception) -> bool:
    """Detect missing CUDA runtime errors from Whisper init/inference."""
    message = str(exc).lower()
    markers = ("cublas", "cudnn", "cudart", "cuda", "nvcuda", "libcublas")
    return any(marker in message for marker in markers)


def _is_missing_vad_asset_error(exc: Exception) -> bool:
    """Detect missing bundled Silero VAD file errors."""
    message = str(exc).lower()
    return "silero_vad_v6.onnx" in message or (
        "onnxruntimeerror" in message and "file doesn't exist" in message
    )


def _get_whisper_model(
    model_name: str,
    compute_type: str = "int8",
    device: str = "auto",
    device_index: int = 0,
) -> tuple[Any, str]:
    """Return cached WhisperModel and active device for the provided config."""
    from faster_whisper import WhisperModel

    try:
        from backend import resource_downloader
    except ImportError:
        import resource_downloader

    cache_key = (model_name, compute_type, device, device_index)
    if state._cached_model is not None and getattr(state, "_cached_model_key", None) == cache_key:
        return state._cached_model, state._cached_model_device

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

    requested_device = device
    try:
        try:
            resource_downloader.ensure_whisper_model(model_name)
        except Exception:
            pass
        model = WhisperModel(
            model_name,
            device=requested_device,
            compute_type=compute_type,
            device_index=device_index,
        )
        active_device = requested_device
    except Exception as exc:
        if not _is_missing_cuda_runtime_error(exc):
            raise
        model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
        active_device = "cpu"

    state._cached_model = model
    state._cached_model_name = model_name
    state._cached_model_device = active_device
    state._cached_model_key = cache_key
    return model, active_device


def _run_transcription_phase(job_id: str) -> tuple[list[dict], dict]:
    """Run the Whisper transcription phase and return segments and metadata."""
    job = state.jobs[job_id]
    opts = job["options"]
    file_path = job["file_path"]

    _push_event(job_id, "loading_model", 0.03, TRANSCRIPTION_LOADING_MODEL.format(model=opts["model"]))

    model, model_device = _get_whisper_model(
        opts["model"],
        compute_type=opts.get("compute_type", "int8"),
        device=opts.get("device", "auto"),
        device_index=opts.get("device_index", 0),
    )

    _push_event(
        job_id,
        "transcribing",
        0.05,
        TRANSCRIPTION_STARTING,
    )

    if opts.get("diarize"):
        ffmpeg_path = ffmpeg_helper.get_ffmpeg_path()
        if ffmpeg_path is not None:
            os.environ["PATH"] = str(Path(ffmpeg_path).parent) + os.pathsep + os.environ.get("PATH", "")
        if not shutil.which("ffmpeg"):
            raise RuntimeError(
                "FFmpeg is required for diarization but was not found. Install ffmpeg or allow the app to download it."
            )
    else:
        try:
            ffmpeg_helper.start_background_download()
        except (RuntimeError, OSError):
            pass

    lang = opts.get("language") or None
    use_word_timestamps = bool(opts.get("word_timestamps", os.environ.get("AMICO_WORD_TIMESTAMPS", "0") == "1"))
    use_vad_filter = bool(opts.get("vad_filter", True))

    whisper_input = _convert_audio_for_transcription(
        job_id,
        file_path,
        force=bool(opts.get("force_normalize_audio", False)),
    )

    first_segment_event = threading.Event()
    stop_first_segment_watchdog = threading.Event()

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
                TRANSCRIPTION_WAITING_FIRST_SEGMENT.format(seconds=waited_seconds),
            )
            if waited_seconds >= 600:
                _push_event(
                    job_id,
                    "error",
                    -1,
                    TRANSCRIPTION_TIMEOUT_FIRST_SEGMENT,
                )
                job["cancel_flag"].set()
                stop_first_segment_watchdog.set()
                return

    threading.Thread(target=_first_segment_watchdog, daemon=True).start()

    segments_gen = None
    try:
        beam_size = int(opts.get("beam_size", 5))
        best_of = int(opts.get("best_of", 5))

        try:
            segments_gen, info = model.transcribe(
                whisper_input,
                language=lang,
                word_timestamps=use_word_timestamps,
                vad_filter=use_vad_filter,
                beam_size=beam_size,
                best_of=best_of,
            )
        except Exception as exc:
            if use_vad_filter and _is_missing_vad_asset_error(exc):
                use_vad_filter = False
                _append_job_log(job_id, "WARN", "VAD asset missing; retrying with vad_filter=False")
                segments_gen, info = model.transcribe(
                    whisper_input,
                    language=lang,
                    word_timestamps=use_word_timestamps,
                    vad_filter=False,
                    beam_size=beam_size,
                    best_of=best_of,
                )
            elif model_device != "cpu" and _is_missing_cuda_runtime_error(exc):
                _push_event(job_id, "transcribing", 0.05, TRANSCRIPTION_GPU_FALLBACK)
                model, _ = _get_whisper_model(
                    opts["model"],
                    compute_type=opts.get("compute_type", "int8"),
                    device="cpu",
                    device_index=0,
                )
                segments_gen, info = model.transcribe(
                    whisper_input,
                    language=lang,
                    word_timestamps=use_word_timestamps,
                    vad_filter=use_vad_filter,
                    beam_size=beam_size,
                    best_of=best_of,
                )
            else:
                raise

        duration = info.duration or 1.0
        segments_list: list[dict] = []

        for seg in segments_gen:
            if not first_segment_event.is_set():
                first_segment_event.set()
                stop_first_segment_watchdog.set()

            if job["cancel_flag"].is_set():
                _push_event(job_id, "cancelled", 0.0, TRANSCRIPTION_CANCELLED)
                _sync_job_to_db(job_id)
                return [], {"cancelled": True}

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
                job_id,
                "transcribing",
                progress,
                f"Transcribing... {format_timestamp(seg.end)} / {format_timestamp(duration)}",
                data={
                    "segment": {
                        "id": seg_dict["id"],
                        "start": seg_dict["start"],
                        "end": seg_dict["end"],
                        "text": seg_dict["text"],
                    }
                },
            )

        return segments_list, {"language": info.language or "", "duration": round(duration, 3)}
    finally:
        stop_first_segment_watchdog.set()
        if segments_gen is not None:
            close_fn = getattr(segments_gen, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except (RuntimeError, OSError):
                    pass


def _finalize_transcription_result(
    job_id: str,
    segments_list: list[dict],
    transcription_meta: dict,
    speakers: list[str],
) -> dict:
    """Build final result payload, store it in memory/DB, and emit completion."""
    result = {
        "language": transcription_meta.get("language", ""),
        "duration": transcription_meta.get("duration", 0.0),
        "num_segments": len(segments_list),
        "speakers": speakers,
        "segments": segments_list,
    }
    state.jobs[job_id]["result"] = result
    _push_event(job_id, "done", 1.0, TRANSCRIPTION_COMPLETE, data=result)
    _sync_job_to_db(job_id)
    return result


def _process_job(job_id: str) -> None:
    """Process one queued job by delegating to type-specific handlers."""
    job = state.jobs[job_id]
    try:
        job_type = job.get("type", "transcribe")

        if job_type == "translate":
            from core.translation import _process_translation_job
            _process_translation_job(job_id)
            return

        if job_type == "analysis":
            from core.analysis import _process_analysis_job
            _process_analysis_job(job_id)
            return

        if job["options"].get("colab_url"):
            _handle_colab_job(job_id)
            return

        _append_job_log(
            job_id,
            "INFO",
            (
                f"Worker started (transcribe). model={job['options']['model']}, "
                f"language={job['options'].get('language') or 'auto'}, diarize={job['options'].get('diarize')}"
            ),
        )

        segments_list, transcription_meta = _run_transcription_phase(job_id)
        if transcription_meta.get("cancelled"):
            return

        speakers = _run_diarization_phase(job_id, segments_list, job)
        _finalize_transcription_result(job_id, segments_list, transcription_meta, speakers)
        _append_job_log(job_id, "INFO", "Worker finished successfully")
    except Exception as exc:
        _handle_job_error(job_id, exc)
    finally:
        _cleanup_job_temp_files(job)
        try:
            import torch as _torch
            if hasattr(_torch, "cuda") and _torch.cuda.is_available():
                _torch.cuda.empty_cache()
        except (ImportError, RuntimeError):
            pass
        gc.collect()


def _worker_loop() -> None:
    """Legacy sync worker entrypoint kept for compatibility."""
    raise RuntimeError("Use _worker_loop_async with asyncio.Queue")


async def _worker_loop_async() -> None:
    """Sequentially process jobs from asyncio queue via a single background task."""
    while True:
        job_id = await state.JOB_QUEUE.get()
        try:
            await asyncio.to_thread(_process_job, job_id)
        finally:
            state.JOB_QUEUE.task_done()
