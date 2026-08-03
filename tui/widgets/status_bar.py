"""Bottom status bar: connection state, hints, transient messages."""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class StatusBar(Widget):
    """Single-line status bar at the bottom of the app.

    Left/right halves are real widgets laid out with ``1fr``/``auto``
    widths, so the right-aligned hint never gets clipped the way a
    fixed-space-padded single string does at narrower terminal widths.
    """

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: #12152a;
        layout: horizontal;
    }
    StatusBar.-error { background: #ef4444; }
    StatusBar.-leader { background: #7c79f0; }
    StatusBar #status-left {
        width: 1fr;
        color: #6b6e9a;
        padding: 0 2;
    }
    StatusBar #status-right {
        width: auto;
        color: #6b6e9a;
        padding: 0 2;
    }
    StatusBar.-error #status-left, StatusBar.-error #status-right { color: #dde1ff; }
    StatusBar.-leader #status-left, StatusBar.-leader #status-right { color: #dde1ff; }
    """

    connection: reactive[str] = reactive("connecting")
    message: reactive[str] = reactive("")
    hint: reactive[str] = reactive("Space leader  ·  / palette")
    leader_hint: reactive[str] = reactive("")
    active_jobs: reactive[int] = reactive(0)

    def compose(self):
        yield Static(id="status-left")
        yield Static("[dim]Space ?[/] Help   [#ef4444]Space q[/] Quit", id="status-right")

    def on_mount(self) -> None:
        self._render_left()

    def watch_connection(self) -> None:
        self._render_left()

    def watch_message(self) -> None:
        self._render_left()

    def watch_hint(self) -> None:
        self._render_left()

    def watch_leader_hint(self) -> None:
        self._render_left()

    def watch_active_jobs(self) -> None:
        self._render_left()

    def _render_left(self) -> None:
        try:
            left = self.query_one("#status-left", Static)
        except Exception:
            return
        if self.leader_hint:
            left.update(f"LEADER · {self.leader_hint}")
            return
        conn_color = "#22c55e" if self.connection in ("connected", "connecting") else "#ef4444"
        text = f"[{conn_color}]●[/] {self.connection}"
        if self.active_jobs:
            noun = "job" if self.active_jobs == 1 else "jobs"
            text += f"  ·  [#f59e0b]⚙ {self.active_jobs} {noun} running[/]"
        if self.message:
            text += f"  ·  {self.message}"
        text += f"  ·  {self.hint}"
        left.update(text)

    def set_connection(self, state: str, ok: bool = True) -> None:
        self.connection = state
        self.set_class(not ok, "-error")

    def flash(self, msg: str) -> None:
        self.message = msg
        self.set_timer(4.0, lambda: setattr(self, "message", ""))

    def show_chord_hints(self, hints: list[tuple[str, str]]) -> None:
        pretty_key = {"question_mark": "?"}
        rendered = " · ".join(
            f"{pretty_key.get(k, k)}={lbl}" for k, lbl in hints
        )
        self.leader_hint = rendered
        self.set_class(True, "-leader")

    def clear_chord_hints(self) -> None:
        self.leader_hint = ""
        self.set_class(False, "-leader")
