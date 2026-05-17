"""Tests for pyannote token kwarg compatibility (issue #24)."""
import sys
import types

import pytest


class _FakeAnnotation:
    def itertracks(self, yield_label=True):
        yield types.SimpleNamespace(start=0.0, end=5.0), None, "SPEAKER_00"


def _install_fake_pyannote(monkeypatch, accepted_kwarg: str, captured: dict) -> None:
    """Install a fake `pyannote.audio` module whose Pipeline.from_pretrained
    only accepts the given auth kwarg (`token` or `use_auth_token`).

    The signature is named explicitly (no **kwargs) so `inspect.signature`
    sees the real parameter names — mirroring real pyannote.audio.
    """

    class _FakePipeline:
        def __call__(self, *_, **__):
            return _FakeAnnotation()

    if accepted_kwarg == "token":
        def _from_pretrained(cls, checkpoint, token=None):
            captured["kwarg"] = "token"
            captured["value"] = token
            return cls()
    else:
        def _from_pretrained(cls, checkpoint, use_auth_token=None):
            captured["kwarg"] = "use_auth_token"
            captured["value"] = use_auth_token
            return cls()

    _FakePipeline.from_pretrained = classmethod(_from_pretrained)

    fake_module = types.ModuleType("pyannote.audio")
    fake_module.Pipeline = _FakePipeline
    fake_pkg = types.ModuleType("pyannote")
    monkeypatch.setitem(sys.modules, "pyannote", fake_pkg)
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_module)


@pytest.mark.parametrize("accepted_kwarg", ["token", "use_auth_token"])
def test_run_diarization_uses_supported_kwarg(monkeypatch, accepted_kwarg) -> None:
    captured: dict = {}
    _install_fake_pyannote(monkeypatch, accepted_kwarg, captured)

    from core import diarization

    monkeypatch.setattr(diarization, "inject_torchcodec_shim", lambda: None)
    monkeypatch.setattr(diarization, "_push_event", lambda *a, **k: None)
    monkeypatch.setattr(diarization, "_append_job_log", lambda *a, **k: None)
    monkeypatch.setattr(
        diarization, "_convert_audio_for_diarization", lambda *a, **k: "/tmp/fake.wav"
    )

    segments = [{"start": 0.0, "end": 5.0, "text": "hi", "speaker": ""}]
    job = {
        "options": {"diarize": True, "hf_token": "hf_abc123"},
        "file_path": "/tmp/whatever.mp3",
    }

    speakers = diarization._run_diarization_phase("job-1", segments, job)

    assert captured["kwarg"] == accepted_kwarg
    assert captured["value"] == "hf_abc123"
    assert speakers == ["SPEAKER_00"]
    assert segments[0]["speaker"] == "SPEAKER_00"


def test_run_diarization_skipped_without_token(monkeypatch) -> None:
    from core import diarization

    pushes = []
    monkeypatch.setattr(diarization, "_push_event", lambda *a, **k: pushes.append(a))
    monkeypatch.setattr(diarization, "_append_job_log", lambda *a, **k: None)

    job = {"options": {"diarize": True, "hf_token": ""}, "file_path": "/tmp/x"}
    assert diarization._run_diarization_phase("job-2", [], job) == []
    assert any("warning" in tup for tup in pushes)
