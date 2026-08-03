"""Leader-key (Space) chord dispatcher.

Each Screen optionally declares ``leader_chords`` as a dict
``{key: (label, command_string)}``. Pressing the leader key arms the
dispatcher; the next key resolves against the current screen's chord
map and runs the associated slash-command. Esc or timeout cancels.

The App holds one ``LeaderDispatcher`` instance and forwards key
events to ``handle_key``; the StatusBar listens for the armed/cleared
state to render the next-key hints.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.message import Message

from .commands import run_command

if TYPE_CHECKING:
    from textual.events import Key

    from .app import AmicoTUI


LEADER_KEY = "space"
LEADER_TIMEOUT_S = 1.5


class LeaderArmed(Message):
    def __init__(self, hints: list[tuple[str, str]]) -> None:
        super().__init__()
        self.hints = hints


class LeaderCleared(Message):
    pass


class LeaderDispatcher:
    def __init__(self, app: "AmicoTUI") -> None:
        self.app = app
        self._armed = False
        self._timer = None

    def _current_map(self) -> dict[str, tuple[str, str]]:
        screen = self.app.screen
        return dict(getattr(screen, "leader_chords", {}) or {})

    def handle_key(self, event: "Key") -> bool:
        """Return True if event consumed."""
        if self._armed:
            if event.key == "escape":
                self._clear()
                return True
            chord_map = self._current_map()
            entry = chord_map.get(event.key)
            self._clear()
            if entry is None:
                return True  # eat unknown key while armed
            _label, cmd = entry
            self.app.run_worker(run_command(self.app, cmd), exclusive=False)
            return True
        if event.key == LEADER_KEY:
            chord_map = self._current_map()
            if not chord_map:
                return False
            self._arm(chord_map)
            return True
        return False

    def _arm(self, chord_map: dict[str, tuple[str, str]]) -> None:
        self._armed = True
        hints = [(k, lbl) for k, (lbl, _cmd) in chord_map.items()]
        self._notify_bars("show_chord_hints", hints)
        self._timer = self.app.set_timer(LEADER_TIMEOUT_S, self._timeout)

    def _timeout(self) -> None:
        if self._armed:
            self._clear()

    def _clear(self) -> None:
        self._armed = False
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass
            self._timer = None
        self._notify_bars("clear_chord_hints")

    def _notify_bars(self, method: str, *args) -> None:
        from .widgets.status_bar import StatusBar
        try:
            for bar in self.app.query(StatusBar):
                fn = getattr(bar, method, None)
                if callable(fn):
                    fn(*args)
        except Exception:
            pass
