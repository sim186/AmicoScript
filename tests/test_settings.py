import os
from pathlib import Path

import pytest

import backend.settings as settings


def test_llm_settings_persistence(tmp_path, monkeypatch):
    # Point the module at a temporary settings directory
    tmp_dir = tmp_path / "amicoscript_test"
    monkeypatch.setattr(settings, "SETTINGS_DIR", tmp_dir)
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_dir / "settings.json")

    # Ensure starting state is clean
    if (tmp_dir / "settings.json").exists():
        (tmp_dir / "settings.json").unlink()

    # Save LLM settings
    settings._save_llm_settings("http://example:11434", "test-model", "secret-key")

    # Read them back
    cfg = settings._get_llm_settings()
    assert cfg["llm_base_url"] == "http://example:11434"
    assert cfg["llm_model_name"] == "test-model"
    assert cfg["llm_api_key"] == "secret-key"


def test_get_saved_hf_token_from_env(monkeypatch, tmp_path):
    # Ensure no saved settings
    tmp_dir = tmp_path / "amicoscript_test2"
    monkeypatch.setattr(settings, "SETTINGS_DIR", tmp_dir)
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_dir / "settings.json")
    if (tmp_dir / "settings.json").exists():
        (tmp_dir / "settings.json").unlink()

    # Set env var
    monkeypatch.setenv("HF_TOKEN", "env-token")
    assert settings._get_saved_hf_token() == "env-token"

    # If settings file contains hf_token, it should prefer file over env
    settings._save_settings({"hf_token": "file-token"})
    assert settings._get_saved_hf_token() == "file-token"
