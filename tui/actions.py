"""What a user can do to a recording, written once.

The TUI offers each operation three ways — a slash command, the fuzzy palette,
and a keybinding on the library screen — and each carried its own copy of the
API call, the notification and the refresh. The copies had drifted:

* Retrying from the library screen refused a recording that was still
  transcribing and explained why; ``/retry`` did neither, so the command line
  would fire a retry the screen had just declined. In exchange, only the
  command opened the new job.
* ``/analyze`` accepted a target language and a custom prompt; the palette's
  type picker had no way to pass either.
* Deleting confirmed with the same sentence in four places and reported the
  result in three different ones.

One function per operation, so which entry point the user came in through
stops being something they can feel. Each returns whether the operation
happened, reports its own failure, and leaves screen-specific follow-up (which
list to reload, what to select next) to the caller.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import explain

if TYPE_CHECKING:
    from .app import AmicoTUI

#: A recording in one of these states is finished with, one way or another, so
#: re-running it is meaningful. Mirrors core/job_status.RETRYABLE on the server,
#: which is the authority — this is a local check so the TUI can explain itself
#: without a round trip, not a second opinion.
RETRYABLE = {"error", "interrupted", "cancelled", "done"}

ANALYSIS_TYPES = [
    ("summary", "Summarise the transcript"),
    ("action_items", "Extract action items"),
    ("translate", "Translate transcript"),
    ("custom", "Run a custom prompt"),
]


def short(rec_id: str) -> str:
    """Recordings are UUIDs; eight characters is enough to recognise one."""
    return rec_id[:8]


def refresh_library(app: "AmicoTUI") -> None:
    """Reload the library list, if the screen in front of the user has one."""
    screen = app.screen
    if hasattr(screen, "refresh_library"):
        screen.refresh_library()


async def confirm(app: "AmicoTUI", question: str) -> bool:
    from .widgets.confirm import ConfirmDialog

    return bool(await app.push_screen_wait(ConfirmDialog(question)))


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


async def retry_recording(
    app: "AmicoTUI",
    rec_id: str,
    *,
    record: dict | None = None,
    follow: bool = True,
) -> bool:
    """Transcribe *rec_id* again. Returns True if it was queued.

    *record* is the row the caller already has, if any. With it the refusal for
    a recording that is still running can be explained here instead of coming
    back as a 409, and any status_detail the server left — why a restart
    interrupted it, say — is surfaced before the retry.
    """
    status = (record or {}).get("status", "")
    if status and status not in RETRYABLE:
        app.notify(f"{status} — wait for it to finish first", severity="warning")
        return False

    detail = (record or {}).get("status_detail")
    if detail:
        app.notify(detail)

    try:
        result = await app.api.retry_recording(rec_id)
    except Exception as exc:
        app.notify(explain(exc, "retry failed"), severity="error")
        return False

    app.notify(f"queued again: {short(rec_id)}…")
    job_id = (result or {}).get("job_id")
    if follow and job_id:
        await open_job(app, job_id)
    refresh_library(app)
    return True


async def delete_recording(app: "AmicoTUI", rec_id: str, *, ask: bool = True) -> bool:
    """Delete *rec_id*, asking first unless the caller already did."""
    if ask and not await confirm(
        app, f"Delete recording {short(rec_id)}…? This cannot be undone."
    ):
        return False

    app.push_busy()
    try:
        await app.api.delete_recording(rec_id)
    except Exception as exc:
        app.notify(explain(exc, "delete failed"), severity="error")
        return False
    finally:
        app.pop_busy()

    app.notify(f"deleted {short(rec_id)}")
    refresh_library(app)
    return True


async def delete_recordings(app: "AmicoTUI", rec_ids: list[str]) -> int:
    """Delete several, reporting how many made it. Returns the count deleted."""
    if not rec_ids:
        return 0
    if not await confirm(
        app, f"Delete {len(rec_ids)} recordings…? This cannot be undone."
    ):
        return 0

    app.push_busy()
    deleted = 0
    try:
        for rec_id in rec_ids:
            try:
                await app.api.delete_recording(rec_id)
                deleted += 1
            except Exception:
                # Reported in aggregate below: one failure in fifty should not
                # bury the user in fifty notifications.
                pass
    finally:
        app.pop_busy()

    failed = len(rec_ids) - deleted
    app.notify(f"deleted {deleted}/{len(rec_ids)}" + (f" ({failed} failed)" if failed else ""))
    return deleted


async def create_analysis(
    app: "AmicoTUI", rec_id: str, analysis_type: str, **extra: Any
) -> bool:
    """Queue an analysis of *rec_id*.

    *extra* carries the per-type arguments — ``target_language`` for translate,
    ``custom_prompt`` for custom — which only the slash command used to be able
    to supply.
    """
    try:
        await app.api.create_analysis(rec_id, analysis_type, **extra)
    except Exception as exc:
        app.notify(explain(exc, "analysis failed"), severity="error")
        return False
    app.notify(f"{analysis_type} analysis queued for {short(rec_id)}")
    return True


def parse_analysis_args(args: list[str]) -> tuple[str, dict[str, str]]:
    """Split ``<type> [extra...]`` into the type and its per-type argument."""
    analysis_type = args[0]
    rest = " ".join(args[1:]).strip()
    if not rest:
        return analysis_type, {}
    if analysis_type == "translate":
        return analysis_type, {"target_language": args[1]}
    if analysis_type == "custom":
        return analysis_type, {"custom_prompt": rest}
    return analysis_type, {}


async def open_job(app: "AmicoTUI", job_id: str) -> None:
    """Open the job detail screen, if the app knows how to."""
    try:
        from .screens.job_detail import JobDetailScreen

        app.push_screen(JobDetailScreen(job_id))
    except Exception:
        # Following a job is a courtesy; the job is queued either way.
        pass
