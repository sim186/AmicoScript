"""Tests for translation chunk file naming — no collisions, tempfile used."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from unittest.mock import MagicMock, patch
import tempfile


def test_chunk_uses_tempfile(monkeypatch):
    """translate_audio_chunk must use tempfile.mkstemp, not a predictable path."""
    import core.translation as tr_mod

    created_paths = []
    original_mkstemp = tempfile.mkstemp

    def tracking_mkstemp(**kwargs):
        fd, path = original_mkstemp(**kwargs)
        created_paths.append(path)
        return fd, path

    monkeypatch.setattr(tr_mod.tempfile, "mkstemp", tracking_mkstemp)

    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter([MagicMock(text="hello")]), MagicMock())

    import subprocess
    monkeypatch.setattr(tr_mod.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0)))
    monkeypatch.setattr(tr_mod.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    with patch("core.transcription.get_whisper_model", return_value=(fake_model, "cpu")):
        result = tr_mod.translate_audio_chunk("/tmp/audio.wav", 0.0, 5.0, "small")

    assert len(created_paths) >= 1
    # The temp file should not be based on audio_path
    for p in created_paths:
        assert "audio" not in Path(p).name


def test_chunk_file_cleaned_up_on_error(monkeypatch):
    """Temp file must be deleted even when translation fails."""
    import core.translation as tr_mod

    cleanup_calls = []
    original_unlink = os.unlink

    def tracking_unlink(path):
        cleanup_calls.append(path)
        # Don't actually delete since file may not exist in test

    monkeypatch.setattr(tr_mod.os, "unlink", tracking_unlink)
    monkeypatch.setattr(tr_mod.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(tr_mod.subprocess, "run", MagicMock(side_effect=RuntimeError("fail")))
    monkeypatch.setattr(tr_mod.tempfile, "mkstemp", lambda **kw: (0, "/tmp/fake_chunk.wav"))
    monkeypatch.setattr(tr_mod.os, "close", MagicMock())

    result = tr_mod.translate_audio_chunk("/tmp/audio.wav", 0.0, 5.0, "small")

    assert "Translation error" in result
    # Cleanup must have been attempted
    assert any("fake_chunk" in p for p in cleanup_calls)
