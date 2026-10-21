"""Latency / reliability feedback loops for meeting capture.

Each test encodes one user-visible symptom reported against meeting
recording ("starts late", "chip badge unreliable"), pinned at a seam that
runs on any OS:

* ``test_finalize_does_not_block_the_watch_loop`` — during capture teardown
  and mixing the watch loop used to freeze: no polls, no heartbeats, so the
  web UI expired the heartbeat and showed the helper as dead mid-cleanup.
  The loop must keep running while a capture finalizes.

Two Web-UI-side latency guards used to live here as source-text checks
against the pre-migration ``frontend/js/*`` modules (the manual mic-modal
track-release ordering, and the recording-chip timer never falling back to
``Date.now()``). Both modules are gone — see
docs/adr/0001-big-bang-web-ui-rewrite.md — and the guarantees they protected
need re-covering as Vitest tests in web/ once the corresponding Svelte
components exist (the chip-timer purity guarantee is already ported forward
as web/src/lib/time.test.ts; the mic-modal one is not yet re-covered).

Windows-only latency sources (device enumeration on the capture critical
path, per-poll COM churn) cannot be exercised on a CI box; they need an
instrumented run on a Windows host (see docs/ROADMAP.md, watcher gaps).
"""

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Captured at import time: _install_fast_loop's monkeypatch replaces the
# attribute on the shared stdlib threading module, so by the time a test
# body runs, "threading.Thread" no longer names the real class.
_REAL_THREAD = threading.Thread

sys.path.insert(0, str(ROOT / "tests"))
from test_meeting_watcher import _load_watcher, _install_fast_loop  # noqa: E402


def test_finalize_does_not_block_the_watch_loop(monkeypatch, tmp_path):
    """While a finished capture is being torn down + mixed, the watch loop
    must keep polling (and therefore heartbeating), and the teardown must
    not run on the loop's own thread."""
    watcher = _load_watcher(monkeypatch, tmp_path)
    _install_fast_loop(monkeypatch, watcher)
    # Finalize runs for real this time: a genuine thread, not inline.
    monkeypatch.setattr(watcher.threading, "Thread", _REAL_THREAD)
    monkeypatch.setattr(watcher, "capture_enabled", lambda: True)
    monkeypatch.setattr(watcher, "submit_recording", lambda path: None)

    release = threading.Event()
    stop_entered = threading.Event()
    loop_advanced_after_stop = threading.Event()
    seen = {"polls": 0}
    threads = {"loop": None, "stop": None}

    class BlockingCapture:
        def __init__(self, mix_mic=True):
            pass

        def start(self):
            pass

        def stop(self, out_path):
            threads["stop"] = threading.current_thread()
            stop_entered.set()
            # Simulated multi-minute mix of a long meeting.
            assert release.wait(timeout=10), "test did not release the capture"
            Path(out_path).write_bytes(b"fake wav")
            return 20.0

    def fake_call_in_progress():
        seen["polls"] += 1
        if threads["loop"] is None:
            threads["loop"] = threading.current_thread()
        if stop_entered.is_set() and seen["polls"] - _polls_at_stop[0] >= 25:
            loop_advanced_after_stop.set()
            watcher._embedded_quit.set()
        if seen["polls"] > 100_000:  # runaway guard
            watcher._embedded_quit.set()
        # Two consecutive active polls trip START_DEBOUNCE; afterwards the
        # meeting has ended and stays inactive.
        return (True, "Zoom") if seen["polls"] <= 2 else (False, "")

    _polls_at_stop = [0]

    def counting_call_in_progress():
        result = fake_call_in_progress()
        if stop_entered.is_set() and _polls_at_stop[0] == 0:
            _polls_at_stop[0] = seen["polls"]
        return result

    monkeypatch.setattr(watcher, "Capture", BlockingCapture)
    monkeypatch.setattr(watcher, "call_in_progress", counting_call_in_progress)

    loop_thread = threading.Thread(target=watcher._main_loop, daemon=True)
    loop_thread.start()

    try:
        assert stop_entered.wait(timeout=5), (
            "capture never reached teardown — start/stop detection is broken"
        )
        assert loop_advanced_after_stop.wait(timeout=5), (
            "watch loop froze during capture finalize: no polls ran while "
            "the capture was being torn down and mixed, so heartbeats stall "
            "and the web UI shows the helper as dead"
        )
    finally:
        release.set()
        watcher._embedded_quit.set()
        loop_thread.join(timeout=10)

    assert not loop_thread.is_alive(), "watch loop did not terminate"
    assert threads["stop"] is not threads["loop"], (
        "capture teardown/mixing ran on the watch-loop thread, blocking "
        "detection and heartbeats for the whole duration"
    )


# The mic-modal track-release guard and the chip-timer-never-falls-back-to-now
# guard used to live here as source-text checks against frontend/js/library-init.js
# and frontend/js/watcher.js. Both files are gone (docs/adr/0001-big-bang-web-ui-rewrite.md).
# The chip-timer guarantee is ported forward as web/src/lib/time.test.ts; the
# mic-modal one still needs re-covering in web/ once that feature is rebuilt.
