"""Per-OS detection and capture backends for the meeting watcher.

``watcher.py`` owns everything that is the same everywhere — the debounce loop,
the allowlist/mic-heuristic decision, the HTTP driver, the resampling mixer.
Everything that needs an operating system's audio APIs lives behind the
``Backend`` protocol below, one module per platform:

* ``windows`` — pycaw audio sessions + WASAPI loopback (pyaudiowpatch)
* ``macos``   — Core Audio process objects + a process tap (macOS 14.2+)
* ``linux``   — PulseAudio/PipeWire streams via ``pactl`` + ``parec``

Nothing in this package's ``__init__`` may import an audio dependency: it is
imported unconditionally by ``watcher.py`` on every platform, including hosts
where no backend is installable at all. The heavy imports happen inside
``get_backend()``, which reports a reason instead of raising when they fail.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = [
    "RawSource",
    "CaptureSession",
    "Backend",
    "app_defaults",
    "get_backend",
    "log",
    "notify",
    "notify_supported",
    "platform_key",
    "set_logger",
    "tray_supported",
]


# --------------------------------------------------------------------------- #
# Logging. The backends have things to say (a device that would not open, a
# heuristic that is unavailable) and the only log the user ever looks at is
# watcher.log, which watcher.py owns. Rather than import watcher from here —
# it imports us — it hands its logger over at startup.
# --------------------------------------------------------------------------- #
_LOGGER = None


def set_logger(fn) -> None:
    global _LOGGER
    _LOGGER = fn


def log(message: str) -> None:
    if _LOGGER is not None:
        _LOGGER(message)


# --------------------------------------------------------------------------- #
# The contract between watcher.py and a backend
# --------------------------------------------------------------------------- #
@dataclass
class RawSource:
    """One capture stream, writing raw little-endian int16 frames to ``path``.

    The mixer in ``watcher.Capture`` reads these files back after the session
    stops, so a backend may fill them however it likes (a thread, an audio
    callback, a subprocess writing to the fd) as long as the bytes are flushed
    by the time ``CaptureSession.stop()`` returns.
    """

    name: str
    rate: int
    channels: int
    path: Path


@runtime_checkable
class CaptureSession(Protocol):
    """A started-and-stoppable group of ``RawSource``s recorded together."""

    sources: list[RawSource]

    def start(self) -> None: ...

    def stop(self) -> None:
        """Stop every source, flush its file, and release the audio devices.

        Must not raise: a capture that cannot be torn down cleanly still has to
        hand the recorded bytes to the mixer.
        """


@runtime_checkable
class Backend(Protocol):
    name: str

    def speaking_procs(self) -> set[str]:
        """Lowercase process names currently playing audio (this one excluded)."""

    def listening_procs(self) -> set[str] | None:
        """Same, for the microphone. ``None`` when this host cannot tell, which
        turns the mic heuristic off instead of reporting "nobody is on the mic"."""

    def open_session(self, mix_mic: bool, out_dir: Path) -> CaptureSession: ...


# --------------------------------------------------------------------------- #
# Platform selection
# --------------------------------------------------------------------------- #
# Detection allowlists differ per OS because process names do: "ms-teams.exe" on
# Windows, "Microsoft Teams" on macOS, "teams-for-linux" on Linux. Matched as
# substrings, so the shortest distinctive stem is the right entry. The blocklist
# is the one that really has to be per-OS — "music" must not block anything on
# Windows, and "wmplayer" means nothing on a Mac.
APP_DEFAULTS: dict[str, dict[str, str]] = {
    "windows": {
        "call": "teams,zoom,webex,gotomeeting,bluejeans,whereby,ringcentral",
        "chat": "whatsapp,telegram,signal,messenger,slack,discord",
        "block": "spotify,vlc,wmplayer",
    },
    "macos": {
        "call": "teams,zoom,webex,gotomeeting,bluejeans,whereby,ringcentral,facetime",
        "chat": "whatsapp,telegram,signal,messenger,slack,discord",
        "block": "music,spotify,podcasts,quicktime player,vlc,tv",
    },
    "linux": {
        "call": "teams,zoom,webex,gotomeeting,bluejeans,whereby,ringcentral",
        "chat": "whatsapp,telegram,signal,messenger,slack,discord",
        "block": "spotify,vlc,mpv,rhythmbox,totem",
    },
}

_MODULE_FOR = {"windows": "windows", "macos": "macos", "linux": "linux"}


def _override() -> str:
    """``AMICOSCRIPT_WATCHER_BACKEND``: a platform name, or an importable module.

    An escape hatch on a host where the default choice is wrong, and the hook
    the tests use to load a fake backend instead of a real audio stack.
    """
    return os.environ.get("AMICOSCRIPT_WATCHER_BACKEND", "").strip().lower()


def platform_key(explicit: str | None = None) -> str:
    """Which platform this host is: ``windows`` | ``macos`` | ``linux`` | ``""``.

    An override naming a *platform* answers this; an override naming some other
    module does not — a fake backend does not change which process names or
    notifier this machine has.
    """
    name = explicit if explicit is not None else _override()
    if name in _MODULE_FOR:
        return name
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return ""


def app_defaults() -> dict[str, str]:
    """Default call/chat/block app lists for this host (env vars still win)."""
    return APP_DEFAULTS.get(platform_key(), APP_DEFAULTS["windows"])


def get_backend() -> tuple[Backend | None, str]:
    """Return ``(backend, "")`` or ``(None, reason)``. Never raises.

    A missing backend is a normal state, not an error: the app bundles the
    watcher on hosts that may not have its audio dependencies installed, and it
    has to keep heartbeating (so the UI can say what is wrong) rather than die.
    """
    override = _override()
    if override and override not in _MODULE_FOR:
        key, module_name = override, override
    else:
        key = platform_key()
        if not key:
            return None, f"no meeting-watcher backend for platform {sys.platform!r}"
        module_name = f"{__name__}.{_MODULE_FOR[key]}"
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        return None, f"{key} backend unavailable ({exc})"
    try:
        return module.create_backend(), ""
    except Exception as exc:
        return None, f"{key} backend failed to start ({exc})"


# --------------------------------------------------------------------------- #
# Desktop notifications — kept out of the backends so a toast still works on a
# host where the audio stack is missing (that is exactly when the user needs to
# be told something).
# --------------------------------------------------------------------------- #
def tray_supported() -> bool:
    """Whether to attempt a notification-area icon.

    Windows only for now. pystray's macOS backend needs ``NSApplication`` on the
    main thread and its Linux backend needs a GTK loop — neither is available to
    a watcher running as a thread inside the app or under launchd/systemd.
    """
    if platform_key() != "windows":
        return False
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        return False
    return True


def _notify_windows(title: str, message: str, icon_path: str | None) -> bool:
    try:
        from winotify import Notification
    except Exception:
        return False
    kwargs = {"app_id": "AmicoScript", "title": title, "msg": message}
    if icon_path:
        kwargs["icon"] = icon_path
    Notification(**kwargs).show()
    return True


def _notify_macos(title: str, message: str, _icon_path: str | None) -> bool:
    # osascript instead of a dependency: it is always present, and the watcher
    # may be running from a bare interpreter under launchd.
    script = (
        f"display notification {_applescript_str(message)} "
        f"with title {_applescript_str('AmicoScript')} "
        f"subtitle {_applescript_str(title)}"
    )
    subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, timeout=10, check=True,
    )
    return True


def _applescript_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _notify_linux(title: str, message: str, icon_path: str | None) -> bool:
    cmd = ["notify-send", "--app-name=AmicoScript"]
    if icon_path:
        cmd.append(f"--icon={icon_path}")
    cmd += [title, message]
    subprocess.run(cmd, capture_output=True, timeout=10, check=True)
    return True


_NOTIFIERS = {
    "windows": _notify_windows,
    "macos": _notify_macos,
    "linux": _notify_linux,
}


def notify_supported() -> bool:
    """Whether this host has any way to show a desktop notification.

    Windows needs winotify installed; macOS and Linux need a binary that ships
    with the desktop (``osascript`` / ``notify-send``).
    """
    key = platform_key()
    if key == "windows":
        try:
            import winotify  # noqa: F401
        except Exception:
            return False
        return True
    if key == "macos":
        return shutil.which("osascript") is not None
    if key == "linux":
        return shutil.which("notify-send") is not None
    return False


def notify(title: str, message: str, icon_path: str | None = None) -> bool:
    """Show a desktop notification. Returns False when this host has no way to.

    Best-effort by design — a failed toast must never interrupt a capture, and
    ``watcher.py`` has already written the same text to the log.
    """
    fn = _NOTIFIERS.get(platform_key())
    if fn is None:
        return False
    try:
        return bool(fn(title, message, icon_path))
    except Exception:
        return False


def run_text(cmd: list[str], timeout: float = 5.0) -> str | None:
    """Run a helper binary and return stdout, or None if it isn't usable.

    Shared by the Linux backend (``pactl``) and the installers' probes; keeping
    it here means one place decides what "this tool isn't available" looks like.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout
