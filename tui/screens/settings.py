"""Settings: sectioned form (Model / Diarization / Output / Server)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, Input, Static

from ..api import UNCHANGED
from ..errors import explain as _explain
from ..widgets.chrome import CommandBar, ContextHint, TitleBar
from ..widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from ..app import AmicoTUI


def _section(title: str) -> Static:
    return Static(f"[b #6b6e9a]{title}[/]", classes="section-hdr")


def _is_mask(value: str) -> bool:
    """True when the field still holds the server's placeholder, not a real secret."""
    stripped = (value or "").strip()
    return not stripped or set(stripped) <= {"\u2022"} or stripped.startswith("\u2022")


def _to_bool(value: str) -> bool:
    return (value or "").strip().lower() in {"on", "1", "true", "yes", "y"}


def _to_int(value: str) -> int | None:
    try:
        parsed = int((value or "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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

    # Set by _load once the server reports whether a secret is stored. Default
    # to True so a save that races the initial load leaves secrets alone.
    _hf_masked = True
    _key_masked = True

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

            yield _section("MEETINGS")
            with Horizontal(classes="setting-row"):
                yield Static("Auto-summarise", classes="setting-label")
                yield Input(id="auto_summary", placeholder="on / off")

            yield _section("LLM")
            with Horizontal(classes="setting-row"):
                yield Static("Provider", classes="setting-label")
                yield Input(id="llm_provider", placeholder="ollama / lmstudio / unsloth / …")
            with Horizontal(classes="setting-row"):
                yield Static("Base URL", classes="setting-label")
                yield Input(id="llm_url", placeholder="http://localhost:11434")
            with Horizontal(classes="setting-row"):
                yield Static("Model name", classes="setting-label")
                yield Input(id="llm_model", placeholder="llama3.1")
            with Horizontal(classes="setting-row"):
                yield Static("API key", classes="setting-label")
                yield Input(id="llm_key", password=True)
            with Horizontal(classes="setting-row"):
                yield Static("Context tokens", classes="setting-label")
                yield Input(id="llm_context", placeholder="8192")
            with Horizontal(classes="setting-row"):
                yield Static("Allow cloud provider", classes="setting-label")
                yield Input(id="llm_allow_cloud", placeholder="on / off")
            yield Static(
                "[#6b6e9a]  /llm-detect finds a running server · /llm-providers lists presets[/]",
                classes="section-hdr",
            )

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
            # Secrets are never sent by the server. Show the masked preview and
            # remember that it is a placeholder, so saving cannot erase the
            # stored value with a row of bullets.
            hf_field = self.query_one("#hf", Input)
            hf_field.value = s.get("hf_token_preview") or ""
            self._hf_masked = bool(s.get("hf_token_set"))
            self.query_one("#model", Input).value = s.get("whisper_model") or "small"
            self.query_one("#device", Input).value = s.get("whisper_device") or "auto"
            self.query_one("#compute", Input).value = s.get("whisper_compute") or "float16"
            self.query_one("#auto_summary", Input).value = (
                "on" if s.get("auto_summarize_meetings") else "off"
            )
        except Exception as e:
            self.app.notify(_explain(e, "settings load failed"), severity="error")
        try:
            llm = await app.api.llm_settings()
            self.query_one("#llm_provider", Input).value = llm.get("provider") or "ollama"
            self.query_one("#llm_url", Input).value = llm.get("base_url") or ""
            self.query_one("#llm_model", Input).value = llm.get("model_name") or ""
            key_field = self.query_one("#llm_key", Input)
            self._key_masked = bool(llm.get("api_key_set"))
            key_field.value = "••••••••" if self._key_masked else ""
            self.query_one("#llm_context", Input).value = str(llm.get("context_tokens") or "")
            self.query_one("#llm_allow_cloud", Input).value = (
                "on" if llm.get("allow_cloud") else "off"
            )
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
            hf_value = self.query_one("#hf", Input).value
            # Untouched masked field: tell the server to keep what it has.
            hf_token = UNCHANGED if (self._hf_masked and _is_mask(hf_value)) else hf_value

            await app.api.save_settings(
                hf_token=hf_token,
                whisper_model=self.query_one("#model", Input).value or None,
                whisper_device=self.query_one("#device", Input).value or None,
                whisper_compute=self.query_one("#compute", Input).value or None,
                auto_summarize_meetings=_to_bool(self.query_one("#auto_summary", Input).value),
            )

            key_value = self.query_one("#llm_key", Input).value
            api_key = UNCHANGED if (self._key_masked and _is_mask(key_value)) else key_value

            result = await app.api.save_llm_settings(
                provider=self.query_one("#llm_provider", Input).value or None,
                base_url=self.query_one("#llm_url", Input).value or None,
                model_name=self.query_one("#llm_model", Input).value or None,
                api_key=api_key,
                context_tokens=_to_int(self.query_one("#llm_context", Input).value),
                allow_cloud=_to_bool(self.query_one("#llm_allow_cloud", Input).value),
            )
            note = result.get("note") if isinstance(result, dict) else ""
            self.app.notify(f"settings saved — {note}" if note else "settings saved")
            # Reload so the normalised address and re-masked secrets are shown.
            self.run_worker(self._load(), exclusive=True)
        except Exception as e:
            self.app.notify(_explain(e, "save failed"), severity="error")


class SettingsScreen(Screen):
    BINDINGS = [Binding("escape", "pop", "Back")]

    leader_chords = {
        "l": ("Library", "/library"),
        "j": ("Jobs", "/jobs"),
        "i": ("Import", "/import"),
        "h": ("Welcome", "/welcome"),
        "question_mark": ("Help", "/help"),
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
        yield TitleBar(id="titlebar")
        with Vertical():
            yield SettingsPanel(id="settings_panel")
        yield ContextHint("tab to navigate fields  ·  Save persists to backend", id="ctxhint")
        yield CommandBar(id="cmdbar")
        yield StatusBar(id="statusbar")

    def action_pop(self) -> None:
        self.app.pop_screen()
