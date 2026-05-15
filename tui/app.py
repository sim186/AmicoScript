"""Main Textual App for AmicoScript TUI.

Modeless, palette-driven. No tabs. Leader key (Space) arms per-screen
chord maps; ``/`` or ``ctrl+k`` opens the unified fuzzy palette.
"""
from __future__ import annotations

from collections import deque

from textual.app import App
from textual.binding import Binding
from textual.events import Key

from .api import ApiClient
from .commands import run_command
from .config import Config
from .leader import LeaderDispatcher
from .palette import Palette
from .screens.welcome import WelcomeScreen
from .server import ServerManager


class AmicoTUI(App):
    """AmicoScript terminal interface."""

    CSS = """
    $primary: #6c63ff;
    $accent: #a78bfa;
    $surface: #0f172a;
    $panel: #1e293b;
    $boost: #2a3548;
    $text: #e2e8f0;
    $text-muted: #94a3b8;
    $success: #10b981;
    $warning: #f59e0b;
    $error: #ef4444;

    Screen { background: $surface; color: $text; }
    Header { background: $primary; color: $text; }
    DataTable > .datatable--header {
        background: $panel;
        color: $accent;
    }
    DataTable > .datatable--cursor {
        background: $primary;
        color: $text;
    }
    DataTable > .datatable--hover {
        background: $boost;
    }
    OptionList { background: $surface; color: $text; }
    OptionList > .option-list--option-highlighted {
        background: $primary;
        color: $text;
    }
    Input {
        background: $panel;
        color: $text;
        border: tall $primary;
    }
    Button {
        background: $primary;
        color: $text;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True, show=False),
        Binding("slash", "palette('/')", "Palette", priority=True, show=False),
        Binding("at", "palette('@')", "Palette @", priority=True, show=False),
        Binding("ctrl+k", "palette()", "Palette", priority=True, show=False),
        Binding("ctrl+p", "palette('/')", "Commands", priority=True, show=False),
    ]

    def __init__(self, cfg: Config, server: ServerManager) -> None:
        super().__init__()
        self.cfg = cfg
        self.server = server
        self.api = ApiClient(cfg.api_url)
        self.title = "AmicoScript"
        self.sub_title = cfg.api_url
        self._palette_mru: deque = deque(maxlen=30)
        self.leader = LeaderDispatcher(self)

    def on_mount(self) -> None:
        self.push_screen(WelcomeScreen())
        self.run_worker(self._health_loop(), exclusive=True, name="health")

    async def on_unmount(self) -> None:
        await self.api.aclose()

    async def _health_loop(self) -> None:
        """Probe /api/version periodically; notify on transitions."""
        import asyncio
        last_ok = True
        backoff = 1.0
        while True:
            try:
                await self.api.version()
                if not last_ok:
                    self.notify("backend reconnected")
                last_ok = True
                backoff = 1.0
                await asyncio.sleep(5.0)
            except Exception:
                if last_ok:
                    self.notify("backend disconnected · retrying", severity="warning")
                last_ok = False
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)

    # --- key intercept (leader) -----------------------------------------

    def on_key(self, event: Key) -> None:
        if self.leader.handle_key(event):
            event.stop()
            event.prevent_default()

    # --- actions --------------------------------------------------------

    def action_palette(self, seed: str = "") -> None:
        # Avoid stacking duplicate palettes.
        if isinstance(self.screen, Palette):
            return
        self.push_screen(Palette(initial=seed))

    async def on_paste(self, event) -> None:
        """Handle drag-and-drop: terminals emit dropped path as paste."""
        text = (event.text or "").strip().strip('"').strip("'")
        if not text:
            return
        if text.startswith("file://"):
            text = text[7:]
        from pathlib import Path
        p = Path(text)
        if p.is_file() and p.suffix.lower() in {
            ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus",
            ".mp4", ".mkv", ".webm", ".mov", ".aac",
        }:
            self.notify(f"dropped: {p.name} — transcribing")
            await run_command(self, f"transcribe {shquote(str(p))}")


def shquote(s: str) -> str:
    if " " in s or "'" in s:
        return '"' + s.replace('"', '\\"') + '"'
    return s
