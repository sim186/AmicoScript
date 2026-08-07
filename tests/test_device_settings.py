"""The saved device/precision settings must actually reach a job.

They were write-only: the settings page offered them, the TUI could set them,
and every transcription used the route's own Form defaults instead.
"""
import sys
import types

import pytest

pytestmark = pytest.mark.usefixtures("no_auth")


def _fake_torch(monkeypatch, cuda_available: bool) -> None:
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(
        is_available=lambda: cuda_available, empty_cache=lambda: None
    )
    torch.device = lambda spec: f"device({spec})"
    monkeypatch.setitem(sys.modules, "torch", torch)


def _options(**overrides) -> dict:
    """The options a job gets when a client submits *overrides* and nothing else."""
    from api.routes.transcription import TranscriptionForm

    return TranscriptionForm(**overrides).to_options()


# --- the saved settings reach a job ------------------------------------------


@pytest.mark.usefixtures("clean_settings")
def test_a_saved_device_is_used_when_the_client_names_none():
    from settings import _save_whisper_settings

    _save_whisper_settings("small", "cuda", "float16")

    opts = _options()

    assert opts["device"] == "cuda"
    assert opts["compute_type"] == "float16"


@pytest.mark.usefixtures("clean_settings")
def test_an_explicit_request_still_beats_the_saved_setting():
    from settings import _save_whisper_settings

    _save_whisper_settings("small", "cuda", "float16")

    opts = _options(device="cpu", compute_type="int8")

    assert opts["device"] == "cpu"
    assert opts["compute_type"] == "int8"


@pytest.mark.usefixtures("clean_settings")
def test_nothing_saved_and_nothing_asked_for_is_auto():
    opts = _options()
    assert opts["device"] == "auto"
    assert opts["compute_type"] == "auto"


# --- saving them ---------------------------------------------------------------


@pytest.mark.usefixtures("clean_settings")
def test_the_device_can_be_saved_on_its_own(client):
    """Saving only the device used to be silently dropped."""
    resp = client.post("/api/settings", data={"whisper_device": "cuda"})

    assert resp.status_code == 200
    assert resp.json()["whisper_device"] == "cuda"
    assert client.get("/api/settings").json()["whisper_device"] == "cuda"


@pytest.mark.usefixtures("clean_settings")
def test_saving_the_device_leaves_the_model_alone(client):
    client.post("/api/settings", data={"whisper_model": "medium"})
    client.post("/api/settings", data={"whisper_device": "cpu"})

    body = client.get("/api/settings").json()
    assert body["whisper_model"] == "medium"
    assert body["whisper_device"] == "cpu"


@pytest.mark.usefixtures("clean_settings")
def test_the_default_precision_is_auto_not_a_fixed_one(client):
    """float16 — the old default — is the wrong choice on a CPU."""
    assert client.get("/api/settings").json()["whisper_compute"] == "auto"


# --- precision resolution ------------------------------------------------------


def test_auto_precision_is_int8_on_a_cpu(monkeypatch):
    from core.transcription import resolve_compute_type

    _fake_torch(monkeypatch, cuda_available=False)
    assert resolve_compute_type("auto", "auto") == "int8"


def test_auto_precision_is_float16_on_a_gpu(monkeypatch):
    from core.transcription import resolve_compute_type

    _fake_torch(monkeypatch, cuda_available=True)
    assert resolve_compute_type("auto", "auto") == "float16"


def test_asking_for_a_cpu_gets_int8_even_where_a_gpu_exists(monkeypatch):
    from core.transcription import resolve_compute_type

    _fake_torch(monkeypatch, cuda_available=True)
    assert resolve_compute_type("auto", "cpu") == "int8"


@pytest.mark.parametrize("pinned", ["int8", "float16", "int8_float16", "float32"])
def test_a_pinned_precision_is_left_alone(monkeypatch, pinned):
    from core.transcription import resolve_compute_type

    _fake_torch(monkeypatch, cuda_available=False)
    assert resolve_compute_type(pinned, "auto") == pinned


def test_an_empty_precision_is_treated_as_auto(monkeypatch):
    from core.transcription import resolve_compute_type

    _fake_torch(monkeypatch, cuda_available=False)
    assert resolve_compute_type("", "auto") == "int8"
