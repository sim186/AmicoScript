"""Live server log tail screen."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Log

from ..widgets.chrome import CommandBar, ContextHint, TitleBar
from ..widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from ..app import AmicoTUI


LEVEL_RE = re.compile(r"\b(INFO|WARN(?:ING)?|ERROR|DEBUG|CRITICAL)\b")


class LogsScreen(Screen):
    BINDINGS = [
        Binding("escape", "pop", "Back"),
        Binding("q", "pop", "Back"),
        Binding("c", "clear", "Clear"),
    ]

    leader_chords = {
        "l": ("Library", "/library"),
        "j": ("Jobs", "/jobs"),
        "s": ("Settings", "/settings"),
        "q": ("Back", "/quit"),
    }

    DEFAULT_CSS = """
    LogsScreen { layout: vertical; }
    Log {
        height: 1fr;
        background: #080a14;
        color: #6b6e9a;
        border: none;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.title = "Logs"
        self._last_n = 0

    def compose(self):
        yield TitleBar(id="titlebar")
        with Vertical():
            yield Log(id="loglines", highlight=False, max_lines=5000)
        yield ContextHint(
            "live tail  ·  c clear  ·  /logs filter <level>  ·  Esc close",
            id="ctxhint",
        )
        yield CommandBar(id="cmdbar")
        yield StatusBar(id="statusbar")

    def on_mount(self) -> None:
        self._refresh_all()
        self.set_interval(0.5, self._poll)

    def _refresh_all(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        log = self.query_one(Log)
        log.clear()
        if not app.server or not app.server.logs:
            log.write_line("(no captured logs — running in --no-server mode)")
            return
        lines = list(app.server.logs)
        self._last_n = len(lines)
        for line in lines:
            log.write_line(self._style(line))

    def _poll(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        if not app.server or not app.server.logs:
            return
        lines = list(app.server.logs)
        if len(lines) <= self._last_n:
            return
        log = self.query_one(Log)
        for line in lines[self._last_n:]:
            log.write_line(self._style(line))
        self._last_n = len(lines)

    def _style(self, line: str) -> str:
        m = LEVEL_RE.search(line)
        if not m:
            return line
        level = m.group(1)
        color = {
            "INFO": "#2dd4bf",
            "WARN": "#f59e0b",
            "WARNING": "#f59e0b",
            "ERROR": "#ef4444",
            "CRITICAL": "#ef4444",
            "DEBUG": "#6b6e9a",
        }.get(level, "#6b6e9a")
        # Log widget supports limited markup via highlight=False — strip styling.
        # Return plain text; coloring via inline markup not supported in Log widget cleanly.
        return line

    def action_clear(self) -> None:
        self.query_one(Log).clear()
        self._last_n = 0

    def action_pop(self) -> None:
        self.app.pop_screen()
