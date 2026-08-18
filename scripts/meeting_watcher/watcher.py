"""Meeting watcher -> AmicoScript transcription helper.

Local-only (no MS Graph / cloud APIs). Detects an in-progress call from any
conferencing or chat app -- Teams, Zoom, Webex, Google Meet, WhatsApp,
Telegram, Signal, Slack, Discord, etc. -- via two signals: a dedicated meeting
app playing audio, or any app on the mic AND speaker at once (catches browser
meetings and chat-app voice/video calls). Captures the meeting audio (the
system output the remote party comes out of, plus your microphone), then
submits the WAV to the normal AmicoScript transcription queue.

This module holds everything that is the same on every operating system: the
debounce loop, the detection decision, the HTTP driver, and the mixer that
resamples the captured streams into one mono 16 kHz WAV. Reading the audio
state and opening the capture streams is the job of a platform backend in
``watcher_platform`` (Windows: pycaw + WASAPI loopback; macOS: Core Audio
process taps; Linux: PulseAudio/PipeWire).

Usage:
    python watcher.py                # uses defaults below / env vars
    AMICOSCRIPT_URL=http://localhost:8002 python watcher.py

Requirements: see requirements.txt

Notes / caveats:
  * Capture records *all* system audio (notifications, music) -- keep other
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
import math
from pathlib import Path

# The backends live next to this file. Standalone runs execute watcher.py by
# path, so its own directory is not necessarily importable — the host app adds
# it for embedded mode, and PyInstaller resolves the package out of the archive
# where this insert is a harmless no-op.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import numpy as np
import requests

try:
    import watcher_platform
except ImportError as _exc:  # pragma: no cover - a broken install, not a code path
    # An installed copy updated by an older setup script has the new watcher.py
    # and none of its backends. Say so in the terms the user can act on rather
    # than leaving a bare ImportError in the log.
    raise ImportError(
        f"{_exc}. The installed meeting watcher is incomplete — re-run its setup "
        "script, or use 'Update now' in the AmicoScript sidebar."
    ) from _exc

# --------------------------------------------------------------------------- #
# Config (override via environment variables)
# --------------------------------------------------------------------------- #
_DEFAULT_PORT = os.environ.get("AMICOSCRIPT_PORT", "8002")
BASE_URL = os.environ.get("AMICOSCRIPT_URL", f"http://127.0.0.1:{_DEFAULT_PORT}").rstrip("/")

class _Http:
    """Every backend call goes through here, so auth is applied in one place.

    A backend started with AMICOSCRIPT_AUTH=always requires credentials even
    from localhost; the token supplies them. In the default 'auto' mode
    loopback needs none and no header is added. Read per call so a token
    exported after import is still picked up.
    """

    @staticmethod
    def _with_auth(kwargs: dict) -> dict:
        token = os.environ.get("AMICOSCRIPT_API_TOKEN", "").strip()
        if token:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("Authorization", f"Bearer {token}")
            kwargs["headers"] = headers
        return kwargs

    def get(self, *args, **kwargs):
        return requests.get(*args, **self._with_auth(kwargs))

    def post(self, *args, **kwargs):
        return requests.post(*args, **self._with_auth(kwargs))


HTTP = _Http()

# Bump whenever watcher.py changes in a way an installed copy should pick up.
# Reported in the heartbeat so the web UI can tell an outdated installed
# watcher apart from the one bundled with the running app (see
# backend/api/routes/settings.py:_bundled_watcher_version, read via regex —
# do not rename this constant without updating that pattern).
WATCHER_VERSION = "4"


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

def _env_flag(var: str) -> bool | None:
    """Tri-state env flag: True/False when set, None when unset (= follow the app)."""
    raw = os.environ.get(var)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Transcription options. Unset (the normal case) means "use whatever the
# AmicoScript UI is set to" — model / language / diarize are read from
# GET /api/settings so an auto-captured meeting is transcribed exactly like a
# manual upload. Setting the env var pins that one option for the watcher only.
# Diarization in particular must NOT default to on here: it needs an HF token,
# adds minutes per meeting, and silently disagreed with the UI's Speakers toggle.
WHISPER_MODEL = os.environ.get("AMICOSCRIPT_MODEL") or None
LANGUAGE = os.environ.get("AMICOSCRIPT_LANGUAGE")
DIARIZE = _env_flag("AMICOSCRIPT_DIARIZE")
MIX_MIC = os.environ.get("AMICOSCRIPT_MIX_MIC", "true").lower() in {"1", "true", "yes", "on"}

POLL_SECONDS = 0.5          # how often to check audio state
START_DEBOUNCE = 1          # consecutive active polls before "call started"
STOP_DEBOUNCE = 2           # consecutive inactive polls before "call ended"
MIN_MEETING_SECONDS = 15    # ignore captures shorter than this (false triggers)
STATUS_HEARTBEAT = 5        # seconds between "recording" heartbeats to the web UI

def _env_set(var: str, default: str) -> set[str]:
    return {a.strip().lower() for a in os.environ.get(var, default).split(",") if a.strip()}


# Meeting-app detection -------------------------------------------------------
# Process names differ per OS ("ms-teams.exe" / "Microsoft Teams" /
# "teams-for-linux"), and so does what belongs on the blocklist, so the defaults
# come from the platform table in watcher_platform. Env vars still override.
_APP_DEFAULTS = watcher_platform.app_defaults()
# CALL_APPS (render-only): dedicated meeting clients where the app playing audio
# is itself a reliable "in a call" signal. Matched as substrings of the process
# name of any app holding an active *speaker* session.
CALL_APPS = _env_set("AMICOSCRIPT_CALL_APPS", _APP_DEFAULTS["call"])
# CHAT_APPS (mic + speaker required): apps that ALSO play non-call audio (voice
# notes, video clips, notification chimes). Triggering on the speaker alone
# would false-fire on every voice-note playback, so these are only detected when
# the app is on the mic AND the speaker at once (the heuristic below). Covers
# WhatsApp, Telegram, Signal, Messenger, Slack huddles, Discord.
CHAT_APPS = _env_set("AMICOSCRIPT_CHAT_APPS", _APP_DEFAULTS["chat"])
KNOWN_APPS = CALL_APPS | CHAT_APPS  # for labelling only
# Blocklist: never treat these as a meeting even under the heuristic (e.g. media
# players). Keep browsers OUT of this list — they host web meetings.
BLOCK_APPS = _env_set("AMICOSCRIPT_BLOCK_APPS", _APP_DEFAULTS["block"])
# Heuristic: any app capturing the mic AND playing audio at once == a 2-way
# call, regardless of name. Catches browser meetings (Google Meet) and all the
# CHAT_APPS above. Requires the backend to report mic activity.
USE_MIC_HEURISTIC = os.environ.get("AMICOSCRIPT_MIC_HEURISTIC", "true").lower() in {"1", "true", "yes", "on"}

CHUNK = 1024
# Whisper transcribes at 16 kHz mono and the backend normalizes to that anyway,
# so write the mix straight out at 16 kHz instead of the ~48 kHz capture rate:
# a 2 h meeting drops from ~700 MB to ~230 MB with no loss of usable signal,
# and the backend's ffmpeg pass gets correspondingly cheaper.
OUT_RATE = 16000
_heuristic_warned = False
_health_warned = False
# Last capture's verdict, kept so the heartbeat can keep reporting a broken
# setup for as long as it stays broken rather than only at the moment it is
# noticed. Cleared by the next healthy capture.
_health_problem = ""

# The platform backend, resolved on first use so that importing this module
# never depends on an audio stack being present (the tests, the packaged app on
# a host without the optional deps, and `--help`-style runs all rely on that).
_BACKEND = None
_BACKEND_ERROR = ""

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
_instance_mutex = None
_instance_lock_fd = None
_instance_lock_path: Path | None = None


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


watcher_platform.set_logger(log)


def backend():
    """The platform backend, or None when this host has none.

    Resolved once, lazily. A missing backend is survivable: the loop keeps
    heartbeating so the web UI can report the helper as running-but-unable
    rather than silently absent.
    """
    global _BACKEND, _BACKEND_ERROR
    if _BACKEND is None and not _BACKEND_ERROR:
        _BACKEND, _BACKEND_ERROR = watcher_platform.get_backend()
    return _BACKEND


try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_IMPORTS_OK = True
except Exception:  # pystray/Pillow optional; fall back to headless
    _TRAY_IMPORTS_OK = False

# Windows-only for now: pystray's macOS backend wants NSApplication on the main
# thread and its Linux one a GTK loop, neither of which a watcher thread or a
# launchd/systemd service has.
_TRAY_OK = _TRAY_IMPORTS_OK and watcher_platform.tray_supported()


def _logo_path() -> str | None:
    """Path to the AmicoScript icon, used for the notification toasts.

    Checked next to this file first (deployed/standalone layout — the setup
    script downloads the icon alongside watcher.py), then the repo's images/
    dir (running from source), then the PyInstaller bundle: frozen into the app
    this module lives in the PYZ archive, so __file__ points at _MEIPASS and
    the icon is only reachable through the bundled scripts/ data tree."""
    here = Path(__file__).parent
    names = ("logo.ico", "logo.png")
    candidates = [here / n for n in names]
    candidates += [here.parent.parent / "images" / n for n in names]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates += [Path(meipass) / "scripts" / "meeting_watcher" / n for n in names]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


_LOGO_PATH = _logo_path()


def notify(title: str, message: str) -> None:
    """Show a desktop notification (no-op where the host has no notifier)."""
    log(f"NOTIFY: {title} — {message}")
    watcher_platform.notify(title, message, _LOGO_PATH)


def _acquire_instance_lock() -> bool:
    """Return False when another watcher already owns the per-user lock."""
    global _instance_mutex, _instance_lock_fd, _instance_lock_path
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.CreateMutexW(None, False, "Local\\AmicoScriptMeetingWatcher")
            if not handle:
                log("WARN: could not create watcher mutex; continuing without lock")
                return True
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(handle)
                return False
            _instance_mutex = handle
            return True
        except Exception as exc:
            log(f"WARN: could not create watcher mutex ({exc}); continuing without lock")
            return True

    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        _instance_lock_path = OUTPUT_DIR / "watcher.lock"
        _instance_lock_fd = os.open(str(_instance_lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(_instance_lock_fd, str(os.getpid()).encode("ascii", errors="ignore"))
        return True
    except FileExistsError:
        return False
    except Exception as exc:
        log(f"WARN: could not create watcher lock ({exc}); continuing without lock")
        return True


def _release_instance_lock() -> None:
    global _instance_mutex, _instance_lock_fd, _instance_lock_path
    if _instance_mutex is not None and os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(_instance_mutex)
        except Exception:
            pass
        _instance_mutex = None
    if _instance_lock_fd is not None:
        try:
            os.close(_instance_lock_fd)
        except Exception:
            pass
        _instance_lock_fd = None
    if _instance_lock_path is not None:
        try:
            _instance_lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        _instance_lock_path = None


# --------------------------------------------------------------------------- #
# Meeting detection -- app-agnostic, backend supplies the raw audio state
# --------------------------------------------------------------------------- #
def _speaking_procs() -> set[str]:
    """Lowercase names of processes currently playing audio."""
    be = backend()
    if be is None:
        return set()
    try:
        return be.speaking_procs()
    except Exception as exc:
        log(f"WARN: could not read speaker activity: {exc}")
        return set()


def _listening_procs() -> set[str] | None:
    """Same for the mic, or None when this host cannot tell (heuristic off)."""
    be = backend()
    if be is None:
        return None
    try:
        return be.listening_procs()
    except Exception as exc:
        log(f"WARN: could not read microphone activity: {exc}")
        return None


def _pretty_app(proc_name: str) -> str:
    """Readable label from a process name: zoom.exe, Microsoft Teams, zoom.us."""
    low = proc_name.lower()
    for known in KNOWN_APPS:
        if known in low:
            return known.capitalize()
    base = low.removesuffix(".exe").removesuffix(".app")
    if base.startswith("ms-"):
        base = base[3:]
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
                log("WARN: mic-heuristic unavailable on this host — allowlist only")
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
# Audio mixing — the backend records the streams, this mixes them into one WAV
# --------------------------------------------------------------------------- #
_AA_KERNELS: dict = {}


def _antialias_kernel(ratio: float):
    """Windowed-sinc low-pass used before decimating by `ratio`.

    Without it every component above the output Nyquist folds back into the
    speech band as aliasing noise, which is exactly the range Whisper listens
    to. Cutoff sits at 0.9x the output Nyquist to leave a transition band; the
    tap count scales with the ratio but is capped so filtering a multi-hour
    meeting stays cheap relative to reading the raw captures off disk.
    """
    key = round(ratio, 3)
    cached = _AA_KERNELS.get(key)
    if cached is not None:
        return cached
    taps = int(min(65, max(15, 8 * ratio))) | 1  # odd -> symmetric, zero phase shift
    n = np.arange(taps, dtype=np.float64) - (taps - 1) / 2.0
    h = np.sinc(n * (0.9 / ratio)) * np.hanning(taps)
    kernel = (h / h.sum()).astype(np.float32)
    _AA_KERNELS[key] = kernel
    return kernel


class Capture:
    """One recording: the platform backend opens the streams, this mixes them.

    The backend decides *what* gets recorded — on Windows that is WASAPI
    loopback from every default render device (multimedia AND communications,
    because calls often route to the latter) plus every default mic. Whatever
    it opens arrives here as raw int16 files, which stop() resamples and mixes
    into a single mono 16 kHz WAV.
    """

    def __init__(self, mix_mic: bool = True):
        self.mix_mic = mix_mic
        be = backend()
        if be is None:
            raise RuntimeError(_BACKEND_ERROR or "no meeting-watcher backend on this host")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self._session = be.open_session(mix_mic=mix_mic, out_dir=OUTPUT_DIR)
        self._sources = list(self._session.sources)
        if not self._sources:
            self._session.stop()
            raise RuntimeError("backend opened no capture sources")
        log("Capture devices: " + ", ".join(s.name for s in self._sources))

    def start(self) -> None:
        self._session.start()

    def health(self) -> str:
        """Backend's verdict on the finished capture: "" if it looks sound.

        Some capture failures are silent rather than loud — macOS hands back a
        working but empty system-audio tap when permission is missing — so a
        backend gets to say "this recording is not what it should be" even
        though nothing raised.
        """
        check = getattr(self._session, "health", None)
        if check is None:
            return ""
        try:
            return check() or ""
        except Exception:
            return ""

    @staticmethod
    def _bytes_to_mono(raw: bytes, channels: int) -> np.ndarray:
        if not raw:
            return np.zeros(0, dtype=np.float32)
        data = np.frombuffer(raw, dtype=np.int16)
        if channels > 1:
            frame_count = data.size // channels
            data = data[: frame_count * channels].reshape(-1, channels).mean(axis=1)
        return data.astype(np.float32)

    @staticmethod
    def _read_resampled_window(fh, stat: dict, out_start: int, out_n: int, out_rate: int) -> np.ndarray:
        src_rate = stat["rate"]
        total_frames = stat["frames"]
        channels = stat["channels"]
        bytes_per_frame = stat["bytes_per_frame"]
        if total_frames <= 0:
            return np.zeros(out_n, dtype=np.float32)

        if src_rate == out_rate:
            src_start = out_start
            if src_start >= total_frames:
                return np.zeros(out_n, dtype=np.float32)
            read_frames = min(out_n, total_frames - src_start)
            fh.seek(src_start * bytes_per_frame)
            mono = Capture._bytes_to_mono(fh.read(read_frames * bytes_per_frame), channels)
            if mono.size < out_n:
                mono = np.pad(mono, (0, out_n - mono.size))
            return mono[:out_n].astype(np.float32)

        ratio = src_rate / float(out_rate)
        src_start = max(0, int(math.floor(out_start * ratio)))
        src_end = min(total_frames, int(math.ceil((out_start + out_n) * ratio)) + 1)
        if src_start >= src_end:
            return np.zeros(out_n, dtype=np.float32)
        # When downsampling, read a margin on each side so the anti-alias filter
        # sees real samples at the window edges — otherwise every chunk boundary
        # gets a click from convolving against zero padding.
        kernel = _antialias_kernel(ratio) if ratio > 1.0 else None
        pad = kernel.size // 2 if kernel is not None else 0
        read_start = max(0, src_start - pad)
        read_end = min(total_frames, src_end + pad)
        fh.seek(read_start * bytes_per_frame)
        mono = Capture._bytes_to_mono(fh.read((read_end - read_start) * bytes_per_frame), channels)
        if mono.size == 0:
            return np.zeros(out_n, dtype=np.float32)
        if kernel is not None and mono.size > kernel.size:
            mono = np.convolve(mono, kernel, mode="same").astype(np.float32)
        positions = (np.arange(out_n, dtype=np.float64) + out_start) * ratio - read_start
        return np.interp(
            positions,
            np.arange(mono.size, dtype=np.float64),
            mono,
            left=0.0,
            right=0.0,
        ).astype(np.float32)

    def _source_stats(self) -> list[dict]:
        stats = []
        for source in self._sources:
            channels = max(1, int(source.channels))
            bytes_per_frame = channels * 2
            try:
                size = source.path.stat().st_size
            except OSError:
                size = 0
            frames = size // bytes_per_frame
            if frames > 0:
                stats.append({
                    "source": source,
                    "channels": channels,
                    "rate": int(source.rate),
                    "bytes_per_frame": bytes_per_frame,
                    "frames": frames,
                })
        return stats

    def stop(self, out_path: Path) -> float:
        """Stop recording, write mixed WAV to out_path, return duration seconds."""
        try:
            self._session.stop()
        except Exception as exc:
            # The bytes are already on disk; a device that would not release is
            # not a reason to lose the meeting.
            log(f"WARN: capture teardown failed (ignored): {exc}")

        stats = self._source_stats()
        rate = OUT_RATE
        duration = max((s["frames"] / float(s["rate"]) for s in stats), default=0.0)
        total_frames = int(math.ceil(duration * rate))
        chunk_frames = max(rate * 10, CHUNK)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        handles = []
        try:
            handles = [(s, open(s["source"].path, "rb")) for s in stats]
            with wave.open(str(out_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                gain = 1.0 / len(handles) if handles else 1.0
                for out_start in range(0, total_frames, chunk_frames):
                    out_n = min(chunk_frames, total_frames - out_start)
                    mix = np.zeros(out_n, dtype=np.float32)
                    for stat, fh in handles:
                        mix += self._read_resampled_window(fh, stat, out_start, out_n, rate) * gain
                    wf.writeframes(np.clip(mix, -32768, 32767).astype(np.int16).tobytes())
        finally:
            for _, fh in handles:
                try:
                    fh.close()
                except Exception:
                    pass
            for source in self._sources:
                try:
                    source.path.unlink(missing_ok=True)
                except Exception:
                    pass

        return duration


# --------------------------------------------------------------------------- #
# AmicoScript HTTP driver
# --------------------------------------------------------------------------- #
_enabled_cache = {"value": None, "ts": 0.0}
_server_token_cache = {"value": "", "ts": 0.0}
# Last-seen transcription defaults from the app (sidebar model / language /
# Speakers toggle). Refreshed by every GET /api/settings the watcher already
# makes for the capture toggle, so uploads follow the UI without extra requests.
_defaults_cache = {"model": "small", "language": "", "diarize": False}
ENABLED_TTL = 5.0  # seconds to cache the toggle state


def _remember_server_settings(data: dict) -> None:
    token = data.get("exit_token") or ""
    if token:
        _server_token_cache["value"] = token
        _server_token_cache["ts"] = time.time()
    if "meeting_capture_enabled" in data:
        _enabled_cache["value"] = bool(data.get("meeting_capture_enabled", False))
        _enabled_cache["ts"] = time.time()
    if data.get("default_model"):
        _defaults_cache["model"] = str(data["default_model"])
    if "default_language" in data:
        _defaults_cache["language"] = str(data.get("default_language") or "")
    if "default_diarize" in data:
        _defaults_cache["diarize"] = bool(data.get("default_diarize"))


def transcription_options() -> dict:
    """Model / language / diarize for an upload.

    An explicit env var pins the option for the watcher; otherwise we follow the
    app's saved defaults (older backends without them fall back to the values in
    ``_defaults_cache``, i.e. small / auto-detect / no diarization).
    """
    return {
        "model": WHISPER_MODEL or _defaults_cache["model"],
        "language": LANGUAGE if LANGUAGE is not None else _defaults_cache["language"],
        "diarize": DIARIZE if DIARIZE is not None else _defaults_cache["diarize"],
    }


def server_token(force: bool = False) -> str:
    """Session token for CSRF-protected local POST endpoints."""
    if _server_token_cache["value"] and not force:
        return str(_server_token_cache["value"])
    try:
        r = HTTP.get(f"{BASE_URL}/api/settings", timeout=5)
        r.raise_for_status()
        _remember_server_settings(r.json())
    except Exception:
        pass
    return str(_server_token_cache["value"] or "")


def capture_enabled() -> bool:
    """Whether the web-UI 'Meeting auto-capture' toggle is ON. Cached briefly.

    On any error, keep the last known value (default False) so a transient app
    restart does not silently start/stop recording.
    """
    now = time.time()
    if _enabled_cache["value"] is not None and now - _enabled_cache["ts"] < ENABLED_TTL:
        return _enabled_cache["value"]
    try:
        r = HTTP.get(f"{BASE_URL}/api/settings", timeout=5)
        r.raise_for_status()
        data = r.json()
        _remember_server_settings(data)
        value = bool(data.get("meeting_capture_enabled", False))
    except Exception:
        value = bool(_enabled_cache["value"])  # last known, or False
    _enabled_cache["value"] = value
    _enabled_cache["ts"] = now
    return value


def unsupported_reason() -> str:
    """Why a running watcher will not actually record, or "" if it will.

    Three shapes of the same problem, worth distinguishing to the user: no
    backend at all, a backend whose OS is too old to capture, and a backend
    that captured and got nothing but silence back. All of them leave a watcher
    that is alive and heartbeating, which is exactly why the UI needs told.
    """
    if _health_problem:
        return _health_problem
    be = backend()
    if be is None:
        return _BACKEND_ERROR
    check = getattr(be, "capture_blocked", None)
    if callable(check):
        try:
            return check() or ""
        except Exception:
            return ""
    return ""


def report_status(recording: bool, app: str = "", started_at: float = 0.0) -> None:
    """Tell AmicoScript whether we're recording, so the web UI can show a chip.

    Best-effort: posted on capture start, periodically as a heartbeat, and on
    stop. Failures are ignored — the server expires a stale heartbeat on its own.

    ``started_at`` is a Unix timestamp of when the capture actually began.
    Sent on recording heartbeats so the server can preserve it across missed
    heartbeats instead of resetting the badge timer.
    """
    try:
        data = {
            "recording": "true" if recording else "false",
            "app": app,
            "version": WATCHER_VERSION,
            "unsupported": unsupported_reason(),
            "token": server_token(),
        }
        if recording and started_at > 0:
            data["started_at"] = str(started_at)
        resp = HTTP.post(
            f"{BASE_URL}/api/watcher/status",
            data=data,
            timeout=5,
        )
        if resp.status_code == 403:
            data["token"] = server_token(force=True)
            HTTP.post(f"{BASE_URL}/api/watcher/status", data=data, timeout=5)
    except Exception:
        pass


def transcribe(wav_path: Path) -> tuple[str, str]:
    """Upload WAV, return (job_id, recording_id)."""
    opts = transcription_options()
    log(f"Transcribing with model={opts['model']} "
        f"language={opts['language'] or 'auto'} diarize={opts['diarize']}")
    with open(wav_path, "rb") as f:
        resp = HTTP.post(
            f"{BASE_URL}/api/transcribe",
            files={"file": (wav_path.name, f, "audio/wav")},
            data={
                "model": opts["model"],
                "language": opts["language"],
                "diarize": "true" if opts["diarize"] else "false",
                # Marks the recording as a captured call, which is what the
                # app's "summarise meetings automatically" option keys off.
                "source": "meeting",
            },
            timeout=300,
        )
    resp.raise_for_status()
    j = resp.json()
    return j["job_id"], j["recording_id"]


def submit_recording(wav_path: Path) -> None:
    """Upload WAV to the normal transcription queue and return."""
    try:
        log(f"Uploading {wav_path.name} -> AmicoScript")
        job_id, recording_id = transcribe(wav_path)
        log(f"Transcription queued: job={job_id} recording={recording_id}")
        notify("Transcription queued", wav_path.name)
    except Exception as exc:
        log(f"ERROR uploading recording: {exc}")


def _cleanup_orphan_raw() -> None:
    """Delete ``capture-*.raw`` scratch files left behind by a crashed watcher.

    ``Capture.stop`` removes them normally, but a hard kill mid-meeting leaves
    hundreds of MB per source sitting in the output dir forever.
    """
    removed = 0
    try:
        for stale in OUTPUT_DIR.glob("capture-*.raw"):
            try:
                stale.unlink()
                removed += 1
            except OSError:
                pass
    except Exception:
        return
    if removed:
        log(f"Cleaned up {removed} orphaned capture scratch file(s)")


def _report_capture_health(capture: "Capture") -> None:
    """Surface a capture that succeeded mechanically but produced no audio.

    Notified once per watcher run, not once per meeting: it is a configuration
    problem that stays broken until the user fixes it, and a toast after every
    call would be nagging rather than informing. The log line is written every
    time, because that is the record someone debugging will read.
    """
    global _health_warned, _health_problem
    # getattr, not capture.health(): Capture is the seam the tests and the
    # embedded host substitute, and a stand-in without a health report should
    # mean "nothing to say", not an AttributeError mid-finalize.
    check = getattr(capture, "health", None)
    problem = check() if callable(check) else ""
    _health_problem = problem
    if not problem:
        return
    log(f"WARN: {problem}")
    if not _health_warned:
        _health_warned = True
        notify("Meeting capture is missing permission", problem)


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
    _report_capture_health(capture)
    if duration >= MIN_MEETING_SECONDS:
        log(f"Captured {duration:.0f}s -> {wav_path.name}")
        notify("Recording stopped", f"{duration / 60:.0f} min captured — transcribing…")
        threading.Thread(
            target=submit_recording, args=(wav_path,), daemon=True
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
        data = {"enabled": "true" if value else "false", "token": server_token()}
        resp = HTTP.post(
            f"{BASE_URL}/api/settings/meeting-capture",
            data=data,
            timeout=5,
        )
        if resp.status_code == 403:
            data["token"] = server_token(force=True)
            HTTP.post(f"{BASE_URL}/api/settings/meeting-capture", data=data, timeout=5)
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


def _tray_title() -> str:
    return "AmicoScript meetings" if _EMBEDDED else "AmicoScript watcher"


def _build_tray_menu():
    items = [
        pystray.MenuItem(lambda item: _tray_status_label(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda item: "Resume auto-capture" if not _tray_state["enabled"] else "Pause auto-capture",
            lambda icon, item: _set_capture_enabled(not _tray_state["enabled"]),
        ),
        pystray.MenuItem("Open AmicoScript", lambda icon, item: __import__("webbrowser").open(BASE_URL)),
    ]
    if not _EMBEDDED:
        # Embedded, the watcher's lifetime is the host app's: quitting it would
        # leave meeting capture dead with no way back short of restarting the
        # app. Pause covers the same need reversibly.
        items += [pystray.Menu.SEPARATOR, pystray.MenuItem("Quit watcher", _tray_on_quit)]
    return pystray.Menu(*items)


def _tray_on_quit(icon, item) -> None:
    _tray_quit.set()
    try:
        icon.stop()
    except Exception:
        pass


def _start_tray() -> None:
    """Show the notification-area icon — in embedded mode too.

    The native app's only other recording indicator is the web-UI chip, which
    vanishes the moment the browser tab is closed. A tool that silently records
    meetings in the background has to be visible somewhere at all times, so the
    tray icon runs regardless of how the watcher was started.
    """
    global _tray_icon
    if os.environ.get("AMICOSCRIPT_TRAY", "true").lower() not in {"1", "true", "yes", "on"}:
        return
    if not _TRAY_OK:
        # Two different situations, and telling a Mac user to pip-install
        # pystray would send them after something that cannot work there.
        if not watcher_platform.tray_supported():
            log("No tray icon on this platform — the web UI's recording chip is the indicator")
        else:
            log("Tray icon unavailable (pip install pystray pillow) — running headless")
        return
    try:
        _tray_icon = pystray.Icon(
            "amicoscript-watcher",
            _tray_image(_TRAY_COLORS["off"]),
            _tray_title(),
            menu=_build_tray_menu(),
        )
        threading.Thread(target=_tray_icon.run, daemon=True, name="tray").start()
        log("Tray icon started (notification area). Right-click to pause.")
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
        icon.title = _tray_title() + " — " + _tray_status_label()
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
def _main_loop() -> None:
    log(f"Meeting watcher v{WATCHER_VERSION} started. AmicoScript = {BASE_URL}")
    be = backend()
    if be is None:
        # Keep running: the heartbeat is how the web UI learns the helper is
        # installed, and saying "cannot capture, here is why" beats vanishing.
        log(f"ERROR: {_BACKEND_ERROR} — detection and capture are disabled")
    else:
        log(f"Platform backend: {be.name}")
    pinned = ", ".join(
        f"{k}={v}" for k, v in (
            ("model", WHISPER_MODEL), ("language", LANGUAGE), ("diarize", DIARIZE),
        ) if v is not None
    )
    log(f"Output dir = {OUTPUT_DIR} | mix_mic={MIX_MIC} | "
        f"transcription options: {pinned or 'following the AmicoScript UI settings'}")
    _cleanup_orphan_raw()
    log(f"Meeting apps (speaker): {', '.join(sorted(CALL_APPS))}")
    log(f"Chat apps (mic+speaker): {', '.join(sorted(CHAT_APPS))} | mic-heuristic={'on' if USE_MIC_HEURISTIC else 'off'}")
    if CHAT_APPS and not USE_MIC_HEURISTIC:
        log("WARN: chat apps (WhatsApp/Telegram/…) need the mic heuristic — it is OFF")
    log("Enable/disable via the 'Meeting auto-capture' toggle in the AmicoScript sidebar.")
    log(f"Desktop notifications: {'on' if watcher_platform.notify_supported() else 'off'}")
    try:
        HTTP.get(f"{BASE_URL}/api/jobs", timeout=5)
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

        if in_call and not enabled:
            in_call = False
            active_streak = inactive_streak = 0
            report_status(False)
            log("Auto-capture disabled -- stopping capture")
            if capture:
                _finalize_capture(capture, started_at, detected_app)
                capture = None

        elif not in_call and enabled and active_streak >= START_DEBOUNCE:
            in_call = True
            started_at = dt.datetime.now()
            detected_app = app or "Meeting"
            log(f"{detected_app} meeting detected -- starting capture")
            try:
                capture = Capture(mix_mic=MIX_MIC)
                capture.start()
                notify("Recording started", f"Capturing {detected_app} meeting at {started_at:%H:%M}")
                _started_at_ts = started_at.timestamp()
                report_status(True, detected_app, _started_at_ts)
                last_heartbeat = time.time()
                # Send a follow-up heartbeat after 1s so the server has fresh
                # state even if the first one was slow or lost.
                def _send_followup():
                    time.sleep(1.0)
                    report_status(True, detected_app, _started_at_ts)
                threading.Thread(target=_send_followup, daemon=True).start()
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

        # Heartbeat: include started_at so the server can preserve the badge
        # timer across missed heartbeats.
        now = time.time()
        if now - last_heartbeat >= STATUS_HEARTBEAT:
            recording = in_call and capture is not None
            _sat_ts = started_at.timestamp() if (recording and started_at) else 0.0
            report_status(recording, detected_app if recording else "", _sat_ts)
            last_heartbeat = now

        # Reflect current state on the tray icon (colour + tooltip + menu label).
        _tray_state["enabled"] = enabled
        _tray_state["recording"] = bool(in_call and capture is not None)
        _tray_state["app"] = detected_app if in_call else ""
        _tray_refresh()

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


def main() -> None:
    if not _acquire_instance_lock():
        log("Another meeting watcher is already running; exiting.")
        return
    try:
        _main_loop()
    finally:
        _release_instance_lock()


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
