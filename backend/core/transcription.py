"""Core transcription worker and phase orchestration."""
import asyncio
import gc
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import ffmpeg_helper
import state
from core.audio_utils import convert_audio_for_transcription
from core.colab_proxy import handle_colab_job
from core.diarization import run_diarization_phase, resolve_device
from core.job_helpers import (
    append_job_log,
    cleanup_job_temp_files,
    handle_job_error,
    push_event,
    sync_job_to_db,
)
from core.messages import (
    COLAB_UPLOADING,
    DOWNLOAD_PREPARING,
    DOWNLOAD_STARTING,
    TRANSCRIPTION_CANCELLED,
    TRANSCRIPTION_COMPLETE,
    TRANSCRIPTION_GPU_FALLBACK,
    TRANSCRIPTION_LOADING_MODEL,
    TRANSCRIPTION_STARTING,
    TRANSCRIPTION_TIMEOUT_FIRST_SEGMENT,
    TRANSCRIPTION_WAITING_FIRST_SEGMENT,
)
from core.source_downloader import download_source_audio
from db import new_session
from exports import format_timestamp
from models import Recording
from storage import ingest_file


def is_missing_cuda_runtime_error(exc: Exception) -> bool:
    """Detect missing CUDA runtime errors from Whisper init/inference."""
    message = str(exc).lower()
    markers = ("cublas", "cudnn", "cudart", "cuda", "nvcuda", "libcublas")
    return any(marker in message for marker in markers)


def is_missing_vad_asset_error(exc: Exception) -> bool:
    """Detect missing bundled Silero VAD file errors."""
    message = str(exc).lower()
    return "silero_vad_v6.onnx" in message or (
        "onnxruntimeerror" in message and "file doesn't exist" in message
    )


def resolve_compute_type(requested: str, device: str) -> str:
    """Pick a precision when the user has not pinned one.

    float16 is the right choice on a GPU and the wrong one on a CPU, where
    CTranslate2 has to emulate it; int8 is the reverse. "auto" — the default —
    therefore has to be resolved against the device rather than baked into a
    setting, which is what the old fixed default got wrong.
    """
    wanted = (requested or "auto").strip().lower()
    if wanted and wanted != "auto":
        return wanted
    return "int8" if resolve_device(device) == "cpu" else "float16"


def get_whisper_model(
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
    with state._model_lock:
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
            if not is_missing_cuda_runtime_error(exc):
                raise
            model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
            active_device = "cpu"

        state._cached_model = model
        state._cached_model_name = model_name
        state._cached_model_device = active_device
        state._cached_model_key = cache_key
        return model, active_device


def _ensure_cuda_runtime(job_id: str, requested_device: str) -> None:
    """Fetch the CUDA runtime before Whisper looks for it, if this machine wants one.

    Whisper never touches torch, but it does transcribe through CTranslate2,
    which needs cuBLAS and cuDNN — and those ship in the same downloaded pack
    as torch, because on Windows they arrive inside the torch wheel itself and
    cannot be separated from it.

    A machine with no NVIDIA driver, or a job pinned to the CPU, downloads
    nothing. A failure here is not fatal: the CPU path below is the one that
    would have run anyway.
    """
    import runtime_pack

    if not runtime_pack.wants_cuda(requested_device):
        return

    try:
        runtime_pack.ensure(
            progress=lambda message: append_job_log(job_id, "INFO", message),
            prefer_cuda=True,
        )
    except runtime_pack.RuntimePackError as exc:
        append_job_log(job_id, "WARN", f"GPU runtime unavailable ({exc}); using the CPU")


def run_transcription_phase(job_id: str) -> tuple[list[dict], dict]:
    """Run the Whisper transcription phase and return segments and metadata."""
    job = state.jobs[job_id]
    opts = job["options"]
    file_path = job["file_path"]
    current_progress = float(job.get("progress", 0.0) or 0.0)

    push_event(
        job_id,
        "loading_model",
        max(current_progress, 0.03),
        TRANSCRIPTION_LOADING_MODEL.format(model=opts["model"]),
    )

    requested_device = opts.get("device", "auto")
    _ensure_cuda_runtime(job_id, requested_device)
    # After the runtime is in place, not before: resolve_compute_type asks
    # torch what device it can have, and on a GPU machine that question has a
    # different answer once the CUDA libraries exist. Asking first pins int8 on
    # hardware that was about to be able to run float16.
    compute_type = resolve_compute_type(opts.get("compute_type", "auto"), requested_device)
    model, model_device = get_whisper_model(
        opts["model"],
        compute_type=compute_type,
        device=requested_device,
        device_index=opts.get("device_index", 0),
    )

    # Which device this actually landed on was previously invisible: a GPU
    # build quietly falling back to the CPU looked exactly like a slow machine.
    append_job_log(
        job_id, "INFO", f"Transcribing on {model_device} ({compute_type})"
    )
    if model_device == "cpu" and requested_device not in {"cpu"}:
        push_event(
            job_id,
            "loading_model",
            max(current_progress, 0.03),
            "No usable GPU found — transcribing on the CPU, which is much slower.",
        )

    push_event(
        job_id,
        "transcribing",
        max(float(job.get("progress", 0.0) or 0.0), 0.05),
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

    whisper_input = convert_audio_for_transcription(
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
            push_event(
                job_id,
                "transcribing",
                max(float(job.get("progress", 0.0) or 0.0), 0.05),
                TRANSCRIPTION_WAITING_FIRST_SEGMENT.format(seconds=waited_seconds),
            )
            if waited_seconds >= 600:
                push_event(
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
            if use_vad_filter and is_missing_vad_asset_error(exc):
                use_vad_filter = False
                append_job_log(job_id, "WARN", "VAD asset missing; retrying with vad_filter=False")
                segments_gen, info = model.transcribe(
                    whisper_input,
                    language=lang,
                    word_timestamps=use_word_timestamps,
                    vad_filter=False,
                    beam_size=beam_size,
                    best_of=best_of,
                )
            elif model_device != "cpu" and is_missing_cuda_runtime_error(exc):
                push_event(job_id, "transcribing", 0.05, TRANSCRIPTION_GPU_FALLBACK)
                model, _ = get_whisper_model(
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
                push_event(job_id, "cancelled", 0.0, TRANSCRIPTION_CANCELLED)
                sync_job_to_db(job_id)
                return [], {"cancelled": True}

            progress = 0.05 + 0.75 * min(seg.end / duration, 1.0)
            progress = max(float(job.get("progress", 0.0) or 0.0), progress)
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

            push_event(
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


def finalize_transcription_result(
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
    job = state.jobs[job_id]
    job["result"] = result
    push_event(job_id, "done", 1.0, TRANSCRIPTION_COMPLETE, data=result)
    sync_job_to_db(job_id)

    recording_id = job.get("recording_id")
    if recording_id:
        from core.analysis_jobs import maybe_queue_auto_summary
        # Only fires for meeting captures, and only when the user turned it on.
        summary_job = maybe_queue_auto_summary(recording_id)
        if summary_job:
            append_job_log(job_id, "INFO", "Queued automatic summary for this meeting")
            push_event(
                job_id, "done", 1.0, TRANSCRIPTION_COMPLETE,
                data={**result, "auto_summary_job_id": summary_job},
            )
    return result


def _run_download_phase(job_id: str) -> bool:
    """Download source audio and materialize it into managed recording storage."""
    job = state.jobs[job_id]
    source_url = (job.get("source_url") or "").strip()
    if not source_url:
        raise RuntimeError("Missing source URL for download job")

    push_event(job_id, "downloading", 0.01, DOWNLOAD_STARTING)

    from config import STORAGE_ROOT

    download_dir = STORAGE_ROOT / "downloads" / job_id

    def _progress(status: str, progress: float, message: str) -> None:
        if status == "downloading":
            mapped = 0.02 + (0.16 * min(max(progress, 0.0), 1.0))
            push_event(job_id, "downloading", mapped, message)
        elif status == "postprocessing":
            push_event(job_id, "downloading", 0.19, DOWNLOAD_PREPARING)

    cancel_flag = job.get("cancel_flag")

    def _should_cancel() -> bool:
        return bool(cancel_flag and cancel_flag.is_set())

    try:
        downloaded_path, detected_title = download_source_audio(
            source_url, download_dir, on_progress=_progress, should_cancel=_should_cancel,
        )
    except Exception as exc:
        from core.source_downloader import DownloadCancelled
        if isinstance(exc, DownloadCancelled) or _should_cancel():
            push_event(job_id, "cancelled", 0.0, "Job cancelled during download")
            sync_job_to_db(job_id)
            return True
        raise
    if not downloaded_path.exists():
        raise RuntimeError("Downloaded file was not found on disk")

    recording_id = str(job.get("recording_id") or "")
    if not recording_id:
        raise RuntimeError("Missing recording id for download job")

    final_path = ingest_file(downloaded_path, recording_id)
    inferred_name = final_path.name
    if detected_title:
        inferred_name = f"{detected_title}{final_path.suffix}"

    job["file_path"] = str(final_path)
    job["original_filename"] = inferred_name

    try:
        with new_session() as session:
            rec = session.get(Recording, recording_id)
            if rec:
                rec.file_path = str(final_path)
                rec.filename = inferred_name
                rec.status = "queued"
                rec.created_at = rec.created_at or time.time()
                session.add(rec)
                session.commit()
    except Exception:
        append_job_log(job_id, "WARN", "Downloaded file saved but database metadata update failed")

    append_job_log(job_id, "INFO", f"Download completed: {inferred_name}")
    return False


def process_job(job_id: str) -> None:
    """Process one queued job by delegating to type-specific handlers."""
    job = state.jobs[job_id]
    try:
        if job.get("cancel_flag") and job["cancel_flag"].is_set():
            push_event(job_id, "cancelled", 0.0, "Job cancelled before start")
            sync_job_to_db(job_id)
            return

        job_type = job.get("type", "transcribe")

        if job_type == "translate":
            from core.translation import process_translation_job
            process_translation_job(job_id)
            return

        if job_type == "analysis":
            from core.analysis import process_analysis_job
            process_analysis_job(job_id)
            return

        if job_type == "download_transcribe":
            if _consume_download_phase(job_id):
                return
            if job.get("cancel_flag") and job["cancel_flag"].is_set():
                push_event(job_id, "cancelled", 0.0, "Job cancelled after download")
                sync_job_to_db(job_id)
                return

        if job["options"].get("colab_url"):
            handle_colab_job(job_id)
            return

        append_job_log(
            job_id,
            "INFO",
            (
                f"Worker started (transcribe). model={job['options']['model']}, "
                f"language={job['options'].get('language') or 'auto'}, diarize={job['options'].get('diarize')}"
            ),
        )

        segments_list, transcription_meta = run_transcription_phase(job_id)
        if transcription_meta.get("cancelled"):
            return

        speakers = run_diarization_phase(job_id, segments_list, job)
        finalize_transcription_result(job_id, segments_list, transcription_meta, speakers)
        append_job_log(job_id, "INFO", "Worker finished successfully")
    except Exception as exc:
        handle_job_error(job_id, exc)
    finally:
        cleanup_job_temp_files(job)
        try:
            import torch as _torch
            if hasattr(_torch, "cuda") and _torch.cuda.is_available():
                _torch.cuda.empty_cache()
        except (ImportError, RuntimeError):
            pass
        gc.collect()


def worker_loop() -> None:
    """Legacy sync worker entrypoint kept for compatibility."""
    raise RuntimeError("Use worker_loop_async with asyncio.Queue")


# ---------------------------------------------------------------------------
# Download prefetch
# ---------------------------------------------------------------------------
#
# Transcription is serialized on purpose — one Whisper model, one GPU. Fetching
# audio from a URL is not: it is network-bound and touches none of that state.
# Keeping them in the same sequential step meant importing a 30-video playlist
# downloaded video 2 only after video 1 had finished transcribing, so the link
# sat idle for the entire model run.
#
# Downloads now start as soon as a job is queued, bounded by a semaphore, while
# the model stage stays strictly one-at-a-time. The worker awaits a job's
# prefetch before transcribing it, so ordering and error handling are unchanged.

_download_semaphore: asyncio.Semaphore | None = None


def _download_concurrency() -> int:
    try:
        value = int(os.environ.get("AMICOSCRIPT_DOWNLOAD_CONCURRENCY", "2"))
    except ValueError:
        return 2
    return max(1, min(value, 8))


def _get_download_semaphore() -> asyncio.Semaphore:
    global _download_semaphore
    if _download_semaphore is None:
        _download_semaphore = asyncio.Semaphore(_download_concurrency())
    return _download_semaphore


def start_download_prefetch(job_id: str) -> None:
    """Begin downloading *job_id*'s source in the background, if it has one.

    Safe to call from a route handler; does nothing outside a running loop.
    """
    job = state.jobs.get(job_id)
    if not job or job.get("type") != "download_transcribe":
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    job["download_task"] = asyncio.create_task(_prefetch_download(job_id))


async def _prefetch_download(job_id: str) -> bool:
    """Run the download phase in a thread. Returns True if the job is finished."""
    async with _get_download_semaphore():
        job = state.jobs.get(job_id)
        if not job:
            return True
        flag = job.get("cancel_flag")
        if flag and flag.is_set():
            return True
        return await asyncio.to_thread(_run_download_phase, job_id)


async def _await_download_prefetch(job_id: str) -> None:
    """Wait for a prefetch to finish and record its outcome on the job."""
    job = state.jobs.get(job_id)
    task = job.get("download_task") if job else None
    if task is None:
        return
    try:
        job["download_finished_job"] = await task
    except Exception as exc:  # re-raised on the worker thread by _consume_…
        job["download_error"] = exc
    finally:
        job["download_task"] = None
        job["download_prefetched"] = True


def _consume_download_phase(job_id: str) -> bool:
    """Return the download result, running it inline if it was not prefetched.

    Returns True when the job is already finished (cancelled during download).
    """
    job = state.jobs[job_id]
    if not job.get("download_prefetched"):
        return _run_download_phase(job_id)

    error = job.pop("download_error", None)
    if error is not None:
        # Raised here so it lands in process_job's handler, exactly as it did
        # when the download ran inline.
        raise error
    return bool(job.pop("download_finished_job", False))


async def worker_loop_async() -> None:
    """Process jobs from the asyncio queue, one model run at a time."""
    while True:
        job_id = await state.JOB_QUEUE.get()
        try:
            await _await_download_prefetch(job_id)
            await asyncio.to_thread(process_job, job_id)
        finally:
            state.JOB_QUEUE.task_done()
