"""macOS backend: Core Audio process objects + a process tap (macOS 14.2+).

Detection and capture come from the same API family here, which makes this the
simpler of the two audio-API ports:

* **Detection** reads ``kAudioHardwarePropertyProcessObjectList`` and each
  process object's ``IsRunningInput`` / ``IsRunningOutput``. That is the direct
  equivalent of walking WASAPI sessions on Windows, minus the endpoint-and-role
  matrix — the process list is device-independent. It needs **no permission**,
  so a Mac that cannot record can still detect calls and report honestly.
* **Capture** creates a system-wide process tap excluding this process, wraps
  it in a private aggregate device, and pulls float32 frames off an IO proc.
  This is the part TCC gates, and the part that needs macOS 14.2.

The microphone is a second, plain IO proc on the default input device rather
than a sub-device of the same aggregate: two independent clocks, exactly as on
Windows, and the shared mixer already resamples per source.
"""
from __future__ import annotations

import os
import platform
import tempfile
import threading
from collections import deque
from pathlib import Path

import numpy as np

from . import RawSource, log
from . import coreaudio as ca

_OWN_PID = os.getpid()

# Frames per IO-proc callback. Bigger means fewer trips into Python on a
# real-time audio thread — at 48 kHz, 4096 frames is ~85 ms, so ~12 callbacks a
# second instead of the ~180 a default 256-frame buffer would cost.
IO_BUFFER_FRAMES = 4096
# Bound on the handoff queue between the IO proc and the writer thread. At
# ~85 ms per chunk this is ~20 s of slack: enough to ride out a stalled disk,
# small enough that a wedged writer cannot eat memory without bound.
MAX_QUEUED_CHUNKS = 256


def _mac_version() -> tuple[int, ...]:
    raw = platform.mac_ver()[0] or "0"
    parts = []
    for chunk in raw.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts) or (0,)


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
class MacProbe:
    """Which processes are on the speaker and which are on the mic."""

    def _names(self, obj: int) -> set[str]:
        pid = ca.get_uint32(obj, ca.kAudioProcessPropertyPID)
        if not pid or pid == _OWN_PID:
            # Our own aggregate device makes coreaudiod report the watcher as
            # being on the mic for the whole meeting; without this the mic
            # heuristic would see a call that never ends.
            return set()
        names: set[str] = set()
        path = ca.proc_path(pid)
        if path:
            names.add(Path(path).name.lower())
        bundle = ca.get_string(obj, ca.kAudioProcessPropertyBundleID)
        if bundle:
            # Both forms go in: the app lists are substring matches, and
            # "whatsapp" should hit either "WhatsApp" or "net.whatsapp.WhatsApp".
            names.add(bundle.lower())
        return names

    def _running(self, selector: int) -> set[str]:
        procs: set[str] = set()
        for obj in ca.process_objects():
            if ca.get_uint32(obj, selector):
                procs |= self._names(obj)
        return procs

    def speaking_procs(self) -> set[str]:
        return self._running(ca.kAudioProcessPropertyIsRunningOutput)

    def listening_procs(self) -> set[str] | None:
        return self._running(ca.kAudioProcessPropertyIsRunningInput)


# --------------------------------------------------------------------------- #
# Capture
# --------------------------------------------------------------------------- #
class _Stream:
    """One IO proc writing int16 frames into one RawSource file.

    The IO proc runs on a real-time Core Audio thread, so it does nothing but
    copy bytes into a deque. All the interpretation — float32 to int16, and
    interleaving when the device hands over one buffer per channel — happens on
    the writer thread, where blocking is allowed.
    """

    def __init__(self, device: int, source: RawSource, non_interleaved: bool, kind: str):
        self.device = device
        self.source = source
        self.kind = kind                # "system" | "mic", for the silence report
        self._non_interleaved = non_interleaved
        self._queue: deque[list[tuple[int, bytes]]] = deque()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._writer: threading.Thread | None = None
        self._proc_id = None
        self.dropped = 0
        # Peak sample seen so far. Core Audio hands back a perfectly valid,
        # perfectly silent tap when system-audio consent is missing, so "did
        # any audio actually arrive" is the only way to tell a quiet meeting
        # from a broken one. Written by the writer thread, read after stop.
        self.peak = 0.0

        def _io_proc(_dev, _now, in_data, _in_time, _out_data, _out_time, _client):
            # Never raise out of here: an exception on a Core Audio thread
            # takes the process with it.
            try:
                if len(self._queue) < MAX_QUEUED_CHUNKS:
                    chunks = ca.buffer_list_chunks(in_data)
                    if chunks:
                        self._queue.append(chunks)
                        self._wake.set()
                else:
                    self.dropped += 1
            except Exception:
                pass
            return 0

        # Held as an attribute for the proc's whole lifetime: if the CFUNCTYPE
        # object is collected while Core Audio still has the pointer, the next
        # callback jumps into freed memory.
        self._callback = ca.AudioDeviceIOProc(_io_proc)

    def start(self) -> None:
        ca.set_uint32(self.device, ca.kAudioDevicePropertyBufferFrameSize, IO_BUFFER_FRAMES)
        self._proc_id = ca.create_io_proc(self.device, self._callback)
        self._writer = threading.Thread(
            target=self._write_loop, daemon=True, name=f"capture-{self.source.name[:16]}"
        )
        self._writer.start()
        ca.start_device(self.device, self._proc_id)

    def _write_loop(self) -> None:
        with open(self.source.path, "ab", buffering=1024 * 1024) as raw:
            while True:
                if not self._queue:
                    if self._stop.is_set():
                        return
                    self._wake.wait(0.2)
                    self._wake.clear()
                    continue
                try:
                    chunks = self._queue.popleft()
                except IndexError:
                    continue
                data = self._to_int16(chunks)
                if data is not None:
                    raw.write(data)

    def _to_int16(self, chunks: list[tuple[int, bytes]]) -> bytes | None:
        try:
            planes = [np.frombuffer(b, dtype="<f4") for _, b in chunks]
            if not planes:
                return None
            if self._non_interleaved and len(planes) > 1:
                width = min(p.size for p in planes)
                frames = np.stack([p[:width] for p in planes], axis=1).ravel()
            else:
                frames = np.concatenate(planes) if len(planes) > 1 else planes[0]
            if frames.size:
                self.peak = max(self.peak, float(np.abs(frames).max()))
            scaled = np.clip(frames, -1.0, 1.0) * 32767.0
            return scaled.astype(np.int16).tobytes()
        except Exception as exc:
            log(f"WARN: dropped a capture chunk ({exc})")
            return None

    def stop(self, timeout: float = 5.0) -> bool:
        """Stop the device and flush the writer. False if the writer wedged."""
        try:
            ca.stop_device(self.device, self._proc_id)
            ca.destroy_io_proc(self.device, self._proc_id)
        except Exception as exc:
            log(f"WARN: could not stop {self.source.name}: {exc}")
        self._proc_id = None
        self._stop.set()
        self._wake.set()
        if self._writer is not None:
            self._writer.join(timeout=timeout)
            if self._writer.is_alive():
                return False
        if self.dropped:
            log(f"WARN: {self.source.name} dropped {self.dropped} audio chunk(s) — "
                "the writer could not keep up")
        return True


class _MacSession:
    """A system-audio tap plus (optionally) the default microphone."""

    def __init__(self, mix_mic: bool, out_dir: Path):
        self._out_dir = out_dir
        self._streams: list[_Stream] = []
        self._tap = 0
        self._tap_desc = 0
        self._aggregate = 0
        self.sources: list[RawSource] = []
        try:
            self._build(mix_mic)
        except Exception:
            self._teardown_devices()
            raise

    def _new_raw_path(self) -> Path:
        self._out_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="capture-", suffix=".raw", dir=str(self._out_dir))
        os.close(fd)
        return Path(name)

    def _build(self, mix_mic: bool) -> None:
        own_object = ca.translate_pid(_OWN_PID)
        exclude = [own_object] if own_object else []
        self._tap_desc, tap_uuid = ca.make_tap_description(
            exclude, "AmicoScript meeting capture"
        )
        self._tap = ca.create_process_tap(self._tap_desc)

        output = ca.default_device()
        output_uid = ca.device_uid(output) if output else None
        self._aggregate = ca.create_aggregate_device({
            ca.kAudioAggregateDeviceNameKey: "AmicoScript Capture",
            ca.kAudioAggregateDeviceUIDKey: f"org.amico.AmicoScript.capture.{os.getpid()}",
            ca.kAudioAggregateDeviceIsPrivateKey: True,
            ca.kAudioAggregateDeviceTapAutoStartKey: True,
            **({ca.kAudioAggregateDeviceMainSubDeviceKey: output_uid} if output_uid else {}),
            ca.kAudioAggregateDeviceTapListKey: [{
                ca.kAudioSubTapUIDKey: tap_uuid,
                ca.kAudioSubTapDriftCompensationKey: True,
            }],
        })
        self._add_stream(self._aggregate, "System audio (tap)", "system",
                         fallback_format=self._tap)

        if mix_mic:
            mic = ca.default_device(input_side=True)
            if mic:
                try:
                    self._add_stream(mic, ca.get_string(mic, ca.kAudioDevicePropertyDeviceUID)
                                     or "Microphone", "mic")
                except Exception as exc:
                    log(f"WARN: microphone unavailable ({exc}) -- recording system audio only")
            else:
                log("WARN: no default microphone found -- recording system audio only")

    def _add_stream(self, device: int, name: str, kind: str,
                    fallback_format: int | None = None) -> None:
        fmt = ca.get_format(device, ca.kAudioDevicePropertyStreamFormat,
                            ca.kAudioObjectPropertyScopeInput)
        if fmt is None and fallback_format:
            fmt = ca.get_format(fallback_format, ca.kAudioTapPropertyFormat)
        if fmt is None or not fmt.mSampleRate:
            raise RuntimeError(f"no input stream format for {name}")
        source = RawSource(
            name=name,
            rate=int(fmt.mSampleRate),
            channels=max(1, int(fmt.mChannelsPerFrame)),
            path=self._new_raw_path(),
        )
        non_interleaved = bool(fmt.mFormatFlags & ca.kAudioFormatFlagIsNonInterleaved)
        self.sources.append(source)
        self._streams.append(_Stream(device, source, non_interleaved, kind))

    def start(self) -> None:
        for stream in self._streams:
            stream.start()

    def health(self) -> str:
        """"" when the capture looks sound, otherwise what went wrong.

        Exists because the failure this backend has to guard against is silent
        in the literal sense: without system-audio consent, Core Audio creates
        the tap, clocks it, and delivers nothing but zeros — reporting success
        the whole way. A meeting recorded with only the user's own voice on it
        is worse than no recording, so an all-zero tap is treated as an error.
        """
        for stream in self._streams:
            if stream.kind == "system" and stream.peak == 0.0:
                return SYSTEM_AUDIO_SILENT
        return ""

    def stop(self) -> None:
        clean = True
        for stream in self._streams:
            if not stream.stop():
                clean = False
        if not clean:
            # Same reasoning as the Windows backend's PyAudio teardown: the
            # audio is already on disk, and destroying a device under a thread
            # that is still touching it is how you crash the host app.
            log("WARN: a capture writer did not stop cleanly — leaving the tap in "
                "place to avoid a crash")
            return
        self._teardown_devices()

    def _teardown_devices(self) -> None:
        try:
            ca.destroy_aggregate_device(self._aggregate)
        except Exception:
            pass
        self._aggregate = 0
        try:
            ca.destroy_process_tap(self._tap)
        except Exception:
            pass
        self._tap = 0
        ca.release_object(self._tap_desc)
        self._tap_desc = 0


class MacBackend:
    name = "macos"

    def __init__(self):
        self._probe = MacProbe()

    def speaking_procs(self) -> set[str]:
        return self._probe.speaking_procs()

    def listening_procs(self) -> set[str] | None:
        return self._probe.listening_procs()

    def capture_blocked(self) -> str:
        """Why this Mac cannot record, or "" if it can. Detection still works
        either way — the process list needs no API this Mac might lack."""
        return "" if capture_supported() else capture_unsupported_reason()

    def open_session(self, mix_mic: bool, out_dir: Path) -> _MacSession:
        if not capture_supported():
            raise RuntimeError(capture_unsupported_reason())
        return _MacSession(mix_mic=mix_mic, out_dir=out_dir)


# The message a silent tap earns. Written once here because it is the single
# most likely thing to go wrong on a Mac, and the user cannot guess the fix:
# macOS attributes the grant to whatever app *launched* the watcher, and if
# that app declares no audio-capture usage it is never even prompted.
SYSTEM_AUDIO_SILENT = (
    "no system audio was captured — macOS has not granted this app permission "
    "to record the computer's audio. Open System Settings › Privacy & Security › "
    "Screen & System Audio Recording and enable AmicoScript (when running from a "
    "terminal, enable that terminal app instead)."
)


def capture_supported() -> bool:
    return _mac_version() >= ca.MAC_TAP_MIN_VERSION and ca.has_tap_api()


def capture_unsupported_reason() -> str:
    version = ".".join(str(p) for p in _mac_version())
    want = ".".join(str(p) for p in ca.MAC_TAP_MIN_VERSION)
    return (f"recording system audio needs macOS {want}+ (this Mac runs {version}); "
            "call detection still works")


def create_backend() -> MacBackend:
    return MacBackend()
