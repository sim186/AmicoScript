"""Diarization phase helpers."""
from typing import Any

from core.audio_utils import _convert_audio_for_diarization
from core.job_helpers import _append_job_log, _push_event, _sync_job_to_db
from shims import inject_torch_load_shim, inject_torchcodec_shim


_DIARIZATION_STEP_WEIGHTS = {
    "segmentation": 0.45,
    "embeddings": 0.40,
    "clustering": 0.10,
    "discrete_diarization": 0.05,
}
_DIARIZATION_PROGRESS_START = 0.82
_DIARIZATION_PROGRESS_END = 0.95


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
        _push_event(job_id, "diarizing", progress, f"Diarization: {label.replace('_', ' ')}")

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
            _push_event(job_id, "cancelled", 0.0, "Job cancelled during diarization")
            _sync_job_to_db(job_id)
            return None
        raise


def _assign_speaker(seg_start: float, seg_end: float, diarization: Any) -> str:
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


def _run_diarization_phase(job_id: str, segments_list: list[dict], job: dict) -> list[str]:
    """Run pyannote diarization and annotate segment speakers in place."""
    opts = job["options"]
    if not opts.get("diarize"):
        return []

    if not opts.get("hf_token"):
        _push_event(
            job_id,
            "warning",
            0.82,
            "Diarization skipped: no Hugging Face token provided. Add your token in Settings.",
        )
        _append_job_log(job_id, "WARN", "Diarization requested but hf_token missing; skipping")
        return []

    cancel_flag = job.get("cancel_flag")
    if cancel_flag and cancel_flag.is_set():
        _push_event(job_id, "cancelled", 0.0, "Job cancelled before diarization")
        _sync_job_to_db(job_id)
        return []

    _push_event(job_id, "diarizing", 0.82, "Running speaker diarization...")

    inject_torchcodec_shim()
    inject_torch_load_shim()

    try:
        try:
            from backend import resource_downloader as _rd
        except ImportError:
            import resource_downloader as _rd
        _rd.ensure_pyannote_model("pyannote/speaker-diarization-3.1", opts.get("hf_token"))
    except Exception:
        pass

    from pyannote.audio import Pipeline as _Pipeline

    import inspect as _inspect
    _sig = _inspect.signature(_Pipeline.from_pretrained)
    _token_kw = "token" if "token" in _sig.parameters else "use_auth_token"
    pipeline = _Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        **{_token_kw: opts["hf_token"]},
    )

    diarization_input = _convert_audio_for_diarization(job_id, job["file_path"], force=True)

    if cancel_flag and cancel_flag.is_set():
        _push_event(job_id, "cancelled", 0.0, "Job cancelled before diarization")
        _sync_job_to_db(job_id)
        return []

    diarization = _run_pipeline_with_progress(
        job_id, pipeline, diarization_input, opts, cancel_flag,
    )
    if diarization is None:
        return []

    if cancel_flag and cancel_flag.is_set():
        _push_event(job_id, "cancelled", 0.0, "Job cancelled after diarization")
        _sync_job_to_db(job_id)
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
        seg["speaker"] = _assign_speaker(seg["start"], seg["end"], diarization)

    return sorted(set(seg["speaker"] for seg in segments_list))
