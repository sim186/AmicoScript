"""Welcome / home screen — root layer, always visible when closing palette or ESC."""
from __future__ import annotations

from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static


LOGO_ART = (
    "[#7c79f0]▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄[/]\n"
    "[#7c79f0]██[/][#dde1ff]▌[/]                               [#7c79f0]▐██[/]\n"
    "[#7c79f0]██[/][#dde1ff]▌[/]   [#7c79f0]█████[/]  [#7c79f0]█[/][#dde1ff]▌[/]  [#7c79f0]█[/][#dde1ff]▌[/] [#7c79f0]█████[/]  [#7c79f0]█████[/]   [#7c79f0]▐██[/]\n"
    "[#7c79f0]██[/][#dde1ff]▌[/]  [#7c79f0]█[/][#dde1ff]▀▀▀▀▀█[/][#7c79f0]▌[/] [#7c79f0]███▌[/] [#7c79f0]█[/][#dde1ff]▌[/]  [#7c79f0]█[/][#dde1ff]▌[/]  [#7c79f0]█[/][#dde1ff]▌[/]     [#7c79f0]▐██[/]\n"
    "[#7c79f0]██[/][#dde1ff]▌[/]  [#7c79f0]███████▌[/] [#7c79f0]█ █▌[/] [#7c79f0]█[/][#dde1ff]▌[/]  [#7c79f0]█[/][#dde1ff]▌[/]  [#7c79f0]████▄[/]   [#7c79f0]▐██[/]\n"
    "[#7c79f0]██[/][#dde1ff]▌[/]  [#7c79f0]█[/][#dde1ff]▀▀▀▀▀█[/][#7c79f0]▌[/] [#7c79f0]█ █▌[/] [#7c79f0]█[/][#dde1ff]▌[/]  [#7c79f0]█[/][#dde1ff]▌[/]  [#7c79f0]█[/][#dde1ff]▀▀[/]    [#7c79f0]▐██[/]\n"
    "[#7c79f0]██[/][#dde1ff]▌[/]  [#7c79f0]█[/][#dde1ff]▌[/]   [#7c79f0]█▌[/] [#7c79f0]█ █▌[/] [#7c79f0]█████▌[/] [#7c79f0]█████[/]   [#7c79f0]▐██[/]\n"
    "[#7c79f0]██[/][#dde1ff]▌[/]                               [#7c79f0]▐██[/]\n"
    "[#7c79f0]▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀[/]"
)


class WelcomeScreen(Screen):
    """Root welcome screen. Never popped — always revealed on palette close / ESC."""

    leader_chords = {
        "l": ("Library", "/library"),
        "i": ("Import", "/import"),
        "j": ("Jobs", "/jobs"),
        "s": ("Settings", "/settings"),
        "m": ("Models", "/models"),
        "q": ("Quit", "/quit"),
    }

    DEFAULT_CSS = """
    WelcomeScreen {
        layout: vertical;
    }
    WelcomeScreen Container {
        height: 1fr;
        align: center middle;
    }
    #welcome-panel {
        width: auto;
        height: auto;
        align: center middle;
    }
    #left-col {
        width: 1fr;
        height: auto;
        padding: 0 4;
    }
    #logo-ascii {
        width: 48;
        height: auto;
        content-align: center middle;
    }
    #app-title {
        color: #7c79f0;
        text-style: bold;
        content-align: center middle;
        height: auto;
        width: auto;
    }
    #tagline {
        color: #6b6e9a;
        content-align: center middle;
        height: auto;
        width: auto;
        padding: 1 0;
    }
    #keyref {
        color: #3a3d6a;
        content-align: center middle;
        height: auto;
        width: auto;
        padding: 1 0;
    }
    #quick-actions {
        color: #dde1ff;
        content-align: center middle;
        height: auto;
        width: auto;
        padding: 1 0;
    }
    #quick-actions Static {
        width: auto;
        color: #6b6e9a;
    }
    """

    def compose(self):
        with Container():
            with Horizontal(id="welcome-panel"):
                with Vertical(id="left-col"):
                    yield Static("AmicoScript TUI", id="app-title")
                    yield Static(
                        "local-first audio & video transcription",
                        id="tagline",
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
                        "[dim]ctrl+k / ctrl+p[/] command palette  ·  "
                        "[dim]space[/] leader chords  ·  "
                        "[dim]?[/] help  ·  "
                        "[dim]ctrl+c[/] quit",
                        id="keyref",
                    )
                yield Static(LOGO_ART, id="logo-ascii")
        from ..widgets.status_bar import StatusBar
        yield StatusBar(id="statusbar")

