"""Reusable single-line text-input modal (rename, create, etc).

Usage: ``name = await app.push_screen_wait(PromptDialog("Rename to:", initial=old_name))``
Resolves to the trimmed input value, or ``None`` if cancelled / left blank.
"""
from __future__ import annotations

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class PromptDialog(ModalScreen[str | None]):
    """Modal that resolves to the entered text, or None on cancel."""

    DEFAULT_CSS = """
    PromptDialog {
        align: center middle;
        background: rgba(12,14,26,0.85);
    }
    #box {
        width: 60;
        height: auto;
        padding: 1 2;
        background: #12152a;
        border: tall #4a47c0;
    }
    #message {
        color: #dde1ff;
        padding: 0 0 1 0;
    }
    #box Input {
        margin-bottom: 1;
    }
    #buttons {
        height: 3;
        align: right middle;
    }
    #buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        message: str,
        initial: str = "",
        placeholder: str = "",
        confirm_label: str = "Save",
    ) -> None:
        super().__init__()
        self.message = message
        self.initial = initial
        self.placeholder = placeholder
        self.confirm_label = confirm_label

    def compose(self):
        with Vertical(id="box"):
            yield Static(self.message, id="message")
            yield Input(value=self.initial, placeholder=self.placeholder, id="prompt-input")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self.confirm_label, id="confirm", variant="primary")

    def on_mount(self) -> None:
        inp = self.query_one("#prompt-input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self._confirm()
        else:
            self.dismiss(None)

    def _confirm(self) -> None:
        value = self.query_one("#prompt-input", Input).value.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
