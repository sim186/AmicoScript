#!/usr/bin/env python3
"""Smoke test for PyInstaller bundle.

Starts the built AmicoScript executable, waits for the local HTTP server to
respond, then terminates the process.

This approximates the real end-user scenario: download artifact -> run -> UI/API
comes up.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _exe_path(repo_root: Path) -> Path:
    system = platform.system().lower()
    if system == "darwin":
        return repo_root / "dist" / "AmicoScript.app" / "Contents" / "MacOS" / "AmicoScript"
    if system == "windows":
        return repo_root / "dist" / "AmicoScript" / "AmicoScript.exe"
    return repo_root / "dist" / "AmicoScript" / "AmicoScript"


def _wait_http(url: str, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    last_error: str | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if 200 <= resp.status < 300:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for {url}. Last error: {last_error}")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    exe = _exe_path(repo_root)
    if not exe.exists():
        raise FileNotFoundError(f"Expected executable not found: {exe}")

    env = os.environ.copy()
    env["AMICOSCRIPT_NO_BROWSER"] = "1"

    url = "http://127.0.0.1:8002/api/version"

    proc = None
    try:
        proc = subprocess.Popen(
            [str(exe)],
            cwd=str(repo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _wait_http(url, timeout_seconds=60)
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
