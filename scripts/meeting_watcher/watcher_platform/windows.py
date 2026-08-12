"""Windows backend: pycaw session detection + WASAPI loopback capture.

Moved out of watcher.py unchanged when the watcher grew macOS and Linux
backends. The two things this file knows that the shared loop does not:

* which processes hold an *active* audio session, per endpoint and per role —
  calls route to the *communications* default device, which is often not the
  multimedia default, so both are scanned;
* how to record the speakers without a virtual cable, via WASAPI loopback
  (pyaudiowpatch), plus the microphone, one thread per device.
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

import pyaudiowpatch as pyaudio
from pycaw.pycaw import AudioUtilities

from . import RawSource, log

AUDIO_SESSION_STATE_ACTIVE = 1  # AudioSessionStateActive
EDATAFLOW_RENDER = 0            # EDataFlow.eRender (speakers)
EDATAFLOW_CAPTURE = 1           # EDataFlow.eCapture (microphone)
EROLE_MULTIMEDIA = 1            # ERole.eMultimedia
EROLE_COMMUNICATIONS = 2        # ERole.eCommunications
# Calls often route audio to the *communications* default device, which can
# differ from the *multimedia* default. Scan both so either is seen.
DEVICE_ROLES = (EROLE_MULTIMEDIA, EROLE_COMMUNICATIONS)

READ_CHUNK = 1024

# Our own loopback + mic streams register Active sessions under this process on
# BOTH the render and capture endpoints. Excluding our PID stops the watcher
# from detecting *itself* as a never-ending call once capture starts.
_OWN_PID = os.getpid()


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
def _active_procs(sessions) -> set[str]:
    """Lowercase process names that own an *active* session in `sessions`."""
    procs: set[str] = set()
    for s in sessions or ():
        try:
            if s.State != AUDIO_SESSION_STATE_ACTIVE or not s.Process:
                continue
            if s.Process.pid == _OWN_PID:  # skip the watcher's own capture streams
                continue
            name = (s.Process.name() or "").lower()
        except Exception:
            continue
        if name:
            procs.add(name)
    return procs


def _endpoint_sessions(flow: int, role: int):
    """AudioSession list for a default endpoint (flow + role), or None on failure.

    pycaw's GetAllSessions only covers the default multimedia render endpoint, so
    we reach into the COM API to inspect any (flow, role) endpoint — crucially the
    *communications* devices where call apps actually route audio. Wrapped
    defensively: any failure returns None so callers can skip gracefully.
    """
    try:
        import comtypes
        from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.utils import AudioSession

        enumerator = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
        )
        dev = enumerator.GetDefaultAudioEndpoint(flow, role)
        mgr = dev.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None)
        mgr = mgr.QueryInterface(IAudioSessionManager2)
        enum = mgr.GetSessionEnumerator()
        return [
            AudioSession(enum.GetSession(i).QueryInterface(IAudioSessionControl2))
            for i in range(enum.GetCount())
        ]
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Device resolution — map default endpoints (both roles) to capturable devices
# --------------------------------------------------------------------------- #
def _endpoint_id(flow: int, role: int):
    """Windows device ID string of a default endpoint, or None."""
    try:
        import comtypes
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator

        en = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
        )
        return en.GetDefaultAudioEndpoint(flow, role).GetId()
    except Exception:
        return None


def _all_device_names() -> dict[str, str]:
    """id -> FriendlyName for every audio device. Expensive (COM enumeration of
    every device incl. disconnected ones) — callers should fetch this *once*
    and reuse it for both render and capture lookups, not once per flow."""
    id_to_name: dict[str, str] = {}
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # pycaw warns on unreadable props
            devices = AudioUtilities.GetAllDevices()
        for d in devices:
            try:
                if d.id and getattr(d, "FriendlyName", None):
                    id_to_name[d.id] = d.FriendlyName
            except Exception:
                continue
    except Exception:
        pass
    return id_to_name


def _default_device_names(flow: int, id_to_name: dict[str, str]) -> set[str]:
    """Friendly names of the default devices for `flow` across all roles.

    Calls route to the *communications* default, which may be a different
    physical device than the *multimedia* default, so we return both and record
    each — otherwise the remote party is captured from the wrong (silent) device.
    """
    names: set[str] = set()
    for role in DEVICE_ROLES:
        did = _endpoint_id(flow, role)
        if did and id_to_name.get(did):
            names.add(id_to_name[did])
    return names


def _norm_name(s: str) -> str:
    """Lowercase, drop the loopback suffix + non-ASCII (umlauts encode
    differently across the pycaw/pyaudio APIs), collapse whitespace."""
    s = (s or "").lower().replace("[loopback]", "")
    s = "".join(c for c in s if c.isascii())
    return " ".join(s.split())


def _names_match(a: str, b: str) -> bool:
    a, b = _norm_name(a), _norm_name(b)
    return bool(a) and bool(b) and (a in b or b in a)


# --------------------------------------------------------------------------- #
# Capture
# --------------------------------------------------------------------------- #
class _WindowsSession:
    """Records WASAPI loopback from every default *render* device (multimedia AND
    communications) plus every default *mic*, each in its own thread.

    Recording both device roles is what makes calls work when the communications
    default (where call apps often route audio) differs from the multimedia
    default — otherwise the remote party is captured from the wrong device."""

    def __init__(self, mix_mic: bool, out_dir: Path):
        self._out_dir = out_dir
        self._stop = threading.Event()
        self._pa = pyaudio.PyAudio()
        self._infos: list[dict] = []
        self._threads: list[threading.Thread] = []
        self.sources: list[RawSource] = []

        id_to_name = _all_device_names()  # one COM enumeration, reused below
        loop_infos = self._loopback_infos(id_to_name)
        if not loop_infos:
            self._pa.terminate()
            raise RuntimeError("No WASAPI loopback device found")
        infos = list(loop_infos)
        if mix_mic:
            infos += self._mic_infos(id_to_name)
        for info in infos:
            self._infos.append(info)
            self.sources.append(RawSource(
                name=str(info.get("name") or "device"),
                rate=self._rate(info),
                channels=self._channels(info),
                path=self._new_raw_path(),
            ))

    def _new_raw_path(self) -> Path:
        self._out_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="capture-", suffix=".raw", dir=str(self._out_dir))
        os.close(fd)
        return Path(name)

    @staticmethod
    def _rate(info: dict) -> int:
        return int(float(info.get("defaultSampleRate") or 16000))

    @staticmethod
    def _channels(info: dict) -> int:
        return max(1, int(info.get("maxInputChannels") or 2))

    def _default_loopback(self) -> dict:
        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        spk = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if not spk.get("isLoopbackDevice"):
            for lb in self._pa.get_loopback_device_info_generator():
                if spk["name"] in lb["name"]:
                    return lb
            raise RuntimeError("No WASAPI loopback device found for default speakers")
        return spk

    def _default_input(self) -> dict | None:
        try:
            wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            return self._pa.get_device_info_by_index(wasapi["defaultInputDevice"])
        except Exception:
            log("WARN: no default microphone found -- recording loopback only")
            return None

    def _loopback_infos(self, id_to_name: dict[str, str]) -> list[dict]:
        """Loopback devices for the default render endpoints across both roles."""
        want = _default_device_names(EDATAFLOW_RENDER, id_to_name)
        loopbacks = list(self._pa.get_loopback_device_info_generator())
        chosen: dict[int, dict] = {}
        for nm in want:
            for lb in loopbacks:
                if _names_match(nm, lb["name"]):
                    chosen[lb["index"]] = lb
                    break
        if not chosen:  # fall back to the multimedia default speaker loopback
            try:
                d = self._default_loopback()
                chosen[d["index"]] = d
            except Exception:
                pass
        return list(chosen.values())

    def _mic_infos(self, id_to_name: dict[str, str]) -> list[dict]:
        """WASAPI mic devices for the default capture endpoints across both roles."""
        want = _default_device_names(EDATAFLOW_CAPTURE, id_to_name)
        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        cand = []
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if (info.get("hostApi") == wasapi["index"]
                    and int(info.get("maxInputChannels", 0)) > 0
                    and not info.get("isLoopbackDevice")):
                cand.append(info)
        chosen: dict[int, dict] = {}
        for nm in want:
            for info in cand:
                if _names_match(nm, info["name"]):
                    chosen[info["index"]] = info
                    break
        if not chosen:  # fall back to the multimedia default mic
            d = self._default_input()
            if d:
                chosen[d["index"]] = d
        return list(chosen.values())

    def _record(self, info: dict, source: RawSource) -> None:
        try:
            stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=source.channels,
                rate=source.rate,
                frames_per_buffer=READ_CHUNK,
                input=True,
                input_device_index=info["index"],
            )
        except Exception as exc:
            log(f"WARN: cannot open {info.get('name')}: {exc}")
            return
        try:
            with open(source.path, "ab", buffering=1024 * 1024) as raw:
                while not self._stop.is_set():
                    raw.write(stream.read(READ_CHUNK, exception_on_overflow=False))
        finally:
            stream.stop_stream()
            stream.close()

    def start(self) -> None:
        for info, source in zip(self._infos, self.sources):
            t = threading.Thread(target=self._record, args=(info, source), daemon=True)
            self._threads.append(t)
            t.start()

    def stop(self) -> None:
        self._stop.set()
        stuck = False
        for t in self._threads:  # join every one: any() would short-circuit
            t.join(timeout=5)
            if t.is_alive():
                stuck = True
        if stuck:
            # A capture thread didn't exit in time (blocked in stream.read on a
            # device that went away mid-call). Tearing down PyAudio with a
            # stream still open has crashed the whole process — leak this
            # PyAudio instance instead of risking that, the raw files are
            # already on disk and safe to mix.
            log("WARN: a capture thread did not stop cleanly — skipping PyAudio teardown to avoid a crash")
            return
        try:
            self._pa.terminate()
        except Exception as exc:
            log(f"WARN: PyAudio teardown failed (ignored): {exc}")


class WindowsBackend:
    name = "windows"

    def speaking_procs(self) -> set[str]:
        """Active speaker procs across the multimedia AND communications render devices."""
        procs: set[str] = set()
        seen = False
        for role in DEVICE_ROLES:
            sess = _endpoint_sessions(EDATAFLOW_RENDER, role)
            if sess is not None:
                seen = True
                procs |= _active_procs(sess)
        if not seen:
            # COM render enumeration unavailable — fall back to pycaw's default helper.
            try:
                procs |= _active_procs(AudioUtilities.GetAllSessions())
            except Exception:
                pass
        return procs

    def listening_procs(self) -> set[str] | None:
        """Active mic procs across both capture-role devices, or None if unavailable."""
        procs: set[str] = set()
        seen = False
        for role in DEVICE_ROLES:
            sess = _endpoint_sessions(EDATAFLOW_CAPTURE, role)
            if sess is not None:
                seen = True
                procs |= _active_procs(sess)
        return procs if seen else None

    def open_session(self, mix_mic: bool, out_dir: Path) -> _WindowsSession:
        return _WindowsSession(mix_mic=mix_mic, out_dir=out_dir)


def create_backend() -> WindowsBackend:
    return WindowsBackend()
