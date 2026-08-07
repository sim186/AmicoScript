"""Starting and stopping the meeting watcher that captures calls.

There are two of them, and which one runs is a property of the host rather than
a setting. The embedded watcher runs in this process and needs WASAPI/mic
access, which only exists when AmicoScript runs directly on Windows — the
PyInstaller build. Inside the Linux Docker image there is no such access, so
the app falls back to the external watcher registered as a scheduled task by
setup.bat.

``AMICOSCRIPT_EMBEDDED_WATCHER=on|off|auto`` (default auto) overrides the
choice. Lives here rather than in main.py because none of it is app wiring:
it is a hundred lines of platform detection, thread and subprocess handling
with its own shutdown protocol.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

from utils.logging_utils import get_logger

logger = get_logger("amicoscript.watcher")

LOCAL_URL = "http://127.0.0.1:8002"

# Set by start() so stop() can signal a clean shutdown and let an in-progress
# capture finalize, rather than losing it with the daemon thread. Both stay
# None when the watcher is not running (Docker, non-Windows).
_thread: threading.Thread | None = None
_module = None


def output_dir() -> Path:
    """User-writable folder for meeting captures (never Program Files)."""
    try:
        from config import STORAGE_ROOT
        return Path(STORAGE_ROOT) / "meetings"
    except Exception:
        return Path.home() / "AmicoScript" / "meetings"


def _start_external_task() -> None:
    """Start the installed per-user watcher task, if setup.bat registered it."""
    if platform.system() != "Windows":
        return
    task_name = os.environ.get("AMICOSCRIPT_WATCHER_TASK", "AmicoScript Meeting Watcher")
    try:
        proc = subprocess.run(
            ["schtasks", "/Run", "/TN", task_name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        logger.warning("External meeting watcher task could not be started (%s)", exc)
        return
    if proc.returncode == 0:
        logger.info("External meeting watcher task started: %s", task_name)
    else:
        detail = (proc.stderr or proc.stdout or "").strip()
        logger.warning(
            "External meeting watcher task not available: %s. %s", task_name, detail
        )


def _embedded_mode() -> str:
    return os.environ.get("AMICOSCRIPT_EMBEDDED_WATCHER", "auto").lower()


def wants_embedded(mode: str | None = None) -> bool:
    """Whether the in-process watcher should be attempted on this host."""
    mode = _embedded_mode() if mode is None else mode
    if mode in {"0", "off", "false", "no"}:
        return False
    if mode == "auto":
        return platform.system() == "Windows"
    return True


def start(scripts_dir: Path) -> None:
    """Start whichever watcher this host can run. Never raises."""
    mode = _embedded_mode()
    if not wants_embedded(mode):
        # An explicit "off" still means the external watcher may be installed;
        # "auto" on a non-Windows host means there is nothing to start at all.
        if mode != "auto":
            _start_external_task()
        return

    watcher_dir = scripts_dir / "meeting_watcher"
    if watcher_dir.exists() and str(watcher_dir) not in sys.path:
        sys.path.insert(0, str(watcher_dir))
    os.environ.setdefault("AMICOSCRIPT_WATCHER_OUT", str(output_dir()))
    os.environ.setdefault("AMICOSCRIPT_URL", LOCAL_URL)

    global _thread
    _thread = threading.Thread(
        target=_run_embedded, args=(mode,), daemon=True, name="meeting-watcher"
    )
    _thread.start()


def _run_embedded(mode: str) -> None:
    global _module
    try:
        import watcher  # noqa: pulls in pyaudiowpatch/pycaw — Windows-only
    except Exception as exc:
        # Audio deps not bundled, or not a host that supports them.
        logger.warning(
            "Embedded meeting watcher unavailable (%s); "
            "use the external watcher (scripts/meeting_watcher) instead", exc
        )
        if mode == "auto":
            _start_external_task()
        return
    _module = watcher
    logger.info("Embedded meeting watcher started (enable via the UI toggle)")
    watcher.run_embedded(base_url=LOCAL_URL)


def stop(timeout: float = 10.0) -> None:
    """Signal the embedded watcher to stop, and give it a moment to finish.

    An in-progress capture has to be finalized — WAV written, transcription
    queued — before the process exits, or it is lost. Bounded so a wedged
    watcher cannot hold shutdown open indefinitely.
    """
    module = _module
    if module is None or not hasattr(module, "stop_embedded"):
        return
    try:
        module.stop_embedded()
    except Exception:
        logger.exception("Embedded meeting watcher did not stop cleanly")
        return
    if _thread is not None:
        _thread.join(timeout=timeout)
