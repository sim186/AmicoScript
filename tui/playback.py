"""Audio playback via system subprocess (afplay/ffplay/aplay).

Minimal — start, stop, status. No precise seek/scrub yet (would require
a controllable player like libmpv). Spacebar toggles play/pause = start
from offset, kill to pause.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path


def _find_player() -> tuple[str, list[str]] | None:
    """Return (binary, base_args). Prefer ffplay (supports -ss seek)."""
    if shutil.which("ffplay"):
        return "ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]
    # Bundled ffplay alongside ffmpeg?
    from .waveform import _ffmpeg_bin
    ff = _ffmpeg_bin()
    if ff:
        ffplay = Path(ff).with_name("ffplay")
        if ffplay.is_file():
            return str(ffplay), ["-nodisp", "-autoexit", "-loglevel", "quiet"]
    if sys.platform == "darwin" and shutil.which("afplay"):
        return "afplay", []
    if sys.platform.startswith("linux"):
        for b in ("paplay", "aplay"):
            if shutil.which(b):
                return b, []
    return None


class Player:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.path: Path | None = None
        self.offset_s: float = 0.0
        self._started_at: float = 0.0
        self._supports_seek: bool = False

    def is_playing(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def elapsed(self) -> float:
        """Seconds since playback started (0 if stopped)."""
        if not self.is_playing():
            return 0.0
        return time.monotonic() - self._started_at

    def position(self) -> float:
        """Approximate playback position in the file, in seconds."""
        base = self.offset_s if self._supports_seek else 0.0
        return base + self.elapsed()

    def play(self, path: Path, offset_s: float = 0.0) -> str | None:
        """Start playback. Return error message or None."""
        self.stop()
        choice = _find_player()
        if choice is None:
            return "no audio player found (install ffplay or use macOS/Linux)"
        binary, args = choice
        cmd = [binary, *args]
        name = Path(binary).name
        if name == "afplay":
            # afplay supports -t (duration); offset via -t not seek. Skip offset.
            cmd.append(str(path))
        elif name.startswith("ffplay"):
            if offset_s > 0:
                cmd.extend(["-ss", f"{offset_s:.2f}"])
            cmd.append(str(path))
        else:
            cmd.append(str(path))
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError as e:
            return f"play failed: {e}"
        self.path = path
        self.offset_s = offset_s
        self._started_at = time.monotonic()
        self._supports_seek = name.startswith("ffplay")
        return None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
