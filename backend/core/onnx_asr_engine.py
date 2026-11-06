"""Non-Whisper speech recognition through ONNX Runtime (NVIDIA Parakeet).

Whisper is an encoder-decoder that writes its transcript one token at a time.
Parakeet-TDT is a transducer: it emits tokens as it walks the audio and skips
frames it does not need, which is why it runs many times faster than real time
on a plain CPU where Whisper large crawls. It is the model family dictation
tools reach for when latency matters.

``onnx-asr`` runs the exported models on ONNX Runtime, so none of this touches
torch or CTranslate2. It is an optional dependency (the ``fast`` extra): when it
is not installed these models simply are not offered, and picking one raises an
error that says how to install it.

Trade-offs the caller should know about, all surfaced in the UI notes:

* Parakeet v2 is English only; v3 covers 25 European languages and detects the
  language itself, so a language chosen in the UI is not passed through.
* There is no translate-to-English task, so translation jobs fall back to Whisper.
* Only 16 kHz PCM WAV is read directly. The transcription phase normalises
  other formats with ffmpeg first; without ffmpeg these models cannot run on
  an mp3 or m4a.
"""
from __future__ import annotations

import gc
import importlib.util
import wave
from typing import Any, Iterator

import state

#: Model ids the UI offers -> the id onnx-asr loads.
ONNX_MODELS: dict[str, str] = {
    "parakeet-v3": "nemo-parakeet-tdt-0.6b-v3",
    "parakeet-v2": "nemo-parakeet-tdt-0.6b-v2",
}

#: What translation jobs use when the recording was transcribed by one of the
#: models above, which cannot translate.
TRANSLATION_FALLBACK_MODEL = "small"

#: Silero VAD splits long audio into speech chunks the transducer can digest.
#: Capped so a monologue without pauses still gets cut somewhere.
_MAX_SPEECH_SECONDS = 30.0


class OnnxAsrUnavailable(RuntimeError):
    """onnx-asr is not installed, or the model could not be loaded."""


def is_onnx_model(model_name: str | None) -> bool:
    """Whether *model_name* is run by this module instead of faster-whisper."""
    return (model_name or "") in ONNX_MODELS


def whisper_model_for_translation(model_name: str | None) -> str:
    """The Whisper model a translation job should load for *model_name*."""
    if is_onnx_model(model_name):
        return TRANSLATION_FALLBACK_MODEL
    return model_name or TRANSLATION_FALLBACK_MODEL


def is_available() -> bool:
    return importlib.util.find_spec("onnx_asr") is not None


def _providers(device: str) -> list[str] | None:
    """Execution providers for *device*; ``None`` lets onnx-asr choose."""
    if (device or "auto").strip().lower() == "cpu":
        return ["CPUExecutionProvider"]
    try:
        import onnxruntime

        available = onnxruntime.get_available_providers()
    except Exception:
        return None
    if "CUDAExecutionProvider" in available:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def get_onnx_model(model_name: str, device: str = "auto") -> tuple[Any, Any, str]:
    """Return ``(asr, vad, active_device)``, loading and caching on first use.

    The cache is separate from the Whisper one and holds a single entry, like
    it: switching model drops the previous one so two never sit in memory.
    """
    if not is_available():
        raise OnnxAsrUnavailable(
            f"The '{model_name}' model needs the onnx-asr package. "
            "Install it with: pip install 'amicoscript[fast]'"
        )
    import onnx_asr

    providers = _providers(device)
    on_gpu = bool(providers and providers[0] != "CPUExecutionProvider")
    cache_key = (model_name, tuple(providers or ()))

    with state._onnx_lock:
        if state._cached_onnx is not None and state._cached_onnx_key == cache_key:
            asr, vad = state._cached_onnx
            return asr, vad, "cuda" if on_gpu else "cpu"

        state._cached_onnx = None
        state._cached_onnx_key = None
        gc.collect()

        kwargs: dict[str, Any] = {}
        if providers is not None:
            kwargs["providers"] = providers
        repo_id = ONNX_MODELS[model_name]
        try:
            # int8 is the right precision on a CPU and is what the published
            # exports ship; on a GPU the full-precision graph is used.
            asr = onnx_asr.load_model(
                repo_id, quantization=None if on_gpu else "int8", **kwargs
            )
        except Exception:
            asr = onnx_asr.load_model(repo_id, **kwargs)
        vad = onnx_asr.load_vad("silero", **kwargs)

        state._cached_onnx = (asr, vad)
        state._cached_onnx_key = cache_key
        return asr, vad, "cuda" if on_gpu else "cpu"


def wav_duration(path: str) -> float:
    """Length in seconds of a PCM WAV file, or raise ``OnnxAsrUnavailable``."""
    try:
        with wave.open(path, "rb") as handle:
            return handle.getnframes() / float(handle.getframerate() or 16000)
    except (wave.Error, EOFError, OSError) as exc:
        raise OnnxAsrUnavailable(
            "Parakeet models read WAV audio only, and this file could not be "
            "converted. Install ffmpeg (or let the app download it) and retry."
            f" ({exc})"
        ) from exc


def iter_segments(asr: Any, vad: Any, audio_path: str) -> Iterator[tuple[float, float, str]]:
    """Yield ``(start, end, text)`` for each speech segment, as it is decoded.

    The generator is lazy: onnx-asr decodes a VAD chunk at a time, so the first
    segment arrives long before the file is finished and the caller can stream
    progress exactly as it does for Whisper.
    """
    pipeline = asr.with_vad(vad, max_speech_duration_s=_MAX_SPEECH_SECONDS)
    for seg in pipeline.recognize(audio_path):
        text = (getattr(seg, "text", "") or "").strip()
        if text:
            yield float(seg.start), float(seg.end), text
