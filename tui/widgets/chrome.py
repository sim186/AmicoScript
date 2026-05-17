"""Shared chrome: TitleBar, ContextHint, CommandBar.

Each primary screen composes these to match the mockup look.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static

if TYPE_CHECKING:
    from ..app import AmicoTUI


class TitleBar(Static):
    """Top band: app name + API URL + commands hint."""

    DEFAULT_CSS = """
    TitleBar {
        height: 1;
        background: #1e1b52;
        color: #7c79f0;
        padding: 0 2;
    }
    """

    def on_mount(self) -> None:
        try:
            app: "AmicoTUI" = self.app  # type: ignore[assignment]
            api = app.api.base_url
        except Exception:
            api = ""
        screen_name = getattr(self.screen, "title", None) or "AmicoScript"
        self.update(
            f"AmicoScript — {api}  ·  [b]{screen_name}[/b]"
            f"                                                 [dim]^p Commands[/dim]"
        )


class ContextHint(Static):
    """One-line bottom-of-content hint (e.g. row counts + keybinds)."""

    DEFAULT_CSS = """
    ContextHint {
        height: 1;
        background: #0c0e1a;
        color: #6b6e9a;
        padding: 0 2;
        border-top: solid #2a2860;
    }
    """

    text: reactive[str] = reactive("")

    def __init__(self, text: str = "", **kwargs) -> None:
        super().__init__(text, **kwargs)
        self.text = text

    def set_text(self, text: str) -> None:
        self.text = text
        self.update(text)


class CommandBar(Widget):
    """Persistent command bar at the bottom of primary screens.

    Typing a slash command and pressing Enter runs it. Pressing ``/`` from
    elsewhere still opens the modal Palette via app binding.
    """

    DEFAULT_CSS = """
    CommandBar {
        height: 3;
        background: #1a1d35;
        border-top: solid #4a47c0;
    }
    CommandBar Horizontal {
        height: 3;
    }
    CommandBar #prompt {
        width: 3;
        height: 3;
        content-align: center middle;
        color: #7c79f0;
        background: #1a1d35;
    }
    CommandBar Input {
        height: 3;
        border: none;
        background: #1a1d35;
        color: #dde1ff;
    }
    CommandBar Input:focus {
        border: none;
    }
    """

    PLACEHOLDER = "type / for commands, /library, /jobs, /settings…"

    def compose(self):
        with Horizontal():
            yield Static("❯", id="prompt")
            yield Input(placeholder=self.PLACEHOLDER, id="cmdinput")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        from ..commands import run_command
        text = (event.value or "").strip()
        event.input.value = ""
        if not text:
            return
        await run_command(self.app, text)
