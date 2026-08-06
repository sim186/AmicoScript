"""Answer "is there an NVIDIA GPU on this machine?" without importing torch.

Everywhere else in the app that question is answered by
``torch.cuda.is_available()``, which is the right tool when torch is present.
It is the wrong tool here: the runtime pack exists precisely because torch is
no longer inside the bundle, and the answer is what decides which pack to
fetch. Asking torch would be asking the thing we have not installed yet.

So the driver is probed directly instead. ``libcuda`` is installed by the
NVIDIA driver, not by any Python package, so it is present on a GPU machine
that has never seen a CUDA wheel and absent on one that never will. Loading it
only proves a driver is installed, so ``cuInit`` and ``cuDeviceGetCount``
follow: a machine can carry a stale driver with no usable device, and that
machine wants the CPU pack.

Every failure here means "no GPU", never an exception. Being wrong costs a
slower transcription; raising would cost the job.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from typing import Optional

# The driver library, by the name the loader knows it under on each platform.
_DRIVER_LIBRARIES = {
    "win32": ("nvcuda.dll",),
    "linux": ("libcuda.so.1", "libcuda.so"),
}

_CUDA_SUCCESS = 0

# Probing touches the driver, so the answer is remembered for the process.
_cached: Optional[dict] = None


def _forced() -> Optional[bool]:
    """Honour an explicit override, for support cases and for CI.

    AMICO_GPU=0 on a GPU machine is the escape hatch when a driver is present
    but broken; AMICO_GPU=1 is how a build machine without a card can still be
    asked for the CUDA pack.
    """
    raw = os.environ.get("AMICO_GPU", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return None


def _driver_device_count() -> Optional[int]:
    """Devices the CUDA driver reports, or None if there is no driver."""
    if sys.platform == "darwin":
        return None  # No CUDA on macOS, at any version.

    names = _DRIVER_LIBRARIES.get("win32" if sys.platform.startswith("win") else "linux", ())
    for name in names:
        try:
            driver = ctypes.CDLL(name)
        except OSError:
            continue

        try:
            if driver.cuInit(0) != _CUDA_SUCCESS:
                # A driver that will not initialise is a driver that cannot
                # give us a device — a container without --gpus, usually.
                return 0
            count = ctypes.c_int(0)
            if driver.cuDeviceGetCount(ctypes.byref(count)) != _CUDA_SUCCESS:
                return 0
            return int(count.value)
        except Exception:
            return 0
    return None


def _smi_device_count() -> Optional[int]:
    """Devices `nvidia-smi -L` lists, for when libcuda is not on the loader path.

    A WSL or oddly-packaged driver can leave the library somewhere the loader
    does not look while keeping the tool on PATH.
    """
    if sys.platform == "darwin":
        return None
    try:
        completed = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return sum(1 for line in completed.stdout.splitlines() if line.strip().startswith("GPU "))


def describe(refresh: bool = False) -> dict:
    """What was found, and what found it. Cached for the life of the process."""
    global _cached
    if _cached is not None and not refresh:
        return _cached

    override = _forced()
    if override is not None:
        _cached = {
            "available": override,
            "device_count": 1 if override else 0,
            "detected_by": "AMICO_GPU",
        }
        return _cached

    count = _driver_device_count()
    detected_by = "libcuda"
    if not count:
        # None (no driver) and 0 (driver, no device) both get a second opinion:
        # the cheap tool disagrees often enough on WSL to be worth asking.
        smi = _smi_device_count()
        if smi:
            count, detected_by = smi, "nvidia-smi"

    available = bool(count)
    _cached = {
        "available": available,
        "device_count": int(count or 0),
        "detected_by": detected_by if available else None,
    }
    return _cached


def has_nvidia_gpu(refresh: bool = False) -> bool:
    """True when this machine has a CUDA device the driver will admit to."""
    return bool(describe(refresh)["available"])


def reset_cache() -> None:
    """Forget the probe result, so the next call asks the driver again."""
    global _cached
    _cached = None
