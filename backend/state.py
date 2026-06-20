"""Shared mutable state for the AmicoScript backend.

Centralising all globals here prevents circular imports between pipeline.py
and main.py while keeping the worker thread and the FastAPI event loop
decoupled.
"""
import asyncio
import threading
from typing import Optional

# ---------------------------------------------------------------------------
# Job store
# ---------------------------------------------------------------------------

# job_id -> job dict
jobs: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Whisper model cache + lock (guards concurrent access from worker + translate)
# ---------------------------------------------------------------------------

_cached_model = None
_cached_model_name: Optional[str] = None
_cached_model_device: Optional[str] = None
_cached_model_key: Optional[tuple] = None
_model_lock: threading.Lock = threading.Lock()

# ---------------------------------------------------------------------------
# Background job queue — initialised in main.py startup (not at import time)
# so the correct asyncio event loop is always used.
# ---------------------------------------------------------------------------

JOB_QUEUE: asyncio.Queue  # assigned by _init_queue() at startup


def _init_queue() -> None:
    global JOB_QUEUE
    JOB_QUEUE = asyncio.Queue()


# ---------------------------------------------------------------------------
# asyncio event loop — set by main.py at startup
# ---------------------------------------------------------------------------

# Kept for compatibility while older code paths are migrated.
event_loop: Optional[asyncio.AbstractEventLoop] = None

# ---------------------------------------------------------------------------
# Exit CSRF token — generated at startup, required by /api/exit
# ---------------------------------------------------------------------------

exit_token: str = ""

# ---------------------------------------------------------------------------
# Meeting-watcher status — set by the external watcher (scripts/teams_watcher)
# via POST /api/watcher/status and read by the web UI to show a "recording"
# chip. Transient (in-memory only); a stale heartbeat is treated as idle by the
# route's TTL so a crashed watcher never leaves the UI stuck on "recording".
# ---------------------------------------------------------------------------

watcher_status: dict = {"recording": False, "app": "", "ts": 0.0}
