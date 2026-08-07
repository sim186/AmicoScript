import sys
import types

import pytest

import state
from core import transcription


class _FakeWhisperModel:
    created = []

    def __init__(self, model_name, device="auto", compute_type="int8", device_index=0):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.device_index = device_index
        _FakeWhisperModel.created.append((model_name, device, compute_type, device_index))


def _reset_state() -> None:
    state._cached_model = None
    state._cached_model_name = None
    state._cached_model_device = None
    state._cached_model_key = None
    _FakeWhisperModel.created.clear()


@pytest.fixture(autouse=True)
def _setup_fake_modules(monkeypatch):
    _reset_state()

    fake_faster_whisper = types.SimpleNamespace(WhisperModel=_FakeWhisperModel)
    fake_downloader = types.SimpleNamespace(ensure_whisper_model=lambda _: None)

    monkeypatch.setitem(sys.modules, "faster_whisper", fake_faster_whisper)
    monkeypatch.setitem(sys.modules, "backend.resource_downloader", fake_downloader)
    monkeypatch.setitem(sys.modules, "resource_downloader", fake_downloader)

    yield

    _reset_state()


def test_get_whisper_model_uses_cache_for_same_key() -> None:
    model1, device1 = transcription.get_whisper_model("small", compute_type="int8", device="cpu", device_index=0)
    model2, device2 = transcription.get_whisper_model("small", compute_type="int8", device="cpu", device_index=0)

    assert model1 is model2
    assert device1 == device2 == "cpu"
    assert len(_FakeWhisperModel.created) == 1


def test_get_whisper_model_cache_miss_when_config_changes() -> None:
    model1, _ = transcription.get_whisper_model("small", compute_type="int8", device="cpu", device_index=0)
    model2, _ = transcription.get_whisper_model("small", compute_type="float32", device="cpu", device_index=0)

    assert model1 is not model2
    assert len(_FakeWhisperModel.created) == 2
