"""Persistent settings for AmicoScript (HF token, etc.).

Settings are stored alongside the data directory so they respect PORTABLE_MODE.
"""
import json
import os
import tempfile
from pathlib import Path


def _settings_file() -> Path:
    """Return the settings file path, respecting PORTABLE_MODE."""
    portable = os.environ.get("AMICOSCRIPT_PORTABLE", "").lower() in ("1", "true", "yes")
    if portable:
        base = Path.cwd() / "amicoscript-data"
    else:
        base = Path.home() / ".amicoscript"
    return base / "settings.json"


def _load_settings() -> dict:
    """Load settings from disk, returning an empty dict on any error."""
    try:
        sf = _settings_file()
        if sf.exists():
            return json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_settings(settings: dict) -> None:
    """Persist settings dict to disk atomically (write-then-rename)."""
    sf = _settings_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(settings, indent=2)
    fd, tmp_path = tempfile.mkstemp(dir=sf.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, sf)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _get_saved_hf_token() -> str:
    """Return the HF token from saved settings or the HF_TOKEN env var."""
    settings = _load_settings()
    return settings.get("hf_token", "") or os.environ.get("HF_TOKEN", "")


def _get_meeting_capture_enabled() -> bool:
    """Return whether the external meeting auto-capture watcher is enabled."""
    settings = _load_settings()
    return bool(settings.get("meeting_capture_enabled", False))


def _set_meeting_capture_enabled(enabled: bool) -> None:
    """Persist the meeting auto-capture enabled flag."""
    settings = _load_settings()
    settings["meeting_capture_enabled"] = bool(enabled)
    _save_settings(settings)


# Defaults for manual uploads *and* the meeting watcher. The web UI persists
# these when the sidebar model/language/diarize controls change; the watcher
# reads them from GET /api/settings so auto-captured meetings match the UI.
_DEFAULT_MODEL = "small"


def _get_transcription_defaults() -> dict:
    """Return Whisper model / language / diarize defaults (diarize off by default)."""
    settings = _load_settings()
    model = (settings.get("default_model") or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    language = str(settings.get("default_language") or "").strip()
    return {
        "default_model": model,
        "default_language": language,
        "default_diarize": bool(settings.get("default_diarize", False)),
    }


def _set_transcription_defaults(
    model: str | None = None,
    language: str | None = None,
    diarize: bool | None = None,
) -> dict:
    """Update any of the transcription defaults; omit a field to leave it unchanged."""
    settings = _load_settings()
    if model is not None:
        cleaned = (model or "").strip() or _DEFAULT_MODEL
        settings["default_model"] = cleaned
    if language is not None:
        settings["default_language"] = (language or "").strip()
    if diarize is not None:
        settings["default_diarize"] = bool(diarize)
    _save_settings(settings)
    return _get_transcription_defaults()


# How much of the model's context window an analysis prompt may occupy. Local
# servers default to small windows (Ollama ships 4096), and exceeding it makes
# the model silently drop the *beginning* of the transcript — so a two-hour
# meeting came back summarised from its last ten minutes with no warning.
# Anything larger than this budget is summarised chunk by chunk instead.
_DEFAULT_CONTEXT_TOKENS = 8192
_DEFAULT_MAX_OUTPUT_TOKENS = 1024


def _get_llm_settings() -> dict:
    """Return LLM config: base_url, model_name, api_key, context budget."""
    settings = _load_settings()

    def _positive_int(key: str, default: int) -> int:
        try:
            value = int(settings.get(key, default))
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    return {
        "llm_base_url": settings.get("llm_base_url", "http://localhost:11434"),
        "llm_model_name": settings.get("llm_model_name", ""),
        "llm_api_key": settings.get("llm_api_key", ""),
        "llm_context_tokens": _positive_int("llm_context_tokens", _DEFAULT_CONTEXT_TOKENS),
        "llm_max_output_tokens": _positive_int(
            "llm_max_output_tokens", _DEFAULT_MAX_OUTPUT_TOKENS
        ),
    }


def _save_llm_settings(
    base_url: str,
    model_name: str,
    api_key: str,
    context_tokens: int | None = None,
    max_output_tokens: int | None = None,
) -> None:
    """Persist LLM settings to disk."""
    settings = _load_settings()
    settings["llm_base_url"] = base_url
    settings["llm_model_name"] = model_name
    settings["llm_api_key"] = api_key
    if context_tokens is not None and context_tokens > 0:
        settings["llm_context_tokens"] = int(context_tokens)
    if max_output_tokens is not None and max_output_tokens > 0:
        settings["llm_max_output_tokens"] = int(max_output_tokens)
    _save_settings(settings)


def _get_auto_summarize_meetings() -> bool:
    """Whether finished meeting captures get summarised without being asked."""
    return bool(_load_settings().get("auto_summarize_meetings", False))


def _set_auto_summarize_meetings(enabled: bool) -> None:
    settings = _load_settings()
    settings["auto_summarize_meetings"] = bool(enabled)
    _save_settings(settings)


def _get_whisper_settings() -> dict:
    """Return Whisper config: model, device, compute_type."""
    settings = _load_settings()
    return {
        "whisper_model": settings.get("whisper_model", "small"),
        "whisper_device": settings.get("whisper_device", "auto"),
        "whisper_compute": settings.get("whisper_compute", "float16"),
    }


def _save_whisper_settings(model: str, device: str, compute: str) -> None:
    """Persist Whisper settings to disk."""
    settings = _load_settings()
    settings["whisper_model"] = model
    settings["whisper_device"] = device
    settings["whisper_compute"] = compute
    _save_settings(settings)
