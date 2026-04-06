"""Download-on-demand helpers for large ML model assets.

This module provides lightweight helpers that download required model
artifacts into a cache directory when requested. Heavy libraries are
imported lazily; when missing the functions raise clear RuntimeError
messages so the application can report actionable errors.

Improvements made:
- explicit use of importlib.util.find_spec
- optional progress callback hooks (coarse-grained)
- clearer VAD placeholder with TODO
"""
from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
from typing import Optional, Callable


ProgressCallback = Callable[[int, int], None]


def _cache_root() -> str:
    return os.environ.get("AMICO_CACHE_DIR") or str(Path.home() / ".cache" / "amicoscript")


def ensure_whisper_model(model_name: str, progress_callback: Optional[ProgressCallback] = None) -> str:
    """Ensure the Whisper model assets for `model_name` are present.

    Returns the path to the cached model directory. If `huggingface_hub` is not
    available, raises RuntimeError. `progress_callback` is an optional hook
    called as `progress_callback(done, total)`; currently coarse-grained.
    """
    hf_spec = importlib.util.find_spec("huggingface_hub")
    dest = Path(_cache_root()) / "whisper" / model_name
    if dest.exists():
        return str(dest)
    if hf_spec is None:
        raise RuntimeError("huggingface_hub is required to download Whisper models; install it or pre-bundle models.")

    hf_mod = importlib.import_module("huggingface_hub")
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if progress_callback:
            progress_callback(0, 1)
        hf_mod.snapshot_download(repo_id=model_name, cache_dir=str(dest))
        if progress_callback:
            progress_callback(1, 1)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to download Whisper model {model_name}: {exc}") from exc
    return str(dest)


def ensure_pyannote_model(model_id: str, hf_token: Optional[str] = None, progress_callback: Optional[ProgressCallback] = None) -> str:
    """Ensure a pyannote pretrained model is cached.

    Requires a Hugging Face token for private/model access.
    """
    hf_spec = importlib.util.find_spec("huggingface_hub")
    dest = Path(_cache_root()) / "pyannote" / model_id.replace("/", "_")
    if dest.exists():
        return str(dest)
    if hf_spec is None:
        raise RuntimeError("huggingface_hub is required to download pyannote models; install it or pre-bundle models.")
    if not hf_token:
        raise RuntimeError("Hugging Face token is required to download pyannote models")
    hf_mod = importlib.import_module("huggingface_hub")
    os.environ.setdefault("HUGGINGFACE_HUB_TOKEN", hf_token)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if progress_callback:
            progress_callback(0, 1)
        hf_mod.snapshot_download(repo_id=model_id, cache_dir=str(dest))
        if progress_callback:
            progress_callback(1, 1)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to download pyannote model {model_id}: {exc}") from exc
    return str(dest)


def ensure_vad_asset(name: str) -> str:
    """Ensure a VAD asset (like silero_vad_v6.onnx) is cached.

    This function is a convenience wrapper that will raise a clear
    RuntimeError if downloads are unavailable.

    TODO: Map canonical VAD asset repo or allow configuring a repo id via
    settings/env so this can automatically fetch known ONNX VAD models.
    """
    dest = Path(_cache_root()) / "vad" / name
    if dest.exists():
        return str(dest)
    hf_spec = importlib.util.find_spec("huggingface_hub")
    if hf_spec is None:
        raise RuntimeError("huggingface_hub is required to download VAD assets; install it or pre-package assets.")
    # Placeholder: no authoritative repo mapped yet.
    raise RuntimeError("No automatic VAD asset repo configured; please add asset to cache or implement repo mapping.")
