#!/usr/bin/env python3
"""Smoke test for PyInstaller bundle.

Starts the built AmicoScript executable, waits for the local HTTP server to
respond, then terminates the process.

This approximates the real end-user scenario: download artifact -> run -> UI/API
comes up.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path


def _exe_path(repo_root: Path, gpu: bool = False) -> Path:
    app_name = "AmicoScript-GPU" if gpu else "AmicoScript"
    system = platform.system().lower()
    if system == "darwin":
        return repo_root / "dist" / "AmicoScript.app" / "Contents" / "MacOS" / "AmicoScript"
    if system == "windows":
        return repo_root / "dist" / app_name / f"{app_name}.exe"
    return repo_root / "dist" / app_name / app_name


def _format_output_tail(output_tail: deque[str]) -> str:
    if not output_tail:
        return "(no process output captured)"
    return "\n".join(output_tail)


def _drain_output(proc: subprocess.Popen[str], output_tail: deque[str]) -> None:
    stream = proc.stdout
    if stream is None:
        return
    try:
        for line in stream:
            output_tail.append(line.rstrip())
    except Exception:
        # If stream reading fails, smoke test can still rely on process exit code.
        pass


def _wait_http(
    url: str,
    timeout_seconds: int,
    proc: subprocess.Popen[str] | None = None,
    output_tail: deque[str] | None = None,
) -> None:
    deadline = time.time() + timeout_seconds
    last_error: str | None = None
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            tail = _format_output_tail(output_tail or deque())
            raise RuntimeError(
                f"Server process exited early with code {proc.returncode}.\n"
                f"Output tail:\n{tail}"
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if 200 <= resp.status < 300:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(0.5)
    tail = _format_output_tail(output_tail or deque())
    raise RuntimeError(
        f"Timed out waiting for {url} after {timeout_seconds}s. Last error: {last_error}\n"
        f"Output tail:\n{tail}"
    )


def _watcher_status() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:8002/api/watcher/status", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _check_meeting_watcher(output_tail: deque[str]) -> None:
    """Windows only: verify the embedded meeting watcher survived packaging.

    This regressed silently once already — the release build didn't install the
    watcher's dependencies, package.py's find_spec check quietly skipped it, and
    the shipped app ran fine with meeting auto-capture simply dead. So assert on
    it here rather than trusting the build.
    """
    if not sys.platform.startswith("win"):
        return

    # Hard check: the bundled scripts/ tree. Pure filesystem read, can't flake.
    status = _watcher_status()
    if not status.get("current_version"):
        raise RuntimeError(
            "Bundle is missing scripts/meeting_watcher/watcher.py "
            "(/api/watcher/status returned an empty current_version). "
            "Check the --add-data=scripts argument in package.py."
        )

    # Soft check: the watcher thread actually importing and heartbeating. Not
    # fatal because a CI runner's audio stack is not a user's machine, but a
    # failure here means auto-capture is broken in the shipped app.
    deadline = time.time() + 30
    while time.time() < deadline:
        if _watcher_status().get("alive"):
            print("Embedded meeting watcher is running.")
            return
        time.sleep(1)
    print(
        "WARNING: embedded meeting watcher never sent a heartbeat within 30s. "
        "Meeting auto-capture may be broken in this build.\n"
        f"Output tail:\n{_format_output_tail(output_tail)}"
    )


def main() -> int:
    gpu = '--gpu' in sys.argv
    repo_root = Path(__file__).resolve().parents[1]
    exe = _exe_path(repo_root, gpu=gpu)
    if not exe.exists():
        raise FileNotFoundError(f"Expected executable not found: {exe}")

    env = os.environ.copy()
    env["AMICOSCRIPT_NO_BROWSER"] = "1"
    timeout_seconds = int(env.get("AMICO_SMOKE_TIMEOUT", "180"))

    url = "http://127.0.0.1:8002/api/version"

    proc = None
    output_tail: deque[str] = deque(maxlen=200)
    try:
        proc = subprocess.Popen(
            [str(exe)],
            cwd=str(repo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        threading.Thread(
            target=_drain_output,
            args=(proc, output_tail),
            daemon=True,
        ).start()

        _wait_http(url, timeout_seconds=timeout_seconds, proc=proc, output_tail=output_tail)
        _check_meeting_watcher(output_tail)
        return 0
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"SMOKE TEST FAILED: {exc}", file=sys.stderr)
        raise
