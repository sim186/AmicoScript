"""Starting and stopping the meeting watcher that captures calls.

There are two of them, and which one runs is a property of the host rather than
a setting. The embedded watcher runs in this process and needs the host's audio
APIs, which exist whenever AmicoScript runs directly on a desktop — the
PyInstaller build, or `python run.py`. Inside a container there is no audio
access at all, so the app falls back to the external watcher that the platform's
setup script registered as a login task on the host.

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

import config
from llm_providers import in_container as _in_container
from utils.logging_utils import get_logger

logger = get_logger("amicoscript.watcher")

# Where the embedded watcher posts its heartbeat and uploads. Overridable
# because the watcher already treats AMICOSCRIPT_URL as the address of the app:
# pinning it here meant an app served on any other port got a watcher talking
# to whatever else happened to be on 8002.
LOCAL_URL = os.environ.get("AMICOSCRIPT_URL", "").rstrip("/") or "http://127.0.0.1:8002"

# Set by start() so stop() can signal a clean shutdown and let an in-progress
# capture finalize, rather than losing it with the daemon thread. Both stay
# None when the watcher is not running (Docker, non-Windows).
_thread: threading.Thread | None = None
_module = None


def output_dir() -> Path:
    """User-writable folder for meeting captures (never Program Files)."""
    try:
        return Path(config.STORAGE_ROOT) / "meetings"
    except Exception:
        return Path.home() / "AmicoScript" / "meetings"


MAC_AGENT_LABEL = "org.amico.AmicoScript.watcher"
LINUX_UNIT = "amicoscript-watcher.service"


def _external_task_command() -> tuple[str, list[str]] | None:
    """(name, argv) that starts this host's installed watcher, or None.

    Each platform's setup script registers the watcher with its own login-task
    system, so "start it" is a different command on each — but the shape is the
    same: a short, non-interactive command that succeeds if the task exists.
    """
    system = platform.system()
    if system == "Windows":
        name = os.environ.get("AMICOSCRIPT_WATCHER_TASK", "AmicoScript Meeting Watcher")
        return name, ["schtasks", "/Run", "/TN", name]
    if system == "Darwin":
        label = os.environ.get("AMICOSCRIPT_WATCHER_LABEL", MAC_AGENT_LABEL)
        return label, ["launchctl", "kickstart", f"gui/{os.getuid()}/{label}"]
    if system == "Linux":
        unit = os.environ.get("AMICOSCRIPT_WATCHER_UNIT", LINUX_UNIT)
        return unit, ["systemctl", "--user", "start", unit]
    return None


def _start_external_task() -> None:
    """Start the installed per-user watcher task, if a setup script registered it."""
    command = _external_task_command()
    if command is None:
        return
    task_name, argv = command
    try:
        proc = subprocess.run(
            argv,
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
    """Whether the in-process watcher should be attempted on this host.

    "auto" means "if this host can": every desktop OS has a backend now, so the
    question is no longer which OS but whether this process can reach the
    machine's audio at all. It cannot from inside a container — the Docker image
    is Linux, and a Linux desktop is not — which is the one distinction that
    matters here.
    """
    mode = _embedded_mode() if mode is None else mode
    if mode in {"0", "off", "false", "no"}:
        return False
    if mode == "auto":
        if platform.system() not in {"Windows", "Darwin", "Linux"}:
            return False
        return not _in_container()
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
        # F401: imported for its side effects — pulls in pyaudiowpatch/pycaw,
        # which exist on Windows only.
        import watcher  # noqa: F401
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
