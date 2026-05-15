"""Settings panel: HF token + LLM config."""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, Footer, Header, Input, Label

if TYPE_CHECKING:
    from ..app import AmicoTUI


class SettingsPanel(Widget):
    DEFAULT_CSS = """
    SettingsPanel { layout: vertical; height: 1fr; }
    Label { padding: 1 2 0 2; color: $accent; }
    Input { margin: 0 2; }
    Button { margin: 1 2 0 2; }
    """

    def compose(self):
        with Vertical():
            yield Label("Hugging Face token (for diarization)")
            yield Input(id="hf", password=True, placeholder="hf_...")
            yield Label("LLM base URL")
            yield Input(id="llm_url", placeholder="http://localhost:11434")
            yield Label("LLM model name")
            yield Input(id="llm_model", placeholder="llama3.1")
            yield Label("LLM API key (optional)")
            yield Input(id="llm_key", password=True)
            yield Button("Save", id="save", variant="primary")

    def on_mount(self) -> None:
        self.run_worker(self._load(), exclusive=True)

    async def _load(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            s = await app.api.settings()
            self.query_one("#hf", Input).value = s.get("hf_token") or ""
        except Exception as e:
            self.app.notify(f"settings load failed: {e}", severity="error")
            return
        try:
            llm = await app.api.llm_settings()
            self.query_one("#llm_url", Input).value = llm.get("base_url") or ""
            self.query_one("#llm_model", Input).value = llm.get("model_name") or ""
            self.query_one("#llm_key", Input).value = llm.get("api_key") or ""
        except Exception:
            pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save":
            return
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            await app.api.save_settings(hf_token=self.query_one("#hf", Input).value)
            await app.api.save_llm_settings(
                base_url=self.query_one("#llm_url", Input).value or None,
                model_name=self.query_one("#llm_model", Input).value or None,
                api_key=self.query_one("#llm_key", Input).value or None,
            )
            self.app.notify("settings saved")
        except Exception as e:
            self.app.notify(f"save failed: {e}", severity="error")


class SettingsScreen(Screen):
    """Full-screen settings view."""

    BINDINGS = [Binding("escape", "pop", "Back")]

    leader_chords = {
        "l": ("Library", "/library"),
        "j": ("Jobs", "/jobs"),
        "h": ("Welcome", "/welcome"),
        "q": ("Quit", "/quit"),
    }

    DEFAULT_CSS = """
    SettingsScreen { layout: vertical; }
    SettingsPanel { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.title = "Settings"

    def compose(self):
        from ..widgets.status_bar import StatusBar
        yield Header(show_clock=False)
        with Vertical():
            yield SettingsPanel(id="settings_panel")
            yield StatusBar(id="statusbar")
        yield Footer()

    def on_mount(self) -> None:
        try:
            self.query_one(SettingsPanel).query_one("#hf", Input).focus()
        except Exception:
            pass

    def action_pop(self) -> None:
        self.app.pop_screen()
