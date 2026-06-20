"""Meeting watcher -> AmicoScript auto-report daemon.

Local-only (no MS Graph / cloud APIs). Detects an in-progress call from any
conferencing or chat app -- Teams, Zoom, Webex, Google Meet, WhatsApp,
Telegram, Signal, Slack, Discord, etc. -- via two signals (pycaw): a dedicated
meeting app playing audio, or any app on the mic AND speaker at once (catches
browser meetings and chat-app voice/video calls). Captures the meeting
audio via WASAPI loopback + your microphone (pyaudiowpatch), then drives the
running AmicoScript instance over HTTP to transcribe and produce a report
(summary + action items).

Usage:
    python watcher.py                # uses defaults below / env vars
    AMICOSCRIPT_URL=http://localhost:8002 python watcher.py

Requirements: see requirements.txt
    pip install pyaudiowpatch pycaw comtypes requests numpy

Notes / caveats:
  * Loopback records *all* system audio (notifications, music) -- keep other
    audio quiet during a call.
  * "In a call" is inferred from sustained audio activity. A long notification
    sound could trigger a false start; tune the debounce constants. Refine
    detection via AMICOSCRIPT_CALL_APPS / AMICOSCRIPT_BLOCK_APPS / the mic
    heuristic (AMICOSCRIPT_MIC_HEURISTIC).
  * Recording meetings may require consent of all parties and/or violate
    company policy. Make sure you are authorized before running this.
"""

from __future__ import annotations

import os
import sys
import time
import wave
import threading
import datetime as dt
from pathlib import Path

import numpy as np
import requests
import pyaudiowpatch as pyaudio
from pycaw.pycaw import AudioUtilities

# --------------------------------------------------------------------------- #
# Config (override via environment variables)
# --------------------------------------------------------------------------- #
BASE_URL = os.environ.get("AMICOSCRIPT_URL", "http://localhost:8002").rstrip("/")


def _default_output_dir() -> Path:
    """Default capture folder: mirrors the app's ``STORAGE_ROOT / meetings``
    (see backend/config.py) so embedded and external watcher write to the same
    place and meeting files never land in the repo root. Resolved here rather
    than via ``from config import ...`` because the watcher runs standalone on
    the host and must not depend on backend imports."""
    portable = os.environ.get("AMICOSCRIPT_PORTABLE", "").lower() in ("1", "true", "yes")
    if portable:
        root = Path.cwd() / "amicoscript-data"
    else:
        root = Path.home() / ".amicoscript" / "data"
    return root / "meetings"


_env_out = os.environ.get("AMICOSCRIPT_WATCHER_OUT")
OUTPUT_DIR = Path(_env_out if _env_out else _default_output_dir()).resolve()

WHISPER_MODEL = os.environ.get("AMICOSCRIPT_MODEL", "small")
DIARIZE = os.environ.get("AMICOSCRIPT_DIARIZE", "true").lower() in {"1", "true", "yes", "on"}
MIX_MIC = os.environ.get("AMICOSCRIPT_MIX_MIC", "true").lower() in {"1", "true", "yes", "on"}
ANALYSES = ("summary", "action_items")  # LLM reports to request after transcription

POLL_SECONDS = 1.0          # how often to check audio state
START_DEBOUNCE = 3          # consecutive active polls before "call started"
STOP_DEBOUNCE = 6           # consecutive inactive polls before "call ended"
MIN_MEETING_SECONDS = 15    # ignore captures shorter than this (false triggers)
STATUS_HEARTBEAT = 10       # seconds between "recording" heartbeats to the web UI

def _env_set(var: str, default: str) -> set[str]:
    return {a.strip().lower() for a in os.environ.get(var, default).split(",") if a.strip()}


# Meeting-app detection -------------------------------------------------------
# CALL_APPS (render-only): dedicated meeting clients where the app playing audio
# is itself a reliable "in a call" signal. Matched as substrings of the process
# name of any app holding an active *speaker* session.
CALL_APPS = _env_set(
    "AMICOSCRIPT_CALL_APPS",
    "teams,zoom,webex,gotomeeting,bluejeans,whereby,ringcentral",
)
# CHAT_APPS (mic + speaker required): apps that ALSO play non-call audio (voice
# notes, video clips, notification chimes). Triggering on the speaker alone
# would false-fire on every voice-note playback, so these are only detected when
# the app is on the mic AND the speaker at once (the heuristic below). Covers
# WhatsApp, Telegram, Signal, Messenger, Slack huddles, Discord.
CHAT_APPS = _env_set(
    "AMICOSCRIPT_CHAT_APPS",
    "whatsapp,telegram,signal,messenger,slack,discord",
)
KNOWN_APPS = CALL_APPS | CHAT_APPS  # for labelling only
# Blocklist: never treat these as a meeting even under the heuristic (e.g. media
# players). Keep browsers OUT of this list — they host web meetings.
BLOCK_APPS = _env_set("AMICOSCRIPT_BLOCK_APPS", "spotify,vlc,wmplayer")
# Heuristic: any app capturing the mic AND playing audio at once == a 2-way
# call, regardless of name. Catches browser meetings (Google Meet) and all the
# CHAT_APPS above. Requires mic-session enumeration (see _capture_sessions).
USE_MIC_HEURISTIC = os.environ.get("AMICOSCRIPT_MIC_HEURISTIC", "true").lower() in {"1", "true", "yes", "on"}

CHUNK = 1024
AUDIO_SESSION_STATE_ACTIVE = 1  # AudioSessionStateActive
EDATAFLOW_RENDER = 0            # EDataFlow.eRender (speakers)
EDATAFLOW_CAPTURE = 1           # EDataFlow.eCapture (microphone)
EROLE_MULTIMEDIA = 1            # ERole.eMultimedia
EROLE_COMMUNICATIONS = 2        # ERole.eCommunications
# Calls (WhatsApp/Teams/Zoom) route audio to the *communications* default device,
# which can differ from the *multimedia* default. Scan both so either is seen.
DEVICE_ROLES = (EROLE_MULTIMEDIA, EROLE_COMMUNICATIONS)
_heuristic_warned = False
# Our own loopback + mic streams register Active sessions under this process on
# BOTH the render and capture endpoints. Excluding our PID stops the watcher
# from detecting *itself* as a never-ending call once capture starts.
_OWN_PID = os.getpid()

# Tray state, shared between the watch loop (writer) and the tray UI (reader).
_EMBEDDED = False                      # True when run inside the native app
_tray_icon = None                      # pystray.Icon once started
_tray_quit = threading.Event()         # set by the tray "Quit" menu item
# Set by the host app (via stop_embedded) on shutdown so the embedded loop can
# finalize any in-progress capture before the process exits. Standalone mode
# ignores this (the tray Quit / KeyboardInterrupt paths drive shutdown instead).
_embedded_quit = threading.Event()
_tray_state = {"enabled": False, "recording": False, "app": ""}
_tray_last_sig = None


LOG_FILE = Path(os.environ.get("AMICOSCRIPT_WATCHER_LOG", str(OUTPUT_DIR / "watcher.log")))


def log(msg: str) -> None:
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


try:
    from winotify import Notification
    _NOTIFY_OK = True
except Exception:  # winotify optional; fall back to log-only
    _NOTIFY_OK = False

try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_OK = True
except Exception:  # pystray/Pillow optional; fall back to headless
    _TRAY_OK = False


def _logo_path() -> str | None:
    """Path to the AmicoScript .ico, used for both the toast and tray icon.

    Checked next to this file first (deployed/standalone layout — setup.bat
    downloads logo.ico alongside watcher.py), then the repo's images/ dir
    (running from source)."""
    for c in (Path(__file__).parent / "logo.ico",
              Path(__file__).parent.parent.parent / "images" / "logo.ico"):
        if c.exists():
            return str(c)
    return None


_LOGO_PATH = _logo_path()


def notify(title: str, message: str) -> None:
    """Show a Windows desktop toast (no-op if winotify unavailable)."""
    log(f"NOTIFY: {title} — {message}")
    if not _NOTIFY_OK:
        return
    try:
        kwargs = {"app_id": "AmicoScript", "title": title, "msg": message}
        if _LOGO_PATH:
            kwargs["icon"] = _LOGO_PATH
        Notification(**kwargs).show()
    except Exception as exc:
        log(f"WARN: toast failed: {exc}")


# --------------------------------------------------------------------------- #
# Meeting detection (pycaw) -- app-agnostic
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


def _speaking_procs() -> set[str]:
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


def _listening_procs():
    """Active mic procs across both render-role mic devices, or None if unavailable."""
    procs: set[str] = set()
    seen = False
    for role in DEVICE_ROLES:
        sess = _endpoint_sessions(EDATAFLOW_CAPTURE, role)
        if sess is not None:
            seen = True
            procs |= _active_procs(sess)
    return procs if seen else None


def _pretty_app(proc_name: str) -> str:
    """teams.exe -> Teams, ms-teams.exe -> Teams, whatsapp.exe -> Whatsapp."""
    low = proc_name.lower()
    for known in KNOWN_APPS:
        if known in low:
            return known.capitalize()
    base = low.removesuffix(".exe").lstrip("ms-")
    return base.capitalize() or proc_name


def call_in_progress() -> tuple[bool, str]:
    """Return (in_call, app_label) using allowlist first, then mic heuristic."""
    global _heuristic_warned
    speaking = _speaking_procs()
    if not speaking:
        return False, ""

    # 1) Allowlist: a known meeting app is playing audio.
    for name in speaking:
        if any(app in name for app in CALL_APPS):
            return True, _pretty_app(name)

    # 2) Heuristic: an app is on the mic AND the speaker simultaneously.
    if USE_MIC_HEURISTIC:
        listening = _listening_procs()
        if listening is None:
            if not _heuristic_warned:
                log("WARN: mic-heuristic unavailable on this pycaw build — allowlist only")
                _heuristic_warned = True
        else:
            both = {
                n for n in (speaking & listening)
                if not any(b in n for b in BLOCK_APPS)
            }
            if both:
                return True, _pretty_app(next(iter(both)))

    return False, ""


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
# Audio capture (pyaudiowpatch WASAPI loopback + mic)
# --------------------------------------------------------------------------- #
class Capture:
    """Records WASAPI loopback from every default *render* device (multimedia AND
    communications) plus every default *mic*, each in its own thread, then mixes
    everything to one mono 16-bit WAV on stop().

    Recording both device roles is what makes calls work when the communications
    default (where WhatsApp/Teams/Zoom route audio) differs from the multimedia
    default — otherwise the remote party is captured from the wrong device."""

    def __init__(self, mix_mic: bool = True):
        self.mix_mic = mix_mic
        self._stop = threading.Event()
        self._pa = pyaudio.PyAudio()
        # Each source: (pyaudio device-info dict, frames sink).
        self._sources: list[tuple[dict, list[bytes]]] = []
        id_to_name = _all_device_names()  # one COM enumeration, reused below
        loop_infos = self._loopback_infos(id_to_name)
        if not loop_infos:
            self._pa.terminate()
            raise RuntimeError("No WASAPI loopback device found")
        for info in loop_infos:
            self._sources.append((info, []))
        if mix_mic:
            for info in self._mic_infos(id_to_name):
                self._sources.append((info, []))
        self._threads: list[threading.Thread] = []
        log("Capture devices: " + ", ".join(i["name"] for i, _ in self._sources))

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

    def _record(self, info: dict, sink: list[bytes]) -> None:
        try:
            stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=int(info["maxInputChannels"]) or 2,
                rate=int(info["defaultSampleRate"]),
                frames_per_buffer=CHUNK,
                input=True,
                input_device_index=info["index"],
            )
        except Exception as exc:
            log(f"WARN: cannot open {info.get('name')}: {exc}")
            return
        try:
            while not self._stop.is_set():
                sink.append(stream.read(CHUNK, exception_on_overflow=False))
        finally:
            stream.stop_stream()
            stream.close()

    def start(self) -> None:
        for info, sink in self._sources:
            t = threading.Thread(target=self._record, args=(info, sink), daemon=True)
            t.start()
            self._threads.append(t)

    @staticmethod
    def _to_mono(frames: list[bytes], channels: int) -> np.ndarray:
        if not frames:
            return np.zeros(0, dtype=np.float32)
        data = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32)
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        return data

    @staticmethod
    def _resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        if src_rate == dst_rate or data.size == 0:
            return data
        n = int(round(data.size * dst_rate / src_rate))
        return np.interp(np.linspace(0, data.size, n, endpoint=False),
                         np.arange(data.size), data)

    def stop(self, out_path: Path) -> float:
        """Stop recording, write mixed WAV to out_path, return duration seconds."""
        self._stop.set()
        stuck = False
        for t in self._threads:
            t.join(timeout=5)
            if t.is_alive():
                stuck = True

        rate = int(self._sources[0][0]["defaultSampleRate"]) if self._sources else 16000
        monos = []
        for info, sink in self._sources:
            channels = int(info.get("maxInputChannels", 1)) or 1
            m = self._to_mono(sink, channels)
            m = self._resample(m, int(info["defaultSampleRate"]), rate)
            if m.size:
                monos.append(m)

        if monos:
            length = max(m.size for m in monos)
            mix = np.zeros(length, dtype=np.float32)
            # Attenuate each source before summing so N near-full-scale signals
            # don't add up to ~N*32767 and hard-clip at the clip step below
            # (audible distortion when loopback + mic are both loud). Dividing by
            # the source count keeps the mix's peak roughly equal to one source.
            gain = 1.0 / len(monos)
            for m in monos:
                mix[: m.size] += m * gain
        else:
            mix = np.zeros(0, dtype=np.float32)

        mix = np.clip(mix, -32768, 32767).astype(np.int16)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(mix.tobytes())

        if stuck:
            # A capture thread didn't exit in time (blocked in stream.read on a
            # device that went away mid-call). Tearing down PyAudio with a
            # stream still open has crashed the whole process — leak this
            # PyAudio instance instead of risking that, the WAV is already safe.
            log("WARN: a capture thread did not stop cleanly — skipping PyAudio teardown to avoid a crash")
        else:
            try:
                self._pa.terminate()
            except Exception as exc:
                log(f"WARN: PyAudio teardown failed (ignored): {exc}")
        return mix.size / float(rate) if rate else 0.0


# --------------------------------------------------------------------------- #
# AmicoScript HTTP driver
# --------------------------------------------------------------------------- #
_enabled_cache = {"value": None, "ts": 0.0}
ENABLED_TTL = 5.0  # seconds to cache the toggle state


def capture_enabled() -> bool:
    """Whether the web-UI 'Teams auto-capture' toggle is ON. Cached briefly.

    On any error, keep the last known value (default False) so a transient app
    restart does not silently start/stop recording.
    """
    now = time.time()
    if _enabled_cache["value"] is not None and now - _enabled_cache["ts"] < ENABLED_TTL:
        return _enabled_cache["value"]
    try:
        r = requests.get(f"{BASE_URL}/api/settings", timeout=5)
        r.raise_for_status()
        value = bool(r.json().get("meeting_capture_enabled", False))
    except Exception:
        value = bool(_enabled_cache["value"])  # last known, or False
    _enabled_cache["value"] = value
    _enabled_cache["ts"] = now
    return value


def report_status(recording: bool, app: str = "") -> None:
    """Tell AmicoScript whether we're recording, so the web UI can show a chip.

    Best-effort: posted on capture start, periodically as a heartbeat, and on
    stop. Failures are ignored — the server expires a stale heartbeat on its own.
    """
    try:
        requests.post(
            f"{BASE_URL}/api/watcher/status",
            data={"recording": "true" if recording else "false", "app": app},
            timeout=5,
        )
    except Exception:
        pass


def transcribe(wav_path: Path) -> tuple[str, str]:
    """Upload WAV, return (job_id, recording_id)."""
    with open(wav_path, "rb") as f:
        resp = requests.post(
            f"{BASE_URL}/api/transcribe",
            files={"file": (wav_path.name, f, "audio/wav")},
            data={"model": WHISPER_MODEL, "diarize": "true" if DIARIZE else "false"},
            timeout=60,
        )
    resp.raise_for_status()
    j = resp.json()
    return j["job_id"], j["recording_id"]


def wait_job(job_id: str, label: str, timeout: float = 3600) -> str:
    """Poll a transcription/translation job until terminal. Returns status."""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        r = requests.get(f"{BASE_URL}/api/jobs/{job_id}/logs", timeout=30)
        r.raise_for_status()
        data = r.json()
        status, prog = data.get("status", ""), data.get("progress", 0.0)
        line = f"{label}: {status} {int((prog or 0) * 100)}%"
        if line != last:
            log(line)
            last = line
        if status in ("done", "error", "cancelled"):
            return status
        time.sleep(2)
    return "timeout"


def run_analysis(recording_id: str, analysis_type: str, timeout: float = 1800) -> str:
    """Create an analysis, poll until done, return result_text (or '')."""
    resp = requests.post(
        f"{BASE_URL}/api/recordings/{recording_id}/analyses",
        data={"analysis_type": analysis_type},
        timeout=60,
    )
    if resp.status_code != 200:
        log(f"  {analysis_type}: skipped ({resp.status_code} {resp.text[:120]})")
        return ""
    analysis_id = resp.json()["analysis_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{BASE_URL}/api/recordings/{recording_id}/analyses/{analysis_id}", timeout=30)
        r.raise_for_status()
        a = r.json()
        if a.get("status") == "done":
            return a.get("result_text") or ""
        if a.get("status") == "error":
            log(f"  {analysis_type}: error")
            return a.get("result_text") or ""
        time.sleep(2)
    return ""


def process_recording(wav_path: Path, started: dt.datetime, app_name: str = "Meeting") -> None:
    """Full pipeline: transcribe -> analyses -> write markdown report."""
    try:
        log(f"Uploading {wav_path.name} -> AmicoScript")
        job_id, recording_id = transcribe(wav_path)
        if wait_job(job_id, "transcribe") != "done":
            log("Transcription did not complete; leaving WAV for manual retry")
            return

        report = [
            f"# Meeting report",
            f"",
            f"- **App:** {app_name}",
            f"- **Started:** {started:%Y-%m-%d %H:%M}",
            f"- **Audio:** `{wav_path.name}`",
            f"- **Recording ID:** {recording_id}",
            f"- **Open in app:** {BASE_URL}/?recording={recording_id}",
            f"",
        ]
        for atype in ANALYSES:
            log(f"  running analysis: {atype}")
            text = run_analysis(recording_id, atype)
            report.append(f"## {atype.replace('_', ' ').title()}\n\n{text or '_(no result)_'}\n")

        report_path = wav_path.with_suffix(".report.md")
        report_path.write_text("\n".join(report), encoding="utf-8")
        log(f"Report written: {report_path}")
        notify("Meeting report ready", report_path.name)
    except Exception as exc:
        log(f"ERROR processing recording: {exc}")


def _finalize_capture(capture: "Capture", started_at, detected_app: str) -> None:
    """Stop a capture, save the WAV, and kick off transcription if long enough."""
    stamp = (started_at or dt.datetime.now()).strftime("%Y%m%d_%H%M%S")
    slug = "".join(c for c in detected_app.lower() if c.isalnum()) or "meeting"
    wav_path = OUTPUT_DIR / f"{slug}_{stamp}.wav"
    try:
        duration = capture.stop(wav_path)
    except Exception as exc:
        log(f"ERROR stopping capture: {exc}")
        duration = 0.0
    if duration >= MIN_MEETING_SECONDS:
        log(f"Captured {duration:.0f}s -> {wav_path.name}")
        notify("Recording stopped", f"{duration / 60:.0f} min captured — transcribing…")
        threading.Thread(
            target=process_recording, args=(wav_path, started_at, detected_app), daemon=True
        ).start()
    else:
        log(f"Ignored short capture ({duration:.0f}s < {MIN_MEETING_SECONDS}s)")
        wav_path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# System-tray icon (notification area) — optional, pystray + Pillow
# --------------------------------------------------------------------------- #
_TRAY_COLORS = {
    "recording": (220, 53, 53),   # red while recording
    "idle": (46, 160, 87),        # green: running, waiting for a call
    "off": (130, 130, 130),       # grey: auto-capture disabled
}


def _set_capture_enabled(value: bool) -> None:
    """Flip the server-side auto-capture toggle (keeps tray + web UI in sync)."""
    try:
        requests.post(
            f"{BASE_URL}/api/settings/meeting-capture",
            data={"enabled": "true" if value else "false"},
            timeout=5,
        )
    except Exception as exc:
        log(f"WARN: could not set capture toggle: {exc}")
    _enabled_cache["value"] = value
    _enabled_cache["ts"] = time.time()
    _tray_state["enabled"] = value
    _tray_refresh(force=True)


def _tray_status_label() -> str:
    s = _tray_state
    if not s["enabled"]:
        return "Auto-capture: OFF"
    if s["recording"]:
        return f"● Recording: {s['app'] or 'meeting'}"
    return "Idle — waiting for a call"


def _tray_image(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((10, 10, 54, 54), fill=color)
    return img


def _tray_color_key() -> str:
    if _tray_state["recording"]:
        return "recording"
    return "idle" if _tray_state["enabled"] else "off"


def _build_tray_menu():
    return pystray.Menu(
        pystray.MenuItem(lambda item: _tray_status_label(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda item: "Resume auto-capture" if not _tray_state["enabled"] else "Pause auto-capture",
            lambda icon, item: _set_capture_enabled(not _tray_state["enabled"]),
        ),
        pystray.MenuItem("Open AmicoScript", lambda icon, item: __import__("webbrowser").open(BASE_URL)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit watcher", _tray_on_quit),
    )


def _tray_on_quit(icon, item) -> None:
    _tray_quit.set()
    try:
        icon.stop()
    except Exception:
        pass


def _start_tray() -> None:
    global _tray_icon
    if _EMBEDDED:
        return  # native app owns its own UI
    if os.environ.get("AMICOSCRIPT_TRAY", "true").lower() not in {"1", "true", "yes", "on"}:
        return
    if not _TRAY_OK:
        log("Tray icon unavailable (pip install pystray pillow) — running headless")
        return
    try:
        _tray_icon = pystray.Icon(
            "amicoscript-watcher",
            _tray_image(_TRAY_COLORS["off"]),
            "AmicoScript watcher",
            menu=_build_tray_menu(),
        )
        threading.Thread(target=_tray_icon.run, daemon=True, name="tray").start()
        log("Tray icon started (notification area). Right-click to pause/quit.")
    except Exception as exc:
        log(f"WARN: tray icon failed: {exc}")
        _tray_icon = None


def _tray_refresh(force: bool = False) -> None:
    global _tray_last_sig
    icon = _tray_icon
    if icon is None:
        return
    sig = (_tray_state["enabled"], _tray_state["recording"], _tray_state["app"])
    if not force and sig == _tray_last_sig:
        return
    color_changed = _tray_last_sig is None or sig[:2] != _tray_last_sig[:2]
    _tray_last_sig = sig
    try:
        icon.icon = _tray_image(_TRAY_COLORS[_tray_color_key()])
        icon.title = "AmicoScript watcher — " + _tray_status_label()
        icon.update_menu()
        if color_changed:
            # pystray/win32 sometimes caches the tray bitmap and ignores a plain
            # icon.icon reassignment (NIM_MODIFY) -- toggling visibility forces
            # Windows to fully re-add the icon (NIM_DELETE + NIM_ADD), which
            # always redraws. Without this the icon can stay stuck on whatever
            # colour it was created with even though the tooltip updates fine.
            icon.visible = False
            icon.visible = True
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Main watch loop
# --------------------------------------------------------------------------- #
def main() -> None:
    log(f"Meeting watcher started. AmicoScript = {BASE_URL}")
    log(f"Output dir = {OUTPUT_DIR} | model={WHISPER_MODEL} diarize={DIARIZE} mix_mic={MIX_MIC}")
    log(f"Meeting apps (speaker): {', '.join(sorted(CALL_APPS))}")
    log(f"Chat apps (mic+speaker): {', '.join(sorted(CHAT_APPS))} | mic-heuristic={'on' if USE_MIC_HEURISTIC else 'off'}")
    if CHAT_APPS and not USE_MIC_HEURISTIC:
        log("WARN: chat apps (WhatsApp/Telegram/…) need the mic heuristic — it is OFF")
    log("Enable/disable via the 'Meeting auto-capture' toggle in the AmicoScript sidebar.")
    log(f"Desktop toasts: {'on' if _NOTIFY_OK else 'OFF (pip install winotify)'}")
    try:
        requests.get(f"{BASE_URL}/api/jobs", timeout=5)
    except Exception:
        log("WARN: AmicoScript not reachable yet -- will retry when a meeting ends")

    _start_tray()

    active_streak = inactive_streak = 0
    in_call = False
    capture: Capture | None = None
    started_at: dt.datetime | None = None
    detected_app = "Meeting"
    last_heartbeat = 0.0

    while True:
        if _tray_quit.is_set() or _embedded_quit.is_set():
            break
        try:
            present, app = call_in_progress()
        except Exception as exc:
            log(f"detection error: {exc}")
            present, app = in_call, detected_app

        if present:
            active_streak += 1
            inactive_streak = 0
        else:
            inactive_streak += 1
            active_streak = 0

        enabled = capture_enabled()

        if not in_call and enabled and active_streak >= START_DEBOUNCE:
            in_call = True
            started_at = dt.datetime.now()
            detected_app = app or "Meeting"
            log(f"{detected_app} meeting detected -- starting capture")
            try:
                capture = Capture(mix_mic=MIX_MIC)
                capture.start()
                notify("Recording started", f"Capturing {detected_app} meeting at {started_at:%H:%M}")
                report_status(True, detected_app)
                last_heartbeat = time.time()
            except Exception as exc:
                log(f"ERROR starting capture: {exc}")
                capture, in_call = None, False

        elif in_call and inactive_streak >= STOP_DEBOUNCE:
            in_call = False
            report_status(False)
            log("Meeting ended -- stopping capture")
            if capture:
                _finalize_capture(capture, started_at, detected_app)
                capture = None

        # Reflect current state on the tray icon (colour + tooltip + menu label).
        _tray_state["enabled"] = enabled
        _tray_state["recording"] = bool(in_call and capture is not None)
        _tray_state["app"] = detected_app if in_call else ""
        _tray_refresh()

        # Heartbeat so the web UI knows the watcher is installed and running
        # (and whether it is currently recording). Sent while idle too, so the
        # UI can hide its one-time setup prompt once the watcher is alive.
        now = time.time()
        if now - last_heartbeat >= STATUS_HEARTBEAT:
            recording = in_call and capture is not None
            report_status(recording, detected_app if recording else "")
            last_heartbeat = now

        time.sleep(POLL_SECONDS)

    # Shutdown (tray Quit, host stop_embedded, or KeyboardInterrupt): finalize
    # any in-progress capture so the WAV is saved + transcribed, then exit.
    if capture is not None:
        log("Quit requested -- finalizing in-progress capture")
        _finalize_capture(capture, started_at, detected_app)
    report_status(False)
    if _tray_icon is not None:
        try:
            _tray_icon.stop()
        except Exception:
            pass
    log("Watcher stopped.")


def stop_embedded() -> None:
    """Signal the embedded watch loop to stop at its next poll.

    Called by the host app's shutdown hook so an in-progress capture is
    finalized (WAV written + transcription queued) before the process exits,
    instead of being lost when the daemon thread is killed. Safe to call when
    not running embedded or already stopped (the event is idempotent).
    """
    _embedded_quit.set()


def run_embedded(base_url: str | None = None) -> None:
    """Run the watch loop *inside* the AmicoScript host process (no sys.exit).

    Used by the native (PyInstaller) build: the backend runs on the host with
    WASAPI/mic access, so the watcher can run as a background thread driven by
    the same "Meeting auto-capture" toggle — no separate process, scheduled
    task, or setup.bat. Exceptions are swallowed so the watcher can never crash
    the host app. (Docker builds keep using the external watcher instead.)
    """
    global BASE_URL, _EMBEDDED
    _EMBEDDED = True  # suppress the standalone tray icon inside the native app
    if base_url:
        BASE_URL = base_url.rstrip("/")
    try:
        main()
    except Exception as exc:  # never take the host app down
        log(f"embedded watcher loop crashed: {exc}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        report_status(False)  # clear the web-UI recording chip on exit
        log("Stopped.")
        sys.exit(0)
