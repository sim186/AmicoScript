"""Who owns "when did the current recording start" — one place, one clock.

The watcher subprocess used to send its own wall-clock started_at, and the
server guessed from timestamp ordering whether a new value meant a genuine
restart or a stale replay — see the five watcher commits that each patched a
different symptom of that guess. The server already receives an explicit
recording=false -> true / true -> false transition from the watcher, so it
doesn't need to guess: this module trusts that transition, stamps its own
clock, and persists the result so a server restart mid-recording doesn't lose
it.
"""
from __future__ import annotations

import time
from typing import Optional

from models import WatcherState
from sqlmodel import Session

_ROW_ID = "singleton"


def get_started_at(session: Session) -> Optional[float]:
    """When the current recording started, or None if nothing is recording."""
    row = session.get(WatcherState, _ROW_ID)
    return row.started_at if row else None


def report_recording(session: Session, is_recording: bool) -> Optional[float]:
    """Record the watcher's recording=<is_recording> transition.

    Returns the current started_at (None if not recording). Stamps the
    server's own clock on the false -> true edge; a repeated True while
    already recording (every heartbeat) is a no-op.
    """
    row = session.get(WatcherState, _ROW_ID)
    if row is None:
        row = WatcherState(id=_ROW_ID)

    if is_recording:
        if row.started_at is None:
            row.started_at = time.time()
    else:
        row.started_at = None

    session.add(row)
    session.commit()
    return row.started_at
