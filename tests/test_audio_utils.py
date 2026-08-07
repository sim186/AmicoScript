from types import SimpleNamespace

import state
from core import audio_utils


def test_normalize_audio_skips_wav_for_transcription() -> None:
    result = audio_utils.normalize_audio("job1", "/tmp/input.wav", purpose="transcription", force=False)
    assert result == "/tmp/input.wav"


def test_normalize_audio_returns_original_when_ffmpeg_missing(monkeypatch) -> None:
    monkeypatch.setattr(audio_utils.shutil, "which", lambda _: None)

    logged = []
    monkeypatch.setattr(audio_utils, "append_job_log", lambda job_id, level, msg: logged.append((job_id, level, msg)))

    result = audio_utils.normalize_audio("job2", "/tmp/input.mp3", purpose="transcription", force=True)

    assert result == "/tmp/input.mp3"
    assert any("ffmpeg not found" in msg for _, _, msg in logged)


def test_convert_audio_for_diarization_forces_normalize(monkeypatch) -> None:
    calls = []

    def _fake_normalize(job_id, input_path, purpose, force=False):
        calls.append((job_id, input_path, purpose, force))
        return "/tmp/output.wav"

    monkeypatch.setattr(audio_utils, "normalize_audio", _fake_normalize)

    out = audio_utils.convert_audio_for_diarization("job3", "/tmp/input.mp3")

    assert out == "/tmp/output.wav"
    assert calls == [("job3", "/tmp/input.mp3", "diarization", True)]
