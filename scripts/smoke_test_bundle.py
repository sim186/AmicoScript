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


def _exe_path(repo_root: Path) -> Path:
    app_name = "AmicoScript"
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


def _bundle_roots(exe: Path) -> list[Path]:
    """Everywhere PyInstaller may have put collected files, for this platform.

    A onedir build keeps them in `_internal` beside the executable. A macOS
    .app splits them: binaries under Contents/Frameworks, data under
    Contents/Resources, with symlinks between the two. Guessing one and
    checking only there would pass on macOS for the wrong reason.
    """
    if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        contents = exe.parent.parent
        return [d for d in (contents / "Frameworks", contents / "Resources", contents / "MacOS")
                if d.is_dir()]

    internal = exe.parent / "_internal"
    return [internal if internal.is_dir() else exe.parent]


def _check_runtime_pack_layout(exe: Path) -> None:
    """The packaging contract the download depends on, asserted rather than assumed.

    torch is downloaded at first use, and that only works if it is genuinely
    not in the bundle: PyInstaller's FrozenImporter sits ahead of every
    path-based finder, so a bundled copy would win over the downloaded one and
    the download would be so much wasted disk. A build machine that happens to
    have torch installed is all it takes — which is a thing CI does to itself
    every time the test suite runs somewhere near the build.

    Cheap filesystem reads, so this cannot flake.
    """
    roots = _bundle_roots(exe)

    stowaways = sorted({
        name
        for root in roots
        for name in ("torch", "torchaudio", "pyannote", "nvidia")
        if (root / name).exists()
    })
    if stowaways:
        raise RuntimeError(
            f"{', '.join(stowaways)} was bundled into the app. These are meant to be "
            "downloaded at first use, and a bundled copy silently wins over the "
            "downloaded one. Check the --exclude-module list in package.py, and "
            "that the build environment does not have them installed."
        )

    manifest = next((root / "runtime_manifest.json" for root in roots
                     if (root / "runtime_manifest.json").is_file()), None)
    if manifest is None:
        raise RuntimeError(
            "runtime_manifest.json is missing from "
            f"{', '.join(str(root) for root in roots)}. This build cannot "
            "download PyTorch, so speaker diarization is dead in it. Run "
            "scripts/generate_runtime_manifest.py before package.py."
        )

    try:
        variants = json.loads(manifest.read_text(encoding="utf-8")).get("variants") or {}
    except ValueError as exc:
        raise RuntimeError(f"runtime_manifest.json is not valid JSON: {exc}") from exc

    empty = [name for name, spec in variants.items() if not (spec or {}).get("wheels")]
    if not variants or empty:
        raise RuntimeError(
            f"runtime_manifest.json lists no wheels for {', '.join(empty) or 'any flavour'}."
        )

    summary = ", ".join(f"{name} ({len(spec['wheels'])} wheels)" for name, spec in variants.items())
    print(f"Runtime pack manifest: {summary}")


def _watcher_status() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:8002/api/watcher/status", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _check_meeting_watcher(output_tail: deque[str]) -> None:
    """Verify the embedded meeting watcher survived packaging.

    This regressed silently once already — the release build didn't install the
    watcher's dependencies, package.py's find_spec check quietly skipped it, and
    the shipped app ran fine with meeting auto-capture simply dead. So assert on
    it here rather than trusting the build.
    """
    # Hard check: the bundled scripts/ tree. Pure filesystem read, can't flake,
    # and true on every platform — scripts/ is always bundled, so a missing
    # watcher.py is a packaging bug wherever it shows up.
    status = _watcher_status()
    if not status.get("current_version"):
        raise RuntimeError(
            "Bundle is missing scripts/meeting_watcher/watcher.py "
            "(/api/watcher/status returned an empty current_version). "
            "Check the --add-data=scripts argument in package.py."
        )

    # The heartbeat check below needs the watcher thread to have actually
    # started, which only happens on a host with a backend.
    if not (sys.platform.startswith("win") or sys.platform == "darwin"):
        return

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
    repo_root = Path(__file__).resolve().parents[1]
    exe = _exe_path(repo_root)
    if not exe.exists():
        raise FileNotFoundError(f"Expected executable not found: {exe}")

    # Before starting anything: if the bundle is shaped wrong, nothing the
    # running app reports about itself is worth reading.
    _check_runtime_pack_layout(exe)

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
