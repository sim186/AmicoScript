"""Speed work: greedy defaults, batched Whisper decoding, and Parakeet via ONNX."""
import asyncio
import sys
import threading
import types
import wave
from types import SimpleNamespace

import pytest

import state
from core import onnx_asr_engine, runtime_config, transcription
from core.transcription_config import TranscriptionConfig


# --- runtime knobs ------------------------------------------------------------


def test_beam_size_defaults_to_greedy(monkeypatch):
    monkeypatch.delenv("AMICO_BEAM_SIZE", raising=False)
    assert runtime_config.default_beam_size() == 1
    assert TranscriptionConfig().beam_size == 1
    assert TranscriptionConfig().best_of == 1


def test_beam_size_env_override_is_clamped(monkeypatch):
    monkeypatch.setenv("AMICO_BEAM_SIZE", "5")
    assert TranscriptionConfig().beam_size == 5
    monkeypatch.setenv("AMICO_BEAM_SIZE", "999")
    assert runtime_config.default_beam_size() == runtime_config.MAX_BEAM_SIZE
    monkeypatch.setenv("AMICO_BEAM_SIZE", "nope")
    assert runtime_config.default_beam_size() == 1


def test_batch_size_picks_by_device(monkeypatch):
    monkeypatch.delenv("AMICO_BATCH_SIZE", raising=False)
    assert runtime_config.whisper_batch_size(on_gpu=True) == 8
    assert runtime_config.whisper_batch_size(on_gpu=False) == 4


def test_batch_size_env_override(monkeypatch):
    monkeypatch.setenv("AMICO_BATCH_SIZE", "16")
    assert runtime_config.whisper_batch_size(on_gpu=False) == 16
    monkeypatch.setenv("AMICO_BATCH_SIZE", "1")
    assert runtime_config.whisper_batch_size(on_gpu=True) == 1  # batching off
    monkeypatch.setenv("AMICO_BATCH_SIZE", "garbage")
    assert runtime_config.whisper_batch_size(on_gpu=True) == 8
    monkeypatch.setenv("AMICO_BATCH_SIZE", "10000")
    assert runtime_config.whisper_batch_size(on_gpu=True) == runtime_config.MAX_BATCH_SIZE


# --- phase plumbing -----------------------------------------------------------


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text, self.words = start, end, text, []


def _job(model="small", **opts):
    options = {
        "model": model,
        "language": "",
        "diarize": False,
        "word_timestamps": False,
        "vad_filter": True,
        "beam_size": 1,
        "best_of": 1,
        "force_normalize_audio": False,
        **opts,
    }
    return {
        "id": "job-fast",
        "status": "queued",
        "progress": 0.0,
        "message": "",
        "file_path": "/tmp/input.mp3",
        "options": options,
        "sse_queue": asyncio.Queue(),
        "cancel_flag": threading.Event(),
        "logs": [],
        "temp_files": [],
    }


@pytest.fixture()
def phase(monkeypatch):
    events = []
    monkeypatch.setattr(transcription, "push_event", lambda *a, **k: events.append(a))
    monkeypatch.setattr(transcription, "convert_audio_for_transcription", lambda *a, **k: "/tmp/in.wav")
    monkeypatch.setattr(transcription.ffmpeg_helper, "start_background_download", lambda: None)
    monkeypatch.delenv("AMICO_BATCH_SIZE", raising=False)
    return events


def test_whisper_uses_the_batched_pipeline_when_available(monkeypatch, phase):
    calls = {}

    class _Pipeline:
        def transcribe(self, audio, **kwargs):
            calls["batched"] = kwargs
            return iter([_Seg(0.0, 1.0, " hi ")]), SimpleNamespace(duration=1.0, language="en")

    class _Model:
        def transcribe(self, *a, **k):  # pragma: no cover - must not be used
            calls["sequential"] = True
            return iter([]), SimpleNamespace(duration=1.0, language="en")

    state.jobs["job-fast"] = _job()
    monkeypatch.setattr(transcription, "get_whisper_model", lambda *a, **k: (_Model(), "cpu"))
    monkeypatch.setattr(transcription, "_batched_pipeline", lambda model: _Pipeline())

    segments, meta = transcription.run_transcription_phase("job-fast")

    assert [s["text"] for s in segments] == ["hi"]
    assert meta["language"] == "en"
    assert "sequential" not in calls
    assert calls["batched"]["batch_size"] == 4  # CPU default
    assert calls["batched"]["beam_size"] == 1
    assert calls["batched"]["vad_filter"] is True


def test_batching_can_be_turned_off(monkeypatch, phase):
    used = {}

    class _Model:
        def transcribe(self, audio, **kwargs):
            used["sequential"] = kwargs
            return iter([_Seg(0.0, 1.0, "x")]), SimpleNamespace(duration=1.0, language="en")

    monkeypatch.setenv("AMICO_BATCH_SIZE", "1")
    state.jobs["job-fast"] = _job()
    monkeypatch.setattr(transcription, "get_whisper_model", lambda *a, **k: (_Model(), "cpu"))
    monkeypatch.setattr(
        transcription, "_batched_pipeline", lambda model: pytest.fail("batching was off")
    )

    transcription.run_transcription_phase("job-fast")
    assert "batch_size" not in used["sequential"]


def test_missing_vad_asset_falls_back_to_the_sequential_path(monkeypatch, phase):
    seen = []

    class _Pipeline:
        def transcribe(self, audio, **kwargs):
            raise RuntimeError("silero_vad_v6.onnx is missing")

    class _Model:
        def transcribe(self, audio, **kwargs):
            seen.append(kwargs)
            return iter([_Seg(0.0, 1.0, "ok")]), SimpleNamespace(duration=1.0, language="en")

    state.jobs["job-fast"] = _job()
    monkeypatch.setattr(transcription, "get_whisper_model", lambda *a, **k: (_Model(), "cpu"))
    monkeypatch.setattr(transcription, "_batched_pipeline", lambda model: _Pipeline())

    segments, _ = transcription.run_transcription_phase("job-fast")

    assert segments[0]["text"] == "ok"
    assert seen[0]["vad_filter"] is False  # batching needs the VAD, so it is off too


def test_a_real_whisper_model_is_wrapped_and_a_stand_in_is_not():
    pytest.importorskip("faster_whisper")
    assert transcription._batched_pipeline(object()) is None


def test_gpu_detection_reads_the_model_not_the_request():
    gpu = SimpleNamespace(model=SimpleNamespace(device="cuda"))
    cpu = SimpleNamespace(model=SimpleNamespace(device="cpu"))
    assert transcription._model_runs_on_gpu(gpu, "auto") is True
    assert transcription._model_runs_on_gpu(cpu, "auto") is False
    assert transcription._model_runs_on_gpu(object(), "cuda") is True


# --- Parakeet / onnx-asr --------------------------------------------------------


def test_model_ids():
    assert onnx_asr_engine.is_onnx_model("parakeet-v3")
    assert not onnx_asr_engine.is_onnx_model("large-v3")
    assert not onnx_asr_engine.is_onnx_model("")
    assert onnx_asr_engine.whisper_model_for_translation("parakeet-v2") == "small"
    assert onnx_asr_engine.whisper_model_for_translation("medium") == "medium"


def test_the_model_list_offers_what_the_engine_can_run():
    from api.routes.releases import MODELS_META

    ids = {m["id"] for m in MODELS_META}
    assert set(onnx_asr_engine.ONNX_MODELS) <= ids
    assert {"large-v3-turbo", "distil-large-v3"} <= ids


@pytest.fixture()
def fake_onnx_asr(monkeypatch):
    loads = []

    class _Asr:
        def with_vad(self, vad, **kwargs):
            return SimpleNamespace(
                recognize=lambda path: iter(
                    [
                        SimpleNamespace(start=0.0, end=1.5, text=" hello "),
                        SimpleNamespace(start=1.5, end=2.0, text="   "),
                        SimpleNamespace(start=2.0, end=3.0, text="world"),
                    ]
                )
            )

    def load_model(name, **kwargs):
        loads.append(("asr", name, kwargs))
        return _Asr()

    def load_vad(name, **kwargs):
        loads.append(("vad", name, kwargs))
        return object()

    monkeypatch.setitem(
        sys.modules, "onnx_asr", types.SimpleNamespace(load_model=load_model, load_vad=load_vad)
    )
    monkeypatch.setattr(onnx_asr_engine, "is_available", lambda: True)
    monkeypatch.setattr(onnx_asr_engine, "_providers", lambda device: ["CPUExecutionProvider"])
    state._cached_onnx = None
    state._cached_onnx_key = None
    yield loads
    state._cached_onnx = None
    state._cached_onnx_key = None


def test_missing_package_explains_how_to_install_it(monkeypatch):
    monkeypatch.setattr(onnx_asr_engine, "is_available", lambda: False)
    with pytest.raises(onnx_asr_engine.OnnxAsrUnavailable, match=r"amicoscript\[fast\]"):
        onnx_asr_engine.get_onnx_model("parakeet-v3")


def test_onnx_model_is_loaded_once_and_cached(fake_onnx_asr):
    first = onnx_asr_engine.get_onnx_model("parakeet-v3", "cpu")
    second = onnx_asr_engine.get_onnx_model("parakeet-v3", "cpu")

    assert first[0] is second[0]
    assert [kind for kind, *_ in fake_onnx_asr] == ["asr", "vad"]
    kind, name, kwargs = fake_onnx_asr[0]
    assert name == "nemo-parakeet-tdt-0.6b-v3"
    assert kwargs["quantization"] == "int8"  # CPU gets the int8 export


def test_switching_models_reloads(fake_onnx_asr):
    a = onnx_asr_engine.get_onnx_model("parakeet-v3", "cpu")
    b = onnx_asr_engine.get_onnx_model("parakeet-v2", "cpu")
    assert a[0] is not b[0]


def test_iter_segments_drops_blank_text(fake_onnx_asr):
    asr, vad, _ = onnx_asr_engine.get_onnx_model("parakeet-v3", "cpu")
    assert list(onnx_asr_engine.iter_segments(asr, vad, "x.wav")) == [
        (0.0, 1.5, "hello"),
        (2.0, 3.0, "world"),
    ]


def test_wav_duration_and_unreadable_audio(tmp_path):
    good = tmp_path / "a.wav"
    with wave.open(str(good), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 32000)
    assert onnx_asr_engine.wav_duration(str(good)) == pytest.approx(2.0)

    bad = tmp_path / "a.mp3"
    bad.write_bytes(b"not a wav")
    with pytest.raises(onnx_asr_engine.OnnxAsrUnavailable, match="ffmpeg"):
        onnx_asr_engine.wav_duration(str(bad))


def test_parakeet_job_runs_end_to_end(monkeypatch, phase, fake_onnx_asr, tmp_path):
    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 48000)  # 3 s

    normalised = {}

    def _normalise(job_id, path, force=False):
        normalised["force"] = force
        return str(wav)

    monkeypatch.setattr(transcription, "convert_audio_for_transcription", _normalise)
    monkeypatch.setattr(transcription, "get_whisper_model", lambda *a, **k: pytest.fail("Whisper loaded"))
    state.jobs["job-fast"] = _job(model="parakeet-v3", device="cpu", language="it")

    segments, meta = transcription.run_transcription_phase("job-fast")

    assert normalised["force"] is True  # onnx-asr cannot read FLAC or stereo WAV
    assert [s["text"] for s in segments] == ["hello", "world"]
    assert [s["id"] for s in segments] == [0, 1]
    assert segments[0]["words"] == []
    assert meta == {"language": "", "duration": 3.0}
    assert any(a[1] == "transcribing" for a in phase)


def test_parakeet_job_honours_cancellation(monkeypatch, phase, fake_onnx_asr, tmp_path):
    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    monkeypatch.setattr(transcription, "convert_audio_for_transcription", lambda *a, **k: str(wav))
    monkeypatch.setattr(transcription, "sync_job_to_db", lambda _id: None)
    job = _job(model="parakeet-v3", device="cpu")
    job["cancel_flag"].set()
    state.jobs["job-fast"] = job

    segments, meta = transcription.run_transcription_phase("job-fast")

    assert segments == [] and meta == {"cancelled": True}
