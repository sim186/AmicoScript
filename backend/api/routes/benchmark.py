"""Benchmark endpoint — measures Whisper inference speed across standard models."""

from __future__ import annotations

import gc
import platform
import time
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

_BENCHMARK_MODELS = ["tiny", "small", "medium"]

# JFK "Ask not what your country can do for you" — 11s public-domain clip
# Used as the canonical reference audio in OpenAI's Whisper test suite.
_REFERENCE_URL = "https://github.com/openai/whisper/raw/main/tests/jfk.flac"
_REFERENCE_FILENAME = "jfk.flac"


def _cache_dir() -> Path:
    import os
    root = Path(os.environ.get("AMICO_CACHE_DIR") or Path.home() / ".cache" / "amicoscript")
    return root / "benchmark"


def _ensure_reference_audio() -> Path:
    dest = _cache_dir() / _REFERENCE_FILENAME
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(_REFERENCE_URL, dest)
    return dest


def _get_cpu_name() -> str:
    import subprocess
    system = platform.system()
    try:
        if system == "Darwin":
            # Intel Mac: machdep.cpu.brand_string works; Apple Silicon: falls back to hw.model
            for key in ("machdep.cpu.brand_string", "hw.model"):
                r = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=3)
                name = r.stdout.strip()
                if name:
                    return name
        elif system == "Linux":
            try:
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if "model name" in line.lower():
                            return line.split(":", 1)[1].strip()
            except OSError:
                pass
            r = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=3)
            for line in r.stdout.splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or platform.machine() or "unknown"


def _get_ram_gb() -> float | None:
    import subprocess
    system = platform.system()
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except ImportError:
        pass
    try:
        if system == "Darwin":
            r = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3)
            return round(int(r.stdout.strip()) / (1024 ** 3), 1)
        elif system == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) / (1024 ** 2), 1)
    except Exception:
        pass
    return None


def _os_display() -> str:
    system = platform.system()
    return "macOS" if system == "Darwin" else system


def _collect_system_info() -> dict:
    import os
    info: dict = {
        "cpu": _get_cpu_name(),
        "os": _os_display(),
        "arch": platform.machine(),
        "cpu_cores": os.cpu_count(),
        "ram_gb": _get_ram_gb(),
    }
    try:
        import psutil
        info["cpu_cores"] = psutil.cpu_count(logical=False) or info["cpu_cores"]
    except ImportError:
        pass
    try:
        import torch
        info["torch_version"] = torch.__version__
        if torch.cuda.is_available():
            info["cuda"] = True
            info["gpu"] = torch.cuda.get_device_name(0)
        else:
            info["cuda"] = False
            info["gpu"] = None
    except Exception:
        info["cuda"] = False
        info["gpu"] = None
        info["torch_version"] = None
    try:
        import faster_whisper
        info["fw_version"] = faster_whisper.__version__
    except Exception:
        info["fw_version"] = None
    return info


def _evict_model_cache() -> None:
    import state
    with state._model_lock:
        if state._cached_model is not None:
            del state._cached_model
            state._cached_model = None
            state._cached_model_key = None
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass


def _benchmark_model(model_name: str, audio_path: Path) -> dict:
    from core.transcription import _get_whisper_model

    _evict_model_cache()

    t0 = time.perf_counter()
    model, _ = _get_whisper_model(model_name, compute_type="int8", device="auto")
    load_time = time.perf_counter() - t0

    t1 = time.perf_counter()
    segments_gen, info = model.transcribe(
        str(audio_path),
        language="en",
        beam_size=5,
        vad_filter=False,
    )
    # Consume generator — inference runs lazily during iteration
    list(segments_gen)
    transcribe_time = time.perf_counter() - t1

    audio_duration = info.duration or 1.0
    elapsed = load_time + transcribe_time
    return {
        "model": model_name,
        "load_time_s": round(load_time, 3),
        "transcribe_time_s": round(transcribe_time, 3),
        "elapsed_s": round(elapsed, 3),
        "audio_duration_s": round(audio_duration, 3),
        "rtf": round(transcribe_time / audio_duration, 4),
    }


@router.post("/api/benchmark/run")
def run_benchmark() -> dict:
    import datetime
    import state

    # Refuse if a transcription job is actively running
    active = [
        j for j in state.jobs.values()
        if j.get("status") in {"queued", "transcribing", "diarizing", "loading_model"}
    ]
    if active:
        raise HTTPException(
            status_code=409,
            detail="A transcription job is running. Wait for it to finish before benchmarking.",
        )

    try:
        audio_path = _ensure_reference_audio()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch reference audio: {exc}")

    system_info = _collect_system_info()
    results: list[dict] = []

    t_total_start = time.perf_counter()
    for model_name in _BENCHMARK_MODELS:
        try:
            results.append(_benchmark_model(model_name, audio_path))
        except Exception as exc:
            results.append({"model": model_name, "error": str(exc)})
    total_elapsed_s = round(time.perf_counter() - t_total_start, 3)

    # Evict after benchmark so normal transcription starts fresh
    _evict_model_cache()

    return {
        "system": system_info,
        "results": results,
        "total_elapsed_s": total_elapsed_s,
        "reference_audio": _REFERENCE_FILENAME,
        "date": datetime.date.today().isoformat(),
    }
