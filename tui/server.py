"""Backend server lifecycle: probe, spawn, supervise, terminate."""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Deque
from urllib.parse import urlparse
from urllib.request import urlopen
from urllib.error import URLError


READY_TIMEOUT_S = 30.0
PROBE_INTERVAL_S = 0.5
LOG_BUFFER_LINES = 2000


class ServerManager:
    """Spawn and supervise the AmicoScript backend as a subprocess.

    If a server already responds at `api_url`, attach to it instead of
    spawning. The spawned process inherits AMICOSCRIPT_NO_BROWSER=1 so it
    does not pop a browser window.
    """

    def __init__(self, api_url: str, spawn: bool = True) -> None:
        self.api_url = api_url.rstrip("/")
        self.spawn_requested = spawn
        self.process: subprocess.Popen | None = None
        self.logs: Deque[str] = deque(maxlen=LOG_BUFFER_LINES)
        self._log_thread: threading.Thread | None = None
        self._shutdown_called = False
        atexit.register(self.shutdown)

    # --- public API --------------------------------------------------

    def ensure_ready(self) -> bool:
        """Return True once `GET /api/version` returns 200.

        Probes the existing URL first; spawns a subprocess if missing and
        allowed.
        """
        if self._probe():
            return True
        if not self.spawn_requested:
            return False
        if not self._is_loopback(self.api_url):
            # Refuse to spawn when targeting a remote host.
            return False
        self._spawn()
        return self._wait_ready(READY_TIMEOUT_S)

    def shutdown(self) -> None:
        if self._shutdown_called:
            return
        self._shutdown_called = True
        proc = self.process
        if proc is None or proc.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except Exception:
            pass

    def is_alive(self) -> bool:
        if self.process is None:
            return self._probe()
        return self.process.poll() is None

    # --- internal ----------------------------------------------------

    @staticmethod
    def _is_loopback(url: str) -> bool:
        host = urlparse(url).hostname or ""
        return host in {"127.0.0.1", "localhost", "::1"}

    def _probe(self) -> bool:
        try:
            with urlopen(f"{self.api_url}/api/version", timeout=1.5) as r:
                return r.status == 200
        except (URLError, OSError, ValueError):
            return False

    def _wait_ready(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._probe():
                return True
            if self.process and self.process.poll() is not None:
                return False
            time.sleep(PROBE_INTERVAL_S)
        return False

    def _spawn(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        run_py = repo_root / "run.py"
        env = os.environ.copy()
        env["AMICOSCRIPT_NO_BROWSER"] = "1"
        kwargs: dict = dict(
            cwd=str(repo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
        )
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        self.process = subprocess.Popen(
            [sys.executable, str(run_py)], **kwargs
        )
        self._log_thread = threading.Thread(
            target=self._drain_logs, daemon=True
        )
        self._log_thread.start()

    def _drain_logs(self) -> None:
        proc = self.process
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            self.logs.append(line.rstrip("\n"))
