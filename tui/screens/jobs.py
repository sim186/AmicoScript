"""Single-job live progress screen via SSE."""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Header, Log, Static

from ..sse import stream_job
from ..widgets.progress_bar import JobProgress
from ..widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from ..app import AmicoTUI


class JobScreen(Screen):
    BINDINGS = [
        Binding("escape", "pop", "Back"),
        Binding("q", "pop", "Back"),
        Binding("c", "cancel", "Cancel job"),
    ]

    DEFAULT_CSS = """
    JobScreen { layout: vertical; }
    #title { padding: 0 1; height: 1; }
    Log { height: 1fr; border: tall $panel; }
    """

    def __init__(self, job_id: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.job_id = job_id

    def compose(self):
        yield Header()
        with Vertical():
            yield Static(f"job [b]{self.job_id}[/b]", id="title")
            yield JobProgress(id="prog")
            yield Log(id="log", highlight=False, max_lines=2000)
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
