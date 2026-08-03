"""Bottom status bar: connection state, hints, transient messages."""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget


class StatusBar(Widget):
    """Single-line status bar at the bottom of the app."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: #12152a;
        color: #6b6e9a;
        padding: 0 2;
    }
    StatusBar.-error { background: #ef4444; color: #dde1ff; }
    StatusBar.-leader { background: #7c79f0; color: #dde1ff; }
    """

    connection: reactive[str] = reactive("connecting")
    message: reactive[str] = reactive("")
    hint: reactive[str] = reactive("Space leader  ·  / palette")
    leader_hint: reactive[str] = reactive("")
    active_jobs: reactive[int] = reactive(0)

    def render(self) -> str:
        if self.leader_hint:
            return f"LEADER · {self.leader_hint}"
        conn_color = "#22c55e" if self.connection in ("connected", "connecting") else "#ef4444"
        left = f"[{conn_color}]●[/] {self.connection}"
        if self.active_jobs:
            noun = "job" if self.active_jobs == 1 else "jobs"
            left += f"  ·  [#f59e0b]⚙ {self.active_jobs} {noun} running[/]"
        if self.message:
            left += f"  ·  {self.message}"
        left += f"  ·  {self.hint}"
        right = "[dim]Space ?[/] Help   [#ef4444]Space q[/] Quit"
        # Padding via spaces between left/right not reliable; use just left and let widget align.
        return f"{left}                                                                 {right}"

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
