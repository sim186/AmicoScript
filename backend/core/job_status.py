"""The states a job moves through, and the sets that group them.

Every consumer used to spell out its own tuple of "still running" statuses —
five of them, no two alike — and the divergences were not cosmetic. The queue
endpoint omitted ``loading_model``, so a job vanished from the UI for the whole
model load and reappeared once the first segment arrived. The hourly cleanup
omitted ``downloading`` and the analysis states, so a long playlist import or a
map-reduce summary older than an hour was treated as abandoned: its temp files
were deleted and its entry replaced by a tombstone, which left the worker
pushing events into a job record that no longer had an SSE queue.

Anything that needs to ask "is this job still going?" asks here.
"""
from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    """A background job's status — and the Recording row's, which mirrors it.

    The values are the strings the API and the SSE stream already publish, so a
    member compares equal to the plain string a client sends back and can be
    used interchangeably with it.
    """

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    LOADING_MODEL = "loading_model"
    TRANSCRIBING = "transcribing"
    DIARIZING = "diarizing"
    TRANSLATING = "translating"
    # Analysis jobs: RUNNING while a prompt is built or a request is in flight,
    # STREAMING once tokens are coming back.
    RUNNING = "running"
    STREAMING = "streaming"
    # Something optional was skipped — diarization, in practice — and the job
    # carried on regardless. Transient, not terminal.
    WARNING = "warning"

    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"

    # Recording-only. A row is PENDING before any job exists for it, and
    # INTERRUPTED when a restart killed its job and the audio did not survive.
    # No job ever reports either.
    PENDING = "pending"
    INTERRUPTED = "interrupted"


# A status written by an earlier version of the download phase. Nothing sets it
# today, but rows in existing databases still carry it, so recovery has to keep
# recognising it.
LEGACY_PREPARING = "preparing"


#: The job still has work to do: it must not be expired, and it belongs in the
#: queue strip. WARNING is included because it is transient — diarization was
#: skipped and transcription is still running.
ACTIVE: frozenset[str] = frozenset({
    JobStatus.QUEUED,
    JobStatus.DOWNLOADING,
    JobStatus.LOADING_MODEL,
    JobStatus.TRANSCRIBING,
    JobStatus.DIARIZING,
    JobStatus.TRANSLATING,
    JobStatus.RUNNING,
    JobStatus.STREAMING,
    JobStatus.WARNING,
    LEGACY_PREPARING,
})

#: The job will not change state again by itself.
TERMINAL: frozenset[str] = frozenset({
    JobStatus.DONE,
    JobStatus.ERROR,
    JobStatus.CANCELLED,
})

#: Recording statuses that mean a restart interrupted a transcription, so the
#: row can go back on the queue if its audio is still on disk.
#:
#: Deliberately narrower than ACTIVE: recovery rebuilds a *transcription* job,
#: so the analysis states must not appear here or a finished recording would be
#: transcribed a second time to recover an analysis.
RESUMABLE: frozenset[str] = frozenset({
    JobStatus.QUEUED,
    JobStatus.DOWNLOADING,
    JobStatus.LOADING_MODEL,
    JobStatus.TRANSCRIBING,
    JobStatus.DIARIZING,
    JobStatus.TRANSLATING,
    LEGACY_PREPARING,
})

#: A recording in one of these states is finished with, one way or another, so
#: re-running it is meaningful. Anything else is already in flight.
RETRYABLE: frozenset[str] = frozenset({
    JobStatus.ERROR,
    JobStatus.INTERRUPTED,
    JobStatus.CANCELLED,
    JobStatus.DONE,
})


def is_active(status: str | None) -> bool:
    """True while *status* means the job still has work left to do."""
    return status in ACTIVE
