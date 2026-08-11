"""Which device diarization runs on, and how often it loads the model.

Both of these were bugs rather than design: pyannote's `from_pretrained`
returns a CPU pipeline, so without an explicit `.to()` diarization ran on the
CPU even where Whisper was using the GPU; and it was reloaded on every job.
"""
import sys
import types

import pytest

import state


class _FakeAnnotation:
    def itertracks(self, yield_label=True):
        yield types.SimpleNamespace(start=0.0, end=5.0), None, "SPEAKER_00"


class _FakePipeline:
    """Records the device it was moved to, if any."""

    def __init__(self):
        self.moved_to = None

    def __call__(self, *_, **__):
        return _FakeAnnotation()

    def to(self, device):
        self.moved_to = str(device)
        return self


def _install_fake_pyannote(monkeypatch, loads: list) -> None:
    def _from_pretrained(cls, checkpoint, token=None):
        pipeline = cls()
        loads.append(pipeline)
        return pipeline

    _FakePipeline.from_pretrained = classmethod(_from_pretrained)
    module = types.ModuleType("pyannote.audio")
    module.Pipeline = _FakePipeline
    monkeypatch.setitem(sys.modules, "pyannote", types.ModuleType("pyannote"))
    monkeypatch.setitem(sys.modules, "pyannote.audio", module)


def _fake_torch(monkeypatch, cuda_available: bool) -> None:
    """A torch stand-in, so these tests run on a machine with no GPU."""
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(
        is_available=lambda: cuda_available, empty_cache=lambda: None
    )
    torch.device = lambda spec: f"device({spec})"
    monkeypatch.setitem(sys.modules, "torch", torch)


# --- device resolution -------------------------------------------------------


@pytest.mark.parametrize("requested", ["auto", "cuda", "gpu", ""])
def test_a_gpu_is_used_when_one_is_available(monkeypatch, requested):
    from core import diarization

    _fake_torch(monkeypatch, cuda_available=True)
    assert diarization.resolve_device(requested) == "cuda:0"


@pytest.mark.parametrize("requested", ["auto", "cuda", "gpu", ""])
def test_everything_falls_back_to_cpu_without_a_gpu(monkeypatch, requested):
    """An explicit 'cuda' on a machine without one must not fail the job."""
    from core import diarization

    _fake_torch(monkeypatch, cuda_available=False)
    assert diarization.resolve_device(requested) == "cpu"


def test_cpu_is_honoured_even_where_a_gpu_exists(monkeypatch):
    from core import diarization

    _fake_torch(monkeypatch, cuda_available=True)
    assert diarization.resolve_device("cpu") == "cpu"


def test_the_device_index_is_respected(monkeypatch):
    from core import diarization

    _fake_torch(monkeypatch, cuda_available=True)
    assert diarization.resolve_device("cuda", device_index=2) == "cuda:2"


def test_a_missing_torch_is_not_fatal(monkeypatch):
    """torch is a dependency, but its absence should mean CPU, not a crash."""
    from core import diarization

    monkeypatch.setitem(sys.modules, "torch", None)
    assert diarization.resolve_device("auto") == "cpu"


# --- placement ---------------------------------------------------------------


def test_the_pipeline_is_moved_onto_the_gpu(monkeypatch):
    """The bug this whole change exists for: from_pretrained returns CPU."""
    from core import diarization

    loads: list = []
    _install_fake_pyannote(monkeypatch, loads)
    _fake_torch(monkeypatch, cuda_available=True)

    pipeline, active = diarization.get_diarization_pipeline("hf_x", device="auto")

    assert active == "cuda:0"
    assert pipeline.moved_to == "device(cuda:0)"


def test_the_pipeline_is_left_alone_on_cpu(monkeypatch):
    from core import diarization

    loads: list = []
    _install_fake_pyannote(monkeypatch, loads)
    _fake_torch(monkeypatch, cuda_available=False)

    pipeline, active = diarization.get_diarization_pipeline("hf_x", device="auto")

    assert active == "cpu"
    assert pipeline.moved_to is None


def test_a_failed_move_falls_back_to_cpu_instead_of_failing(monkeypatch):
    """Too little VRAM or a driver mismatch: slow beats broken."""
    from core import diarization

    loads: list = []
    _install_fake_pyannote(monkeypatch, loads)
    _fake_torch(monkeypatch, cuda_available=True)

    def _explode(self, device):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(_FakePipeline, "to", _explode)

    _, active = diarization.get_diarization_pipeline("hf_x", device="cuda")

    assert active == "cpu"


def test_a_failed_move_is_remembered_rather_than_retried_every_job(monkeypatch):
    """Otherwise a machine with a broken GPU reloads the model on every job —
    the exact cost this change removes."""
    from core import diarization

    loads: list = []
    _install_fake_pyannote(monkeypatch, loads)
    _fake_torch(monkeypatch, cuda_available=True)
    monkeypatch.setattr(
        _FakePipeline, "to", lambda self, device: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    for _ in range(3):
        _, active = diarization.get_diarization_pipeline("hf_x", device="cuda")
        assert active == "cpu"

    assert len(loads) == 1


# --- caching -----------------------------------------------------------------


def test_the_pipeline_is_loaded_once_and_reused(monkeypatch):
    from core import diarization

    loads: list = []
    _install_fake_pyannote(monkeypatch, loads)
    _fake_torch(monkeypatch, cuda_available=False)

    first, _ = diarization.get_diarization_pipeline("hf_x")
    second, _ = diarization.get_diarization_pipeline("hf_x")

    assert first is second
    assert len(loads) == 1


def test_changing_device_reloads_the_pipeline(monkeypatch):
    from core import diarization

    loads: list = []
    _install_fake_pyannote(monkeypatch, loads)
    _fake_torch(monkeypatch, cuda_available=True)

    diarization.get_diarization_pipeline("hf_x", device="cpu")
    diarization.get_diarization_pipeline("hf_x", device="cuda")

    assert len(loads) == 2


def test_resetting_the_cache_forces_a_reload(monkeypatch):
    from core import diarization

    loads: list = []
    _install_fake_pyannote(monkeypatch, loads)
    _fake_torch(monkeypatch, cuda_available=False)

    diarization.get_diarization_pipeline("hf_x")
    diarization.reset_pipeline_cache()
    diarization.get_diarization_pipeline("hf_x")

    assert len(loads) == 2
    assert state._cached_diarization is not None  # the second load is cached


# --- the phase uses all of it ------------------------------------------------


def test_the_phase_passes_the_jobs_device_through(monkeypatch):
    """Diarization follows the same device the transcription was given."""
    from core import diarization

    loads: list = []
    _install_fake_pyannote(monkeypatch, loads)
    _fake_torch(monkeypatch, cuda_available=True)
    monkeypatch.setattr(diarization, "inject_torchcodec_shim", lambda: None)
    monkeypatch.setattr(diarization, "inject_torch_load_shim", lambda: None)
    monkeypatch.setattr(diarization, "push_event", lambda *a, **k: None)
    monkeypatch.setattr(
        diarization, "convert_audio_for_diarization", lambda *a, **k: "/tmp/fake.wav"
    )
    logged: list = []
    monkeypatch.setattr(
        diarization, "append_job_log", lambda job_id, level, msg: logged.append(msg)
    )

    segments = [{"start": 0.0, "end": 5.0, "text": "hi", "speaker": ""}]
    job = {
        "options": {
            "diarize": True, "hf_token": "hf_abc", "device": "cuda", "device_index": 1,
        },
        "file_path": "/tmp/whatever.mp3",
    }

    assert diarization.run_diarization_phase("job-1", segments, job) == ["SPEAKER_00"]
    assert loads[0].moved_to == "device(cuda:1)"
    # The user is told which device they got — a CPU run is 10x slower and
    # there was previously no way to tell from the outside.
    assert any("cuda:1" in message for message in logged)


def test_a_second_job_does_not_reload_the_model(monkeypatch):
    from core import diarization

    loads: list = []
    _install_fake_pyannote(monkeypatch, loads)
    _fake_torch(monkeypatch, cuda_available=False)
    monkeypatch.setattr(diarization, "inject_torchcodec_shim", lambda: None)
    monkeypatch.setattr(diarization, "inject_torch_load_shim", lambda: None)
    monkeypatch.setattr(diarization, "push_event", lambda *a, **k: None)
    monkeypatch.setattr(diarization, "append_job_log", lambda *a, **k: None)
    monkeypatch.setattr(
        diarization, "convert_audio_for_diarization", lambda *a, **k: "/tmp/fake.wav"
    )

    job = {
        "options": {"diarize": True, "hf_token": "hf_abc"},
        "file_path": "/tmp/whatever.mp3",
    }
    for _ in range(3):
        diarization.run_diarization_phase(
            "job", [{"start": 0.0, "end": 5.0, "text": "hi", "speaker": ""}], job
        )

    assert len(loads) == 1
