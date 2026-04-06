"""Persistent settings for AmicoScript (HF token, etc.).

Settings are stored in ~/.amicoscript/settings.json so they survive app
reinstalls and Docker volume mounts.
"""
import json
import os
from pathlib import Path

SETTINGS_DIR = Path.home() / ".amicoscript"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"


def _load_settings() -> dict:
    """Load settings from disk, returning an empty dict on any error."""
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_settings(settings: dict) -> None:
    """Persist settings dict to disk."""
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _get_saved_hf_token() -> str:
    """Return the HF token from saved settings or the HF_TOKEN env var."""
    settings = _load_settings()
    return settings.get("hf_token", "") or os.environ.get("HF_TOKEN", "")


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
