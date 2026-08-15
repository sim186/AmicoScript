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
_AMICO_PORT = os.environ.get("AMICOSCRIPT_PORT", "8002")
LOCAL_URL = os.environ.get("AMICOSCRIPT_URL", "").rstrip("/") or f"http://127.0.0.1:{_AMICO_PORT}"

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


def _clear_stale_watcher_lock() -> bool:
    """Remove a leftover watcher.lock from a crashed process so a new watcher
    can start. Returns True if a stale lock was removed."""
    lock_path = output_dir() / "watcher.lock"
    if not lock_path.exists():
        return False
    try:
        pid_text = lock_path.read_text(encoding="ascii", errors="ignore").strip()
        if pid_text and pid_text.isdigit():
            pid = int(pid_text)
            # Check whether that PID is still alive on this host.
            if platform.system() == "Windows":
                import ctypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                handle = kernel32.OpenProcess(1, False, pid)  # PROCESS_TERMINATE=1
                if handle:
                    kernel32.CloseHandle(handle)
                    return False  # process is alive; don't touch the lock
            else:
                try:
                    os.kill(pid, 0)
                    return False  # process is alive; don't touch the lock
                except (OSError, ProcessLookupError):
                    pass  # PID does not exist → stale lock
        lock_path.unlink()
        logger.info("Cleared stale watcher.lock (PID %s)", pid_text or "?")
        return True
    except Exception:
        return False


def _start_external_task() -> None:
    """Start the installed per-user watcher task, if a setup script registered it."""
    _clear_stale_watcher_lock()
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


def _watcher_scripts_dir() -> Path | None:
    """Find the bundled meeting_watcher scripts next to the app install.

    Works both from a checkout (repo-root/scripts/meeting_watcher) and from the
    installed wheel (.../site-packages/amicoscript/scripts/meeting_watcher).
    """
    candidate = Path(__file__).resolve().parent.parent / "scripts" / "meeting_watcher"
    if candidate.exists():
        return candidate
    return None


def is_external_installed() -> bool:
    """Check whether the platform's login task for the external watcher exists."""
    system = platform.system()
    if system == "Darwin":
        label = os.environ.get("AMICOSCRIPT_WATCHER_LABEL", MAC_AGENT_LABEL)
        plist = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
        return plist.exists()
    if system == "Linux":
        unit = os.environ.get("AMICOSCRIPT_WATCHER_UNIT", LINUX_UNIT)
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "cat", unit],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False
    if system == "Windows":
        name = os.environ.get("AMICOSCRIPT_WATCHER_TASK", "AmicoScript Meeting Watcher")
        try:
            proc = subprocess.run(
                ["schtasks", "/Query", "/TN", name],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False
    return False


def install_external(scripts_dir: Path | None = None) -> bool:
    """Run the platform install script to register the external watcher.

    Returns True if installation succeeded.
    """
    watcher_dir = scripts_dir or _watcher_scripts_dir()
    if watcher_dir is None or not watcher_dir.exists():
        logger.warning("Watcher directory not found: %s", watcher_dir)
        return False

    system = platform.system()
    if system == "Darwin":
        install_script = watcher_dir / "install-macos.sh"
    elif system == "Linux":
        install_script = watcher_dir / "install-linux.sh"
    elif system == "Windows":
        install_script = watcher_dir / "install-windows.ps1"
    else:
        return False

    if not install_script.exists():
        logger.warning("Install script not found: %s", install_script)
        return False

    try:
        if system == "Windows":
            proc = subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(install_script)],
                cwd=str(watcher_dir),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        else:
            proc = subprocess.run(
                ["bash", str(install_script)],
                cwd=str(watcher_dir),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
    except Exception as exc:
        logger.error("External watcher install exception: %s", exc)
        return False

    if proc.returncode == 0:
        logger.info("External watcher installed successfully on %s", system)
        return True
    else:
        logger.error("External watcher install failed (%d): %s", proc.returncode, proc.stderr)
        return False


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
            if not is_external_installed():
                if install_external():
                    _start_external_task()
                else:
                    logger.warning(
                        "External watcher auto-install failed; "
                        "meeting auto-capture will not work until installed manually"
                    )
            else:
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
