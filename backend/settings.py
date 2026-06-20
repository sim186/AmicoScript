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
    """Return whether the external Teams auto-capture watcher is enabled."""
    settings = _load_settings()
    return bool(settings.get("meeting_capture_enabled", False))


def _set_meeting_capture_enabled(enabled: bool) -> None:
    """Persist the Teams auto-capture enabled flag."""
    settings = _load_settings()
    settings["meeting_capture_enabled"] = bool(enabled)
    _save_settings(settings)


def _get_llm_settings() -> dict:
    """Return LLM config: base_url, model_name, api_key."""
    settings = _load_settings()
    return {
        "llm_base_url": settings.get("llm_base_url", "http://localhost:11434"),
        "llm_model_name": settings.get("llm_model_name", ""),
        "llm_api_key": settings.get("llm_api_key", ""),
    }


def _save_llm_settings(base_url: str, model_name: str, api_key: str) -> None:
    """Persist LLM settings to disk."""
    settings = _load_settings()
    settings["llm_base_url"] = base_url
    settings["llm_model_name"] = model_name
    settings["llm_api_key"] = api_key
    _save_settings(settings)
