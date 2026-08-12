"""Linux backend: PulseAudio/PipeWire stream inspection + ``parec`` capture.

The simplest of the three backends, because PulseAudio already answers the
question the watcher asks. ``pactl list sink-inputs`` is a list of applications
playing audio and ``source-outputs`` a list of applications recording — the
per-application view Windows needs COM for and macOS needs a process-object
walk for. Both PulseAudio and PipeWire serve it, since ``pipewire-pulse``
implements the same protocol, so one code path covers essentially every modern
desktop.

Capture is a ``parec`` subprocess per source with its stdout pointed straight at
the raw file. ``parec`` already emits signed 16-bit little-endian interleaved
PCM, which is exactly what the mixer reads back, so there is no Python in the
audio path at all — no ring buffer, no writer thread, no format conversion.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import RawSource, log, run_text

# Tagged onto every capture stream we start, so the detector can recognise its
# own recorders. Without this the watcher's parec shows up as an application
# holding the microphone and the mic heuristic sees a call that never ends.
CLIENT_NAME = "AmicoScript-watcher"

# Captured at the source's own rate rather than the 16 kHz the mixer writes:
# it keeps all three platforms exercising the same resampling path, so a bug
# there cannot hide on Linux alone.
CAPTURE_RATE = int(os.environ.get("AMICOSCRIPT_LINUX_CAPTURE_RATE", "48000"))
CAPTURE_CHANNELS = 2


class PactlUnavailable(RuntimeError):
    pass


def _pactl() -> str | None:
    return shutil.which("pactl")


def parse_streams(payload: str, own_pids: set[str]) -> set[str]:
    """Application names from one ``pactl -f json list …`` document.

    Skips corked (paused) streams — the analogue of Windows' "session is
    Active" test — and anything belonging to this watcher.
    """
    try:
        entries = json.loads(payload)
    except (ValueError, TypeError):
        return set()
    if not isinstance(entries, list):
        return set()

    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("corked") is True:
            continue
        props = entry.get("properties") or {}
        if not isinstance(props, dict):
            continue
        if props.get("application.name") == CLIENT_NAME:
            continue
        if str(props.get("application.process.id", "")) in own_pids:
            continue
        for key in ("application.process.binary", "application.name", "node.name"):
            value = props.get(key)
            if value:
                names.add(str(value).lower())
    return names


class LinuxProbe:
    def __init__(self):
        self._own_pids = {str(os.getpid())}
        self._warned = False

    def track_pid(self, pid: int) -> None:
        """Remember a capture subprocess so it is never mistaken for a call."""
        self._own_pids.add(str(pid))

    def forget_pid(self, pid: int) -> None:
        self._own_pids.discard(str(pid))

    def _list(self, kind: str) -> set[str] | None:
        pactl = _pactl()
        if pactl is None:
            return None
        payload = run_text([pactl, "-f", "json", "list", kind], timeout=2.0)
        if payload is None:
            if not self._warned:
                self._warned = True
                log(f"WARN: `pactl -f json list {kind}` failed — "
                    "detection needs PulseAudio 15+ or pipewire-pulse")
            return None
        return parse_streams(payload, self._own_pids)

    def speaking_procs(self) -> set[str]:
        return self._list("sink-inputs") or set()

    def listening_procs(self) -> set[str] | None:
        return self._list("source-outputs")


def _default_monitor() -> str:
    """The source that carries whatever is playing through the speakers.

    ``@DEFAULT_MONITOR@`` is resolved server-side and follows a default-device
    change mid-call, so it is preferred over a name pinned at startup.
    """
    return os.environ.get("AMICOSCRIPT_LINUX_MONITOR") or "@DEFAULT_MONITOR@"


def _default_source() -> str:
    return os.environ.get("AMICOSCRIPT_LINUX_SOURCE") or "@DEFAULT_SOURCE@"


class _LinuxSession:
    def __init__(self, mix_mic: bool, out_dir: Path, probe: LinuxProbe):
        self._out_dir = out_dir
        self._probe = probe
        self._procs: list[subprocess.Popen] = []
        self._handles: list = []
        self._targets: list[str] = []
        self.sources: list[RawSource] = []

        recorder = shutil.which("parec") or shutil.which("pw-record")
        if recorder is None:
            raise PactlUnavailable(
                "neither parec nor pw-record found — install pulseaudio-utils"
            )
        self._recorder = recorder

        self._add(_default_monitor(), "System audio (monitor)")
        if mix_mic:
            self._add(_default_source(), "Microphone")

    def _add(self, target: str, name: str) -> None:
        self._out_dir.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="capture-", suffix=".raw", dir=str(self._out_dir))
        os.close(fd)
        self.sources.append(RawSource(
            name=name, rate=CAPTURE_RATE, channels=CAPTURE_CHANNELS, path=Path(path),
        ))
        self._targets.append(target)

    def _command(self, target: str) -> list[str]:
        if self._recorder.endswith("pw-record"):
            return [self._recorder, "--target", target, "--format", "s16",
                    "--rate", str(CAPTURE_RATE), "--channels", str(CAPTURE_CHANNELS), "-"]
        return [self._recorder, "--device", target, "--format=s16le",
                f"--rate={CAPTURE_RATE}", f"--channels={CAPTURE_CHANNELS}",
                f"--client-name={CLIENT_NAME}", "--stream-name=meeting-capture"]

    def start(self) -> None:
        env = dict(os.environ)
        # libpulse reads PULSE_PROP, which is what tags pw-record too — the
        # detector filters on this name.
        env["PULSE_PROP"] = f"application.name={CLIENT_NAME} media.role=production"
        for source, target in zip(self.sources, self._targets):
            handle = open(source.path, "wb", buffering=0)
            self._handles.append(handle)
            proc = subprocess.Popen(
                self._command(target), stdout=handle,
                stderr=subprocess.DEVNULL, env=env,
            )
            self._procs.append(proc)
            self._probe.track_pid(proc.pid)
        log("Capture devices: " + ", ".join(self._targets))

    def stop(self) -> None:
        for proc in self._procs:
            self._probe.forget_pid(proc.pid)
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log("WARN: a capture process ignored SIGTERM — killing it")
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
            except Exception as exc:
                log(f"WARN: could not stop a capture process: {exc}")
        self._procs.clear()
        for handle in self._handles:
            try:
                handle.close()
            except Exception:
                pass
        self._handles.clear()

    def health(self) -> str:
        for source in self.sources:
            if source.name.startswith("System audio"):
                try:
                    if source.path.stat().st_size == 0:
                        return SYSTEM_AUDIO_EMPTY
                except OSError:
                    return SYSTEM_AUDIO_EMPTY
        return ""


SYSTEM_AUDIO_EMPTY = (
    "no system audio was captured — the default sink's monitor source produced "
    "nothing. Check that `parec -d @DEFAULT_MONITOR@` works and that the sink "
    "you are listening on is the default one."
)


class LinuxBackend:
    name = "linux"

    def __init__(self):
        self._probe = LinuxProbe()

    def speaking_procs(self) -> set[str]:
        return self._probe.speaking_procs()

    def listening_procs(self) -> set[str] | None:
        return self._probe.listening_procs()

    def capture_blocked(self) -> str:
        if _pactl() is None:
            return ("PulseAudio tools not found — install pulseaudio-utils for "
                    "call detection and recording")
        if shutil.which("parec") is None and shutil.which("pw-record") is None:
            return "no parec or pw-record found — install pulseaudio-utils to record"
        return ""

    def open_session(self, mix_mic: bool, out_dir: Path) -> _LinuxSession:
        blocked = self.capture_blocked()
        if blocked:
            raise PactlUnavailable(blocked)
        return _LinuxSession(mix_mic=mix_mic, out_dir=out_dir, probe=self._probe)


def create_backend() -> LinuxBackend:
    return LinuxBackend()
