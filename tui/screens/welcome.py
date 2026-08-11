"""Welcome / home screen — root layer, always visible when closing palette or ESC."""
from __future__ import annotations

from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Static


class WelcomeScreen(Screen):
    """Root welcome screen. Never popped — always revealed on palette close / ESC."""

    leader_chords = {
        "l": ("Library", "/library"),
        "i": ("Import", "/import"),
        "j": ("Jobs", "/jobs"),
        "s": ("Settings", "/settings"),
        "m": ("Models", "/models"),
        "question_mark": ("Help", "/help"),
        "q": ("Quit", "/quit"),
    }

    # Bare l/j/s jump directly on the welcome screen (README "Keys" section);
    # every other screen requires the Space leader first.
    BINDINGS = [
        Binding("l", "goto('/library')", show=False),
        Binding("j", "goto('/jobs')", show=False),
        Binding("s", "goto('/settings')", show=False),
    ]

    DEFAULT_CSS = """
    WelcomeScreen {
        layout: vertical;
    }
    WelcomeScreen > Container {
        height: 1fr;
        align: center middle;
    }
    #welcome-panel {
        width: auto;
        height: auto;
        border: round #4a47c0;
        padding: 1 5;
        align: center middle;
    }
    #app-title {
        color: #dde1ff;
        width: 100%;
        text-align: center;
        height: auto;
    }
    #tagline {
        color: #6b6e9a;
        width: 100%;
        text-align: center;
        height: auto;
        padding: 0 0 1 0;
    }
    #quick-actions {
        color: #dde1ff;
        width: auto;
        height: auto;
        padding: 1 0;
    }
    #keyref {
        color: #3a3d6a;
        width: 100%;
        text-align: center;
        height: auto;
        padding: 1 0 0 0;
    }
    """

    def compose(self):
        with Container():
            with Vertical(id="welcome-panel"):
                yield Static("AmicoScript", id="app-title")
                yield Static(
                    "local-first audio & video transcription", id="tagline"
                )
                yield Static(
                    "[bold #7c79f0]/[/] [bold #dde1ff]library[/]      browse recordings\n"
                    "[bold #7c79f0]/[/] [bold #dde1ff]transcribe[/]   upload & transcribe a file\n"
                    "[bold #7c79f0]/[/] [bold #dde1ff]import[/]       browse filesystem\n"
                    "[bold #7c79f0]/[/] [bold #dde1ff]jobs[/]         active & completed jobs\n"
                    "[bold #7c79f0]/[/] [bold #dde1ff]settings[/]     configure models & tokens\n"
                    "[bold #7c79f0]/[/] [bold #dde1ff]search[/]       full-text search transcripts\n"
                    "[bold #7c79f0]/[/] [bold #dde1ff]models[/]       pick Whisper model\n"
                    "[bold #7c79f0]/[/] [bold #dde1ff]llm[/]          pick LLM model",
                    id="quick-actions",
                )
                yield Static(
                    "[dim]ctrl+k[/] palette  ·  [dim]space[/] leader\n"
                    "[dim]space ?[/] help  ·  [dim]ctrl+c[/] quit",
                    id="keyref",
                )
        from ..widgets.status_bar import StatusBar
        yield StatusBar(id="statusbar")

    def action_goto(self, cmd: str) -> None:
        from ..commands import run_command
        self.run_worker(run_command(self.app, cmd), exclusive=False)
