"""Diarization phase helpers."""
import gc
from typing import Any

import state
from core.audio_utils import convert_audio_for_diarization
from core.job_helpers import append_job_log, push_event, sync_job_to_db
from shims import inject_torch_load_shim, inject_torchcodec_shim
from utils.logging_utils import get_logger

logger = get_logger("amicoscript.diarization")

DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"


_DIARIZATION_STEP_WEIGHTS = {
    "segmentation": 0.45,
    "embeddings": 0.40,
    "clustering": 0.10,
    "discrete_diarization": 0.05,
}
_DIARIZATION_PROGRESS_START = 0.82
_DIARIZATION_PROGRESS_END = 0.95


def resolve_device(requested: str, device_index: int = 0) -> str:
    """Turn a requested device into one torch can actually be handed.

    faster-whisper understands "auto" itself; torch does not, so the choice has
    to be made here. An explicit "cuda" on a machine without one falls back
    rather than raising: the user asked for speed, not for a failed job.
    """
    wanted = (requested or "auto").strip().lower()
    try:
        import torch

        available = bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
    except Exception:
        available = False

    if wanted in {"cpu"}:
        return "cpu"
    if wanted.startswith("cuda") or wanted == "gpu":
        return f"cuda:{device_index}" if available else "cpu"
    # "auto" and anything unrecognised
    return f"cuda:{device_index}" if available else "cpu"


def _evict_cached_pipeline() -> None:
    """Drop the cached pipeline and give its VRAM back."""
    if state._cached_diarization is None:
        return
    state._cached_diarization = None
    state._cached_diarization_device = None
    state._cached_diarization_key = None
    gc.collect()
    try:
        import torch

        if getattr(torch, "cuda", None) and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def reset_pipeline_cache() -> None:
    """Forget the cached pipeline, so the next job loads it again.

    A cached pipeline holds VRAM for the life of the process, and a test that
    stubs pyannote needs the next one to actually reach its stub.
    """
    with state._diarization_lock:
        _evict_cached_pipeline()


def get_diarization_pipeline(
    hf_token: str, device: str = "auto", device_index: int = 0
) -> tuple[Any, str]:
    """Return a (pipeline, active_device) pair, loading and caching as needed.

    Two things this fixes, both of which made diarization far slower than it
    had to be:

    * ``Pipeline.from_pretrained`` returns a pipeline **on the CPU**. Without
      the explicit ``.to(device)`` below, diarization ran on the CPU even on a
      machine where Whisper was happily using the GPU next door.
    * It was reloaded from disk on every job. Whisper has been cached since it
      was written; this now matches.
    """
    from pyannote.audio import Pipeline as _Pipeline

    target = resolve_device(device, device_index)
    # Keyed on the device that was *asked for*, not the one that was reached.
    # When a GPU move fails, the CPU pipeline is remembered under the GPU key,
    # so the next job reuses it instead of reloading the model and failing the
    # same move again — which is the per-job reload this change exists to stop.
    cache_key = (DIARIZATION_MODEL, target)

    with state._diarization_lock:
        if (
            state._cached_diarization is not None
            and state._cached_diarization_key == cache_key
        ):
            return state._cached_diarization, state._cached_diarization_device

        _evict_cached_pipeline()

        import inspect as _inspect

        signature = _inspect.signature(_Pipeline.from_pretrained)
        token_kw = "token" if "token" in signature.parameters else "use_auth_token"
        pipeline = _Pipeline.from_pretrained(
            DIARIZATION_MODEL, **{token_kw: hf_token}
        )

        active = target
        if target != "cpu":
            try:
                import torch

                pipeline = pipeline.to(torch.device(target))
            except Exception:
                # A driver mismatch or too little VRAM: CPU is slow, but it is
                # an answer. Falling back beats failing the whole job.
                logger.exception("Could not move diarization to %s; using the CPU", target)
                active = "cpu"

        state._cached_diarization = pipeline
        state._cached_diarization_device = active
        state._cached_diarization_key = cache_key
        return pipeline, active


def _run_pipeline_with_progress(
    job_id: str,
    pipeline: Any,
    diarization_input: Any,
    opts: dict,
    cancel_flag: Any,
) -> Any:
    """Run pyannote pipeline with step-level progress via its ProgressHook API.

    Falls back to a single blocking call (no progress updates) if the hook
    interface is unavailable in the installed pyannote version.
    """
    span = _DIARIZATION_PROGRESS_END - _DIARIZATION_PROGRESS_START
    completed_weight = 0.0
    step_order: list[str] = []

    def _emit(label: str, fraction_within_step: float) -> None:
        local = completed_weight + _DIARIZATION_STEP_WEIGHTS.get(label, 0.0) * fraction_within_step
        progress = _DIARIZATION_PROGRESS_START + span * min(max(local, 0.0), 1.0)
        push_event(job_id, "diarizing", progress, f"Diarization: {label.replace('_', ' ')}")

    class _ProgressHookAdapter:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __call__(
            self,
            step_name: str,
            step_artifact: Any = None,
            file: Any = None,
            total: int | None = None,
            completed: int | None = None,
        ) -> None:
            nonlocal completed_weight
            if cancel_flag and cancel_flag.is_set():
                raise RuntimeError("Diarization cancelled")
            if step_name not in step_order:
                step_order.append(step_name)
                _emit(step_name, 0.0)
                return
            if total and completed is not None:
                frac = min(max(completed / total, 0.0), 1.0)
                _emit(step_name, frac)
                if completed >= total:
                    completed_weight += _DIARIZATION_STEP_WEIGHTS.get(step_name, 0.0)

    try:
        return pipeline(
            diarization_input,
            num_speakers=opts.get("num_speakers"),
            min_speakers=opts.get("min_speakers"),
            max_speakers=opts.get("max_speakers"),
            hook=_ProgressHookAdapter(),
        )
    except TypeError:
        return pipeline(
            diarization_input,
            num_speakers=opts.get("num_speakers"),
            min_speakers=opts.get("min_speakers"),
            max_speakers=opts.get("max_speakers"),
        )
    except RuntimeError as exc:
        if "cancelled" in str(exc).lower():
            push_event(job_id, "cancelled", 0.0, "Job cancelled during diarization")
            sync_job_to_db(job_id)
            return None
        raise


def assign_speaker(seg_start: float, seg_end: float, diarization: Any) -> str:
    """Return the speaker label with maximum overlap or closest turn fallback."""
    best_speaker = None
    best_overlap = 0.0
    best_dist = float("inf")

    for turn, _, speaker in diarization.itertracks(yield_label=True):
        overlap = max(0.0, min(seg_end, turn.end) - max(seg_start, turn.start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = speaker
        elif best_overlap == 0.0:
            dist = min(abs(seg_start - turn.end), abs(seg_end - turn.start))
            if dist < best_dist:
                best_dist = dist
                best_speaker = speaker

    return best_speaker or "SPEAKER_00"


def _ensure_torch_runtime(job_id: str) -> bool:
    """Make torch and pyannote importable, downloading them if this is the first time.

    Returns False when they cannot be had, in which case the caller skips
    diarization rather than failing the job — the transcript is finished by
    this point, and delivering it without speaker labels beats losing it. That
    is how a missing Hugging Face token is already handled a few lines above.
    """
    import runtime_pack

    try:
        outcome = runtime_pack.ensure(
            progress=lambda message: append_job_log(job_id, "INFO", message)
        )
    except runtime_pack.RuntimePackError as exc:
        append_job_log(job_id, "WARN", f"Diarization skipped: {exc}")
        push_event(job_id, "warning", 0.82, f"Diarization skipped: {exc}")
        return False

    if outcome == "installed":
        append_job_log(job_id, "INFO", "PyTorch runtime installed")
    return True


def run_diarization_phase(job_id: str, segments_list: list[dict], job: dict) -> list[str]:
    """Run pyannote diarization and annotate segment speakers in place."""
    opts = job["options"]
    if not opts.get("diarize"):
        return []

    if not opts.get("hf_token"):
        push_event(
            job_id,
            "warning",
            0.82,
            "Diarization skipped: no Hugging Face token provided. Add your token in Settings.",
        )
        append_job_log(job_id, "WARN", "Diarization requested but hf_token missing; skipping")
        return []

    cancel_flag = job.get("cancel_flag")
    if cancel_flag and cancel_flag.is_set():
        push_event(job_id, "cancelled", 0.0, "Job cancelled before diarization")
        sync_job_to_db(job_id)
        return []

    push_event(job_id, "diarizing", 0.82, "Running speaker diarization...")

    # Before the shims, which import torch, and before anything else on this
    # path does: in the packaged app torch is not in the bundle, and this is
    # what fetches it. Placed after the early returns above so a job that is
    # not going to diarize never downloads a PyTorch runtime.
    if not _ensure_torch_runtime(job_id):
        return []

    inject_torchcodec_shim()
    inject_torch_load_shim()

    try:
        try:
            from backend import resource_downloader as _rd
        except ImportError:
            import resource_downloader as _rd
        _rd.ensure_pyannote_model(DIARIZATION_MODEL, opts.get("hf_token"))
    except Exception:
        pass

    pipeline, active_device = get_diarization_pipeline(
        opts["hf_token"],
        device=opts.get("device", "auto"),
        device_index=int(opts.get("device_index") or 0),
    )
    # Worth saying out loud: diarization on the CPU is the difference between
    # a minute and ten, and this is how a user finds out which they are getting.
    append_job_log(job_id, "INFO", f"Diarization running on {active_device}")
    if active_device == "cpu":
        push_event(
            job_id,
            "diarizing",
            0.82,
            "Running speaker diarization on the CPU — this is much slower than a GPU.",
        )

    diarization_input = convert_audio_for_diarization(job_id, job["file_path"], force=True)

    if cancel_flag and cancel_flag.is_set():
        push_event(job_id, "cancelled", 0.0, "Job cancelled before diarization")
        sync_job_to_db(job_id)
        return []

    diarization = _run_pipeline_with_progress(
        job_id, pipeline, diarization_input, opts, cancel_flag,
    )
    if diarization is None:
        return []

    if cancel_flag and cancel_flag.is_set():
        push_event(job_id, "cancelled", 0.0, "Job cancelled after diarization")
        sync_job_to_db(job_id)
        return []

    if not hasattr(diarization, "itertracks"):
        annotation = None
        for field in getattr(diarization, "_fields", []):
            val = getattr(diarization, field, None)
            if hasattr(val, "itertracks"):
                annotation = val
                break
        if annotation is None:
            for val in getattr(diarization, "__dict__", {}).values():
                if hasattr(val, "itertracks"):
                    annotation = val
                    break
        if annotation is None:
            raise RuntimeError(
                f"pyannote returned {type(diarization).__name__} without itertracks annotation"
            )
        diarization = annotation

    for seg in segments_list:
        seg["speaker"] = assign_speaker(seg["start"], seg["end"], diarization)

    return sorted(set(seg["speaker"] for seg in segments_list))
