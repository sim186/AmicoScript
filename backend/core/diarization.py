"""Diarization phase helpers."""
from typing import Any

from core.audio_utils import _convert_audio_for_diarization
from core.job_helpers import _append_job_log, _push_event
from shims import inject_torchcodec_shim


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

    _push_event(job_id, "diarizing", 0.82, "Running speaker diarization...")

    inject_torchcodec_shim()

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

    diarization = pipeline(
        diarization_input,
        num_speakers=opts.get("num_speakers"),
        min_speakers=opts.get("min_speakers"),
        max_speakers=opts.get("max_speakers"),
    )

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
