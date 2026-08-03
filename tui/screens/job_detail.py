"""Single-job live progress screen via SSE."""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Log, Static

from ..sse import stream_job
from ..widgets.chrome import CommandBar, ContextHint, TitleBar
from ..widgets.progress_bar import JobProgress
from ..widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from ..app import AmicoTUI


def _fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class JobDetailScreen(Screen):
    BINDINGS = [
        Binding("escape", "pop", "Back"),
        Binding("q", "pop", "Back"),
        Binding("c", "cancel", "Cancel job"),
    ]

    leader_chords = {
        "l": ("Library", "/library"),
        "j": ("Jobs", "/jobs"),
        "s": ("Settings", "/settings"),
        "h": ("Welcome", "/welcome"),
        "question_mark": ("Help", "/help"),
        "q": ("Quit", "/quit"),
    }

    DEFAULT_CSS = """
    JobDetailScreen { layout: vertical; }
    #title { padding: 0 1; height: 1; }
    Log { height: 1fr; border: tall $panel; }
    """

    def __init__(self, job_id: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.job_id = job_id
        self.title = f"Job · {job_id[:8]}"

    def compose(self):
        yield TitleBar(id="titlebar")
        with Vertical():
            yield Static(f"job [b]{self.job_id}[/b]", id="title")
            yield JobProgress(id="prog")
            yield Log(id="log", highlight=False, max_lines=2000)
        yield ContextHint("c cancel job  ·  Esc / q back", id="ctxhint")
        yield CommandBar(id="cmdbar")
        yield StatusBar(id="statusbar")

    def on_mount(self) -> None:
        self.run_worker(self._stream(), exclusive=True)

    async def _stream(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        prog = self.query_one(JobProgress)
        log = self.query_one(Log)
        status = self.query_one(StatusBar)
        try:
            async for evt in stream_job(app.api.client, app.api.base_url, self.job_id):
                if not isinstance(evt, dict):
                    continue
                if "progress" in evt:
                    try:
                        prog.progress = float(evt["progress"])
                    except (TypeError, ValueError):
                        pass
                if "message" in evt and evt["message"]:
                    prog.label = str(evt["message"])
                # Each transcribed segment arrives as evt["data"]["segment"] —
                # show the actual words as they're produced instead of just
                # the generic "Transcribing... 00:12 / 05:30" progress line.
                segment = (evt.get("data") or {}).get("segment")
                text = (segment or {}).get("text", "").strip()
                if text:
                    ts = _fmt_ts(float(segment.get("start", 0.0)))
                    log.write_line(f"[{ts}] {text}")
                elif "message" in evt and evt["message"]:
                    log.write_line(str(evt["message"]))
                if "log" in evt and evt["log"]:
                    log.write_line(str(evt["log"]))
                if evt.get("status") in {"done", "error", "completed", "cancelled"}:
                    status.flash(f"job {evt['status']}")
                    break
        except Exception as e:
            status.set_connection(f"stream error: {e}", ok=False)

    def action_pop(self) -> None:
        self.app.pop_screen()

    def action_cancel(self) -> None:
        self.run_worker(self._cancel(), exclusive=False)

    async def _cancel(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            await app.api.cancel_job(self.job_id)
            self.query_one(StatusBar).flash("cancel sent")
        except Exception as e:
            self.query_one(StatusBar).flash(f"cancel failed: {e}")
