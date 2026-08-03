"""Active-jobs list with live progress bars."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import DataTable

from ..widgets.chrome import CommandBar, ContextHint, TitleBar
from ..widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from ..app import AmicoTUI


def _fmt_started(ts: float) -> str:
    if not ts:
        return ""
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _progress_bar(pct: float, width: int = 24, color: str = "#7c79f0") -> Text:
    p = max(0.0, min(1.0, pct))
    filled = int(round(p * width))
    out = Text()
    out.append("█" * filled, style=color)
    out.append("░" * (width - filled), style="#2a2860")
    out.append(f"  {int(p * 100):3d}%", style=color)
    return out


class JobsPanel(Widget):
    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("c", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    JobsPanel { layout: vertical; height: 1fr; }
    DataTable { height: 1fr; background: #0c0e1a; }
    """

    def __init__(self, on_count=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.table: DataTable | None = None
        self.job_ids: list[str] = []
        self.on_count = on_count

    def compose(self):
        with Vertical():
            yield DataTable(cursor_type="row", zebra_stripes=False)

    def on_mount(self) -> None:
        self.table = self.query_one(DataTable)
        self.table.add_columns("FILE", "STATUS", "STARTED", "PROGRESS", "ACTION")
        self.refresh_jobs()
        self.set_interval(2.0, self.refresh_jobs)

    def action_refresh(self) -> None:
        self.refresh_jobs()

    def action_cursor_down(self) -> None:
        if self.table:
            self.table.action_cursor_down()

    def action_cursor_up(self) -> None:
        if self.table:
            self.table.action_cursor_up()

    def action_cancel(self) -> None:
        if not self.table or self.table.row_count == 0:
            return
        idx = self.table.cursor_row
        if not (0 <= idx < len(self.job_ids)):
            return
        from ..commands import run_command
        self.run_worker(run_command(self.app, f"cancel {self.job_ids[idx]}"))

    def refresh_jobs(self) -> None:
        self.run_worker(self._load(), exclusive=True)

    async def _load(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            data = await app.api.jobs()
        except Exception as e:
            self.app.notify(f"jobs load failed: {e}", severity="error")
            return
        rows = data.get("jobs", []) if isinstance(data, dict) else []
        assert self.table is not None
        self.table.clear()
        self.job_ids.clear()
        for j in rows:
            jid = str(j.get("id", ""))
            fname = j.get("filename") or j.get("source_url") or jid
            status = j.get("status", "")
            pct = 0.0
            try:
                pct = float(j.get("progress") or 0.0)
            except (TypeError, ValueError):
                pass
            if pct > 1.0:
                pct = pct / 100.0
            color = "#22c55e" if status in ("done", "completed") else "#f59e0b"
            self.table.add_row(
                Text(f"⠸ {fname}", style="#f59e0b"),
                Text(status, style=color),
                Text(_fmt_started(j.get("created_at") or 0), style="#6b6e9a"),
                _progress_bar(pct, width=24, color=color),
                Text("/cancel", style="#ef4444"),
            )
            self.job_ids.append(jid)
        if self.on_count:
            self.on_count(len(rows))


class JobsListScreen(Screen):
    """List of active jobs — replaces old filtered-library Jobs view."""

    BINDINGS = [Binding("escape", "pop", "Back")]

    leader_chords = {
        "l": ("Library", "/library"),
        "s": ("Settings", "/settings"),
        "i": ("Import", "/import"),
        "h": ("Welcome", "/welcome"),
        "question_mark": ("Help", "/help"),
        "q": ("Quit", "/quit"),
    }

    DEFAULT_CSS = """
    JobsListScreen { layout: vertical; }
    JobsPanel { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.title = "Jobs"

    def compose(self):
        yield TitleBar(id="titlebar")
        with Vertical():
            yield JobsPanel(on_count=self._on_count, id="jobs_panel")
        yield ContextHint(
            "0 active  ·  c cancel selected  ·  /cancel <id>  ·  ↵ open detail",
            id="ctxhint",
        )
        yield CommandBar(id="cmdbar")
        yield StatusBar(id="statusbar")

    def on_mount(self) -> None:
        try:
            self.query_one(DataTable).focus()
        except Exception:
            pass

    def _on_count(self, n: int) -> None:
        try:
            self.query_one("#ctxhint", ContextHint).set_text(
                f"{n} active  ·  c cancel selected  ·  /cancel <id>  ·  ↵ open detail"
            )
        except Exception:
            pass

    def action_pop(self) -> None:
        self.app.pop_screen()
