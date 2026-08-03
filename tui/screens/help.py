"""Scrollable command / keybinding reference (Space ? or /help)."""
from __future__ import annotations

from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from ..commands import list_commands
from ..widgets.chrome import CommandBar, ContextHint, TitleBar
from ..widgets.status_bar import StatusBar

LEADER_CHEATSHEET = (
    "[b #6b6e9a]LEADER CHORDS (Space + …)[/]\n"
    "  l  Library        j  Jobs           s  Settings\n"
    "  i  Import         h  Welcome        ?  This screen\n"
    "  q  Quit\n"
    "\n"
    "[b #6b6e9a]PALETTE[/]\n"
    "  /            open palette (commands)\n"
    "  @            open palette (transcripts)\n"
    "  Ctrl+K       open palette (free fuzzy)\n"
    "  Ctrl+P       open palette (commands)\n"
    "  Tab          autocomplete / cycle\n"
    "  ↑↓ / Shift+Tab   move selection\n"
    "  Enter        activate\n"
    "  Escape       close\n"
)


class HelpScreen(Screen):
    BINDINGS = [
        Binding("escape", "pop", "Back"),
        Binding("q", "pop", "Back"),
    ]

    leader_chords = {
        "l": ("Library", "/library"),
        "j": ("Jobs", "/jobs"),
        "s": ("Settings", "/settings"),
        "h": ("Welcome", "/welcome"),
        "q": ("Quit", "/quit"),
    }

    DEFAULT_CSS = """
    HelpScreen { layout: vertical; }
    VerticalScroll { height: 1fr; padding: 1 2; }
    #commands { padding-top: 1; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.title = "Help"

    def compose(self):
        yield TitleBar(id="titlebar")
        with Vertical():
            with VerticalScroll():
                yield Static(LEADER_CHEATSHEET, id="cheatsheet")
                lines = "\n".join(
                    f"  /{c.name:<16} {c.help}" for c in list_commands()
                )
                yield Static(
                    f"[b #6b6e9a]SLASH COMMANDS[/]\n{lines}", id="commands"
                )
        yield ContextHint("Esc / q back  ·  Space h welcome", id="ctxhint")
        yield CommandBar(id="cmdbar")
        yield StatusBar(id="statusbar")

    def action_pop(self) -> None:
        self.app.pop_screen()
