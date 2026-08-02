import json
import os
from pathlib import Path

import pytest

import backend.settings as settings

def _patch_settings_file(monkeypatch, tmp_path):
    sf = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "_settings_file", lambda: sf)
    return sf


def test_llm_settings_persistence(tmp_path, monkeypatch):
    _patch_settings_file(monkeypatch, tmp_path)
    settings._save_llm_settings("http://example:11434", "test-model", "secret-key")
    cfg = settings._get_llm_settings()
    assert cfg["llm_base_url"] == "http://example:11434"
    assert cfg["llm_model_name"] == "test-model"
    assert cfg["llm_api_key"] == "secret-key"


def test_get_saved_hf_token_from_env(monkeypatch, tmp_path):
    _patch_settings_file(monkeypatch, tmp_path)
    monkeypatch.setenv("HF_TOKEN", "env-token")
    assert settings._get_saved_hf_token() == "env-token"
    settings._save_settings({"hf_token": "file-token"})
    assert settings._get_saved_hf_token() == "file-token"


def test_save_settings_atomic(tmp_path, monkeypatch):
    """Write should be atomic: no partial file if process dies mid-write."""
    sf = _patch_settings_file(monkeypatch, tmp_path)
    settings._save_settings({"hf_token": "abc"})
    assert sf.exists()
    # No .tmp file left behind
    assert not any(tmp_path.glob("*.tmp"))
    assert json.loads(sf.read_text())["hf_token"] == "abc"


def test_save_settings_atomic_no_corruption_on_error(tmp_path, monkeypatch):
    """If write fails, original file should be untouched (or not created)."""
    sf = _patch_settings_file(monkeypatch, tmp_path)
    settings._save_settings({"hf_token": "original"})

    import builtins
    real_replace = os.replace

    def fail_replace(src, dst):
        raise OSError("simulated disk full")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        settings._save_settings({"hf_token": "corrupted"})

    # Original file should be unchanged
    assert json.loads(sf.read_text())["hf_token"] == "original"
    # No stale .tmp files
    assert not any(tmp_path.glob("*.tmp"))


def test_settings_respects_portable_mode(tmp_path, monkeypatch):
    """PORTABLE_MODE should use cwd/amicoscript-data."""
    monkeypatch.setenv("AMICOSCRIPT_PORTABLE", "1")
    monkeypatch.chdir(tmp_path)
    sf = settings._settings_file()
    assert sf == tmp_path / "amicoscript-data" / "settings.json"


def test_settings_standard_mode(tmp_path, monkeypatch):
    """Standard mode should use ~/.amicoscript."""
    monkeypatch.delenv("AMICOSCRIPT_PORTABLE", raising=False)
    sf = settings._settings_file()
    assert str(Path.home()) in str(sf)


def test_meeting_capture_enabled_defaults_off(tmp_path, monkeypatch):
    _patch_settings_file(monkeypatch, tmp_path)
    assert settings._get_meeting_capture_enabled() is False


def test_meeting_capture_enabled_persistence(tmp_path, monkeypatch):
    sf = _patch_settings_file(monkeypatch, tmp_path)
    settings._set_meeting_capture_enabled(True)
    assert settings._get_meeting_capture_enabled() is True
    assert json.loads(sf.read_text())["meeting_capture_enabled"] is True
    # Round-trip back to False; other keys must be preserved.
    settings._set_meeting_capture_enabled(False)
    assert settings._get_meeting_capture_enabled() is False
    assert "meeting_capture_enabled" in json.loads(sf.read_text())


def test_meeting_capture_enabled_preserves_other_keys(tmp_path, monkeypatch):
    sf = _patch_settings_file(monkeypatch, tmp_path)
    settings._save_settings({"hf_token": "keep-me", "meeting_capture_enabled": True})
    settings._set_meeting_capture_enabled(False)
    data = json.loads(sf.read_text())
    assert data["hf_token"] == "keep-me"
    assert data["meeting_capture_enabled"] is False
