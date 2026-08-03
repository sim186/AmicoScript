"""Settings: sectioned form (Model / Diarization / Output / Server)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, Input, Static

from ..widgets.chrome import CommandBar, ContextHint, TitleBar
from ..widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from ..app import AmicoTUI


def _section(title: str) -> Static:
    return Static(f"[b #6b6e9a]{title}[/]", classes="section-hdr")


class SettingsPanel(Widget):
    DEFAULT_CSS = """
    SettingsPanel { layout: vertical; height: 1fr; }
    VerticalScroll { height: 1fr; }
    .section-hdr {
        padding: 1 2 0 2;
        height: 2;
        color: #6b6e9a;
        background: #0c0e1a;
        border-top: solid #2a2860;
    }
    .setting-row {
        height: 3;
        padding: 0 2;
        background: #0c0e1a;
        border-bottom: solid #2a2860;
    }
    .setting-label {
        width: 28;
        height: 3;
        content-align: left middle;
        color: #6b6e9a;
    }
    .setting-row Input {
        height: 3;
        background: #1a1d35;
        color: #dde1ff;
        border: tall #2a2860;
    }
    .setting-row Input:focus { border: tall #7c79f0; }
    #btnrow {
        height: 3;
        padding: 1 2;
        background: #0c0e1a;
    }
    """

    def compose(self):
        with VerticalScroll():
            yield _section("MODEL")
            with Horizontal(classes="setting-row"):
                yield Static("Default model", classes="setting-label")
                yield Input(id="model", placeholder="large-v3")
            with Horizontal(classes="setting-row"):
                yield Static("Device", classes="setting-label")
                yield Input(id="device", placeholder="auto")
            with Horizontal(classes="setting-row"):
                yield Static("Compute type", classes="setting-label")
                yield Input(id="compute", placeholder="float16")

            yield _section("DIARIZATION")
            with Horizontal(classes="setting-row"):
                yield Static("Hugging Face token", classes="setting-label")
                yield Input(id="hf", password=True, placeholder="hf_…")

            yield _section("LLM")
            with Horizontal(classes="setting-row"):
                yield Static("Base URL", classes="setting-label")
                yield Input(id="llm_url", placeholder="http://localhost:11434")
            with Horizontal(classes="setting-row"):
                yield Static("Model name", classes="setting-label")
                yield Input(id="llm_model", placeholder="llama3.1")
            with Horizontal(classes="setting-row"):
                yield Static("API key", classes="setting-label")
                yield Input(id="llm_key", password=True)

            yield _section("SERVER")
            with Horizontal(classes="setting-row"):
                yield Static("API URL", classes="setting-label")
                yield Input(id="api_url", disabled=True)

            with Horizontal(id="btnrow"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Reset defaults", id="reset")

    def on_mount(self) -> None:
        try:
            self.query_one("#api_url", Input).value = self.app.api.base_url
        except Exception:
            pass
        self.run_worker(self._load(), exclusive=True)

    async def _load(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            s = await app.api.settings()
            self.query_one("#hf", Input).value = s.get("hf_token") or ""
            self.query_one("#model", Input).value = s.get("whisper_model") or "small"
            self.query_one("#device", Input).value = s.get("whisper_device") or "auto"
            self.query_one("#compute", Input).value = s.get("whisper_compute") or "float16"
        except Exception as e:
            self.app.notify(f"settings load failed: {e}", severity="error")
        try:
            llm = await app.api.llm_settings()
            self.query_one("#llm_url", Input).value = llm.get("base_url") or ""
            self.query_one("#llm_model", Input).value = llm.get("model_name") or ""
            self.query_one("#llm_key", Input).value = llm.get("api_key") or ""
        except Exception:
            pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        if event.button.id == "reset":
            self.run_worker(self._load(), exclusive=True)
            self.app.notify("reloaded from server")
            return
        if event.button.id != "save":
            return
        try:
            await app.api.save_settings(
                hf_token=self.query_one("#hf", Input).value,
                whisper_model=self.query_one("#model", Input).value or None,
                whisper_device=self.query_one("#device", Input).value or None,
                whisper_compute=self.query_one("#compute", Input).value or None,
            )
            await app.api.save_llm_settings(
                base_url=self.query_one("#llm_url", Input).value or None,
                model_name=self.query_one("#llm_model", Input).value or None,
                api_key=self.query_one("#llm_key", Input).value or None,
            )
            self.app.notify("settings saved")
        except Exception as e:
            self.app.notify(f"save failed: {e}", severity="error")


class SettingsScreen(Screen):
    BINDINGS = [Binding("escape", "pop", "Back")]

    leader_chords = {
        "l": ("Library", "/library"),
        "j": ("Jobs", "/jobs"),
        "i": ("Import", "/import"),
        "q": ("Back", "/quit"),
    }

    DEFAULT_CSS = """
    SettingsScreen { layout: vertical; }
    SettingsPanel { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.title = "Settings"

    def compose(self):
        yield TitleBar(id="titlebar")
        with Vertical():
            yield SettingsPanel(id="settings_panel")
        yield ContextHint("tab to navigate fields  ·  Save persists to backend", id="ctxhint")
        yield CommandBar(id="cmdbar")
        yield StatusBar(id="statusbar")

    def action_pop(self) -> None:
        self.app.pop_screen()
