"""Finding an NVIDIA GPU without asking torch.

The whole point of this module is that it runs before torch exists, so the
property worth pinning is that every way of failing to find a driver — no
library, a library that will not initialise, a library reporting no devices —
comes back as "no GPU" rather than as an exception on the transcription path.
"""
import ctypes
import subprocess
import sys

import pytest

import gpu_probe


@pytest.fixture(autouse=True)
def _forget_probe_result():
    """The answer is cached for the process, which no test may inherit."""
    gpu_probe.reset_cache()
    yield
    gpu_probe.reset_cache()


class _FakeDriver:
    """A stand-in for libcuda that reports whatever the test wants."""

    def __init__(self, *, init_result: int = 0, count: int = 1, count_result: int = 0):
        self._init_result = init_result
        self._count = count
        self._count_result = count_result

    def cuInit(self, _flags):
        return self._init_result

    def cuDeviceGetCount(self, pointer):
        pointer._obj.value = self._count
        return self._count_result


def _driver(monkeypatch, driver) -> None:
    """Make ctypes.CDLL hand back `driver` (or raise, when it is None)."""

    def _load(name, *_, **__):
        if driver is None:
            raise OSError(f"{name}: cannot open shared object file")
        return driver

    monkeypatch.setattr(ctypes, "CDLL", _load)


def _no_smi(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
    )


# --- the override ------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "yes", "ON"])
def test_the_environment_can_force_a_gpu(monkeypatch, value):
    monkeypatch.setenv("AMICO_GPU", value)
    assert gpu_probe.has_nvidia_gpu() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "OFF"])
def test_the_environment_can_force_no_gpu(monkeypatch, value):
    """The escape hatch for a machine whose driver is present but broken."""
    monkeypatch.setenv("AMICO_GPU", value)
    monkeypatch.setattr(gpu_probe, "_driver_device_count", lambda: 4)
    assert gpu_probe.has_nvidia_gpu() is False


def test_an_unset_override_is_not_an_answer(monkeypatch):
    monkeypatch.setenv("AMICO_GPU", "")
    monkeypatch.setattr(gpu_probe, "_driver_device_count", lambda: 2)
    assert gpu_probe.has_nvidia_gpu() is True


# --- the driver --------------------------------------------------------------


def test_no_driver_library_means_no_gpu(monkeypatch):
    monkeypatch.delenv("AMICO_GPU", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    _driver(monkeypatch, None)
    _no_smi(monkeypatch)

    assert gpu_probe.has_nvidia_gpu() is False
    assert gpu_probe.describe()["detected_by"] is None


def test_a_driver_that_reports_a_device_is_a_gpu(monkeypatch):
    monkeypatch.delenv("AMICO_GPU", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    _driver(monkeypatch, _FakeDriver(count=2))

    found = gpu_probe.describe()
    assert found["available"] is True
    assert found["device_count"] == 2
    assert found["detected_by"] == "libcuda"


def test_a_driver_that_will_not_initialise_is_not_a_gpu(monkeypatch):
    """A container started without --gpus has the library and no device."""
    monkeypatch.delenv("AMICO_GPU", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    _driver(monkeypatch, _FakeDriver(init_result=100))
    _no_smi(monkeypatch)

    assert gpu_probe.has_nvidia_gpu() is False


def test_a_driver_reporting_zero_devices_is_not_a_gpu(monkeypatch):
    monkeypatch.delenv("AMICO_GPU", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    _driver(monkeypatch, _FakeDriver(count=0))
    _no_smi(monkeypatch)

    assert gpu_probe.has_nvidia_gpu() is False


def test_a_driver_that_raises_is_not_fatal(monkeypatch):
    monkeypatch.delenv("AMICO_GPU", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")

    class _Exploding:
        def cuInit(self, _flags):
            raise ctypes.ArgumentError("nonsense")

    _driver(monkeypatch, _Exploding())
    _no_smi(monkeypatch)

    assert gpu_probe.has_nvidia_gpu() is False


def test_macos_is_never_cuda(monkeypatch):
    """No macOS has ever had a CUDA driver, so nothing is probed there."""
    monkeypatch.delenv("AMICO_GPU", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")

    def _explode(*_, **__):
        raise AssertionError("macOS must not probe for a CUDA driver")

    monkeypatch.setattr(ctypes, "CDLL", _explode)
    monkeypatch.setattr(subprocess, "run", _explode)

    assert gpu_probe.has_nvidia_gpu() is False


# --- the fallback ------------------------------------------------------------


def test_nvidia_smi_answers_when_the_library_is_not_on_the_loader_path(monkeypatch):
    """WSL and some packaged drivers leave libcuda somewhere the loader misses."""
    monkeypatch.delenv("AMICO_GPU", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    _driver(monkeypatch, None)

    class _Completed:
        returncode = 0
        stdout = "GPU 0: NVIDIA GeForce RTX 4090 (UUID: GPU-abc)\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed())

    found = gpu_probe.describe()
    assert found["available"] is True
    assert found["device_count"] == 1
    assert found["detected_by"] == "nvidia-smi"


def test_a_failing_nvidia_smi_is_not_a_gpu(monkeypatch):
    monkeypatch.delenv("AMICO_GPU", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    _driver(monkeypatch, None)

    class _Completed:
        returncode = 9
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed())

    assert gpu_probe.has_nvidia_gpu() is False


def test_a_hanging_nvidia_smi_is_not_a_gpu(monkeypatch):
    monkeypatch.delenv("AMICO_GPU", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    _driver(monkeypatch, None)

    def _timeout(*_, **__):
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=10)

    monkeypatch.setattr(subprocess, "run", _timeout)

    assert gpu_probe.has_nvidia_gpu() is False


# --- caching -----------------------------------------------------------------


def test_the_driver_is_asked_once(monkeypatch):
    monkeypatch.delenv("AMICO_GPU", raising=False)
    calls: list = []

    def _count():
        calls.append(1)
        return 1

    monkeypatch.setattr(gpu_probe, "_driver_device_count", _count)

    gpu_probe.has_nvidia_gpu()
    gpu_probe.has_nvidia_gpu()
    gpu_probe.describe()

    assert len(calls) == 1


def test_refresh_asks_again(monkeypatch):
    monkeypatch.delenv("AMICO_GPU", raising=False)
    answers = iter([0, 1])
    monkeypatch.setattr(gpu_probe, "_driver_device_count", lambda: next(answers))
    _no_smi(monkeypatch)

    assert gpu_probe.has_nvidia_gpu() is False
    assert gpu_probe.has_nvidia_gpu(refresh=True) is True
