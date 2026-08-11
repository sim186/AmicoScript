"""Reusable yes/no confirmation modal for destructive actions.

Usage: ``confirmed = await app.push_screen_wait(ConfirmDialog("Delete X?"))``
"""
from __future__ import annotations

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmDialog(ModalScreen[bool]):
    """Modal that resolves to ``True`` (confirm) or ``False`` (cancel)."""

    DEFAULT_CSS = """
    ConfirmDialog {
        align: center middle;
        background: rgba(12,14,26,0.85);
    }
    #box {
        width: 60;
        height: auto;
        padding: 1 2;
        background: #12152a;
        border: tall #ef4444;
    }
    #message {
        color: #dde1ff;
        padding: 0 0 1 0;
    }
    #buttons {
        height: 3;
        align: right middle;
    }
    #buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("n", "cancel", show=False),
        Binding("y", "confirm", show=False),
    ]

    def __init__(
        self,
        message: str,
        confirm_label: str = "Delete",
        cancel_label: str = "Cancel",
    ) -> None:
        super().__init__()
        self.message = message
        self.confirm_label = confirm_label
        self.cancel_label = cancel_label

    def compose(self):
        with Vertical(id="box"):
            yield Static(self.message, id="message")
            with Horizontal(id="buttons"):
                yield Button(self.cancel_label, id="cancel")
                yield Button(self.confirm_label, id="confirm", variant="error")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
