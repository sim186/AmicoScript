"""Render audio waveform as unicode bars for the terminal.

Decodes any audio/video format via ffmpeg to s16le mono PCM, then
downsamples by peak amplitude per bucket. Both raw-levels (for rich
multi-row rendering) and single-line string renderers are exposed.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

BLOCKS = " ▁▂▃▄▅▆▇█"  # 9 levels


def _ffmpeg_bin() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = [
        Path.home() / ".amicoscript" / "data" / "bin" / "ffmpeg",
        Path.cwd() / "amicoscript-data" / "bin" / "ffmpeg",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
        win = c.with_suffix(".exe")
        if win.is_file():
            return str(win)
    return None


def _decode_pcm(path: Path, sr: int = 4000) -> bytes | None:
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [
                ffmpeg, "-v", "quiet", "-nostdin",
                "-i", str(path),
                "-f", "s16le",
                "-acodec", "pcm_s16le",
                "-ac", "1",
                "-ar", str(sr),
                "-",
            ],
            capture_output=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


async def _decode_pcm_async(path: Path, sr: int = 4000) -> bytes | None:
    """Non-blocking ffmpeg decode for use inside event loop."""
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-v", "quiet", "-nostdin",
            "-i", str(path),
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ac", "1",
            "-ar", str(sr),
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    if proc.returncode != 0:
        return None
    return stdout


def _levels_from_samples(samples, width: int) -> list[float]:
    import numpy as np
    if samples.size == 0:
        return []
    if samples.size < width:
        pad = width - samples.size
        samples = np.concatenate([samples, np.zeros(pad, dtype=samples.dtype)])
    bucket = max(1, samples.size // width)
    trimmed = samples[: bucket * width]
    peaks = trimmed.reshape(width, bucket).max(axis=1)
    peak_max = float(peaks.max()) if peaks.size else 0.0
    if peak_max <= 0:
        return [0.0] * width
    return (peaks / peak_max).tolist()


def compute_levels(path: Path | str, width: int = 120) -> list[float]:
    """Decode audio and return per-column peak levels normalized to [0, 1].

    Blocking — for use in threads/executors. See compute_levels_async.
    """
    width = max(8, int(width))
    try:
        import numpy as np
    except Exception:
        return []
    raw = _decode_pcm(Path(path))
    if raw:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        samples = np.abs(samples) / 32768.0
    else:
        try:
            import soundfile as sf
            data, _sr = sf.read(str(path), dtype="float32", always_2d=False)
            if data.ndim > 1:
                data = data.mean(axis=1)
            samples = np.abs(data)
        except Exception:
            return []
    return _levels_from_samples(samples, width)


async def compute_levels_async(
    path: Path | str, width: int = 120
) -> list[float]:
    """Non-blocking variant: async ffmpeg, executor-bounded numpy."""
    width = max(8, int(width))
    try:
        import numpy as np
    except Exception:
        return []
    raw = await _decode_pcm_async(Path(path))
    if not raw:
        # Fall back to blocking soundfile in executor.
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None, lambda: compute_levels(path, width)
            )
        except Exception:
            return []
    loop = asyncio.get_running_loop()

    def _work() -> list[float]:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        samples = np.abs(samples) / 32768.0
        return _levels_from_samples(samples, width)

    return await loop.run_in_executor(None, _work)


def render_waveform(path: Path | str, width: int = 120) -> str:
    """Single-line unicode waveform (legacy)."""
    levels = compute_levels(path, width)
    if not levels:
        return ""
    return "".join(
        BLOCKS[int(min(len(BLOCKS) - 1, round(v * (len(BLOCKS) - 1))))]
        for v in levels
    )


def overlay_cursor(waveform: str, position: float) -> str:
    if not waveform:
        return waveform
    col = max(0, min(len(waveform) - 1, int(position * len(waveform))))
    return waveform[:col] + f"[reverse]{waveform[col]}[/reverse]" + waveform[col + 1 :]
