"""Welcome screen: hints + leader chord landing.

No tabs. Bare ``l``/``j``/``s`` are shortcuts. ``Space`` arms the
leader and the StatusBar shows next-key hints.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

if TYPE_CHECKING:
    from ..app import AmicoTUI


LOGO = "\n".join([
    "      ▄▄▀▀▀▄▄     ",
    "   ▄▄▀       ▀█▄  ",
    " ▄▀       ▄██▄  ▀▄",
    " █   █  ▄ ▀███   █",
    " ▀█▄█▀█▄▀▄████▄  █",
    "▀█ ██ ██ ▀▄▄█▄▀  █",
    " █    ▀▀   ██▄   █",
    " ▀▄       ████  ▄▀",
    "   ▀▀▄  █▀   ▀▄▀  ",
    "      ▀▀▄▄▄▀▀     ",
])

HINTS = """\
[b]AmicoScript[/b] · local-first transcription

[b $accent]Space l[/]   Library      [b $accent]Space j[/]   Jobs
[b $accent]Space s[/]   Settings     [b $accent]Space ?[/]   Help
[b $accent]Space q[/]   Quit         [b $accent]/  ·  ^K[/]  Palette

Direct keys (welcome only): [b]l[/] [b]j[/] [b]s[/]
Drop an audio file onto the terminal to transcribe.
"""


class WelcomeScreen(Screen):
    """Landing screen. Bare letters jump; Space arms leader chord."""

    BINDINGS = [
        Binding("l", "go('/library')", show=False),
        Binding("j", "go('/jobs')", show=False),
        Binding("s", "go('/settings')", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    leader_chords = {
        "l": ("Library", "/library"),
        "j": ("Jobs", "/jobs"),
        "s": ("Settings", "/settings"),
        "question_mark": ("Help", "/help"),
        "q": ("Quit", "/quit"),
    }

    DEFAULT_CSS = """
    WelcomeScreen { layout: vertical; }
    #main {
        width: 100%;
        height: 1fr;
        align: center middle;
    }
    #stack {
        width: auto;
        height: auto;
    }
    #logo {
        color: $primary;
        text-style: bold;
        content-align: center middle;
        width: 60;
        height: auto;
        margin-bottom: 1;
    }
    #version {
        color: $accent;
        content-align: center middle;
        height: 1;
        width: 60;
        margin-bottom: 1;
    }
    #hints {
        height: auto;
        width: 60;
        padding: 1 2;
        background: $panel;
        border: tall $primary;
        color: $text;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.title = "AmicoScript"

    def compose(self):
        from textual.containers import Container
        from ..widgets.status_bar import StatusBar
        yield Header(show_clock=False)
        with Container(id="main"):
            with Vertical(id="stack"):
                yield Static(LOGO, id="logo")
                yield Static("local-first · privacy-focused · whisper", id="version")
                yield Static(HINTS, id="hints")
        yield StatusBar(id="statusbar")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._fetch_version(), exclusive=True)

    async def _fetch_version(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            v = await app.api.version()
            ver = v.get("version") if isinstance(v, dict) else str(v)
            if ver:
                self.query_one("#version", Static).update(
                    f"v{ver}  ·  local-first  ·  privacy-focused  ·  whisper"
                )
        except Exception:
            pass

    async def action_go(self, cmd: str) -> None:
        from ..commands import run_command
        await run_command(self.app, cmd)

    def action_help(self) -> None:
        self.app.notify(
            "Space + l/j/s/q · / palette · ? help",
            timeout=6,
        )

    def action_quit(self) -> None:
        self.app.exit()
