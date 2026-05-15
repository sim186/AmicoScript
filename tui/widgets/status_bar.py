"""Bottom status bar: connection state, hints, transient messages."""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget


class StatusBar(Widget):
    """Single-line status bar at the bottom of the app."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    StatusBar.-error { background: $error; color: $text; }
    StatusBar.-ok { background: $panel; }
    StatusBar.-leader { background: $primary; color: $text; }
    """

    connection: reactive[str] = reactive("connecting")
    message: reactive[str] = reactive("")
    hint: reactive[str] = reactive("Space leader · / palette · ? help")
    leader_hint: reactive[str] = reactive("")

    def render(self) -> str:
        if self.leader_hint:
            return f"LEADER · {self.leader_hint}"
        parts = [f"● {self.connection}"]
        if self.message:
            parts.append(self.message)
        parts.append(self.hint)
        return "  ·  ".join(parts)

    def set_connection(self, state: str, ok: bool = True) -> None:
        self.connection = state
        self.set_class(not ok, "-error")
        self.set_class(ok, "-ok")

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
