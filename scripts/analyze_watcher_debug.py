"""Summarize [DEBUG-w1] timing instrumentation from a meeting-watcher log.

Run on the machine that produced the log (or anywhere, really):

    python scripts/analyze_watcher_debug.py ~/.amicoscript/data/meetings/watcher.log

Prints every timing line in order plus a per-phase verdict against rough
targets, so you can see where capture latency actually goes without
reading raw logs. All numbers come from the watcher's own perf_counter
instrumentation; this script only parses and ranks.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LINE = re.compile(r"\[DEBUG-w1\] (.+)")
KV = re.compile(r"([a-z_]+)=([\d.]+)s")

# Phase -> (target seconds, what it means if exceeded)
TARGETS = {
    "pyaudio_init": 1.0,
    "device_enumeration": 0.5,
    "loopback_resolution": 0.5,
    "mic_resolution": 0.5,
    "backend_open_session_total": 2.5,
    "stream_open": 1.0,
    "first_chunk_after_open": 1.0,
    "detect_to_first_byte_budget": 3.0,
    "session_teardown": 1.0,
    "mix_and_write": 30.0,
    "slow_status_post": 1.0,
}


def _parse(path: Path) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.search(raw)
        if not m:
            continue
        stamp = raw[:19] if raw.startswith("[") else ""
        events.append((stamp, m.group(1)))
    return events


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"not found: {path}")
        return 2
    events = _parse(path)
    if not events:
        print("no [DEBUG-w1] lines found — was the instrumented watcher used?")
        return 1

    print(f"{path} — {len(events)} debug events\n")
    overages: list[tuple[float, str]] = []
    for stamp, body in events:
        print(f"  {stamp}  {body}")
        for key, value in KV.findall(body):
            target = TARGETS.get(key)
            if target is None:
                # composite lines like poll_period avg=... max=... use avg=
                avg = re.search(r"avg=([\d.]+)s", body)
                mx = re.search(r"max=([\d.]+)s", body)
                if avg and float(avg.group(1)) > 0.75:
                    overages.append((float(mx.group(1)) if mx else 0.0,
                                     f"poll_period avg={avg.group(1)}s max={body}"))
                continue
            if value and float(value) > target:
                overages.append((float(value), f"{key}={value}s (target {target}s)"))

    print("\n— verdict —")
    if not overages:
        print("all phases within target — latency lives elsewhere "
              "(detection debounce or app-side session state)")
    else:
        print("phases over target, worst first:")
        for seconds, what in sorted(overages, reverse=True):
            print(f"  {seconds:7.2f}s  {what}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
