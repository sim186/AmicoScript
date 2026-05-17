"""Main Textual App for AmicoScript TUI.

Modeless, palette-driven. Lands directly on Library. Leader key (Space)
arms per-screen chord maps; ``/`` or ``ctrl+k`` opens the unified fuzzy
palette.
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
from .server import ServerManager


# Mockup palette (see amicoscript_tui_mockups.html).
COLOR_BG = "#0c0e1a"
COLOR_SURFACE = "#12152a"
COLOR_SURFACE2 = "#1a1d35"
COLOR_BORDER = "#4a47c0"
COLOR_BORDER_DIM = "#2a2860"
COLOR_BORDER_BRIGHT = "#7c79f0"
COLOR_TEXT = "#dde1ff"
COLOR_TEXT_DIM = "#6b6e9a"
COLOR_TEXT_MUTED = "#3a3d6a"
COLOR_PURPLE = "#7c79f0"
COLOR_PURPLE_BG = "#1e1b52"
COLOR_PURPLE_SEL = "#2d2a7a"
COLOR_AMBER = "#f59e0b"
COLOR_GREEN = "#22c55e"
COLOR_RED = "#ef4444"
COLOR_TEAL = "#2dd4bf"


class AmicoTUI(App):
    """AmicoScript terminal interface."""

    CSS = f"""
    $primary: {COLOR_PURPLE};
    $accent: {COLOR_PURPLE};
    $surface: {COLOR_BG};
    $panel: {COLOR_SURFACE};
    $boost: {COLOR_SURFACE2};
    $text: {COLOR_TEXT};
    $text-muted: {COLOR_TEXT_DIM};
    $success: {COLOR_GREEN};
    $warning: {COLOR_AMBER};
    $error: {COLOR_RED};

    Screen {{
        background: {COLOR_BG};
        color: {COLOR_TEXT};
    }}
    Header {{
        background: {COLOR_PURPLE_BG};
        color: {COLOR_PURPLE};
    }}
    Footer {{
        background: {COLOR_SURFACE};
        color: {COLOR_TEXT_DIM};
    }}
    DataTable {{
        background: {COLOR_BG};
        color: {COLOR_TEXT};
    }}
    DataTable > .datatable--header {{
        background: {COLOR_SURFACE};
        color: {COLOR_TEXT_DIM};
        text-style: none;
    }}
    DataTable > .datatable--cursor {{
        background: {COLOR_PURPLE_SEL};
        color: {COLOR_TEXT};
    }}
    DataTable > .datatable--hover {{
        background: {COLOR_SURFACE2};
    }}
    OptionList {{
        background: {COLOR_BG};
        color: {COLOR_TEXT};
        border: none;
    }}
    OptionList > .option-list--option-highlighted {{
        background: {COLOR_PURPLE_SEL};
        color: {COLOR_TEXT};
    }}
    OptionList > .option-list--option-hover {{
        background: {COLOR_SURFACE2};
    }}
    Input {{
        background: {COLOR_SURFACE2};
        color: {COLOR_TEXT};
        border: tall {COLOR_BORDER_DIM};
    }}
    Input:focus {{
        border: tall {COLOR_BORDER_BRIGHT};
    }}
    Button {{
        background: {COLOR_PURPLE_BG};
        color: {COLOR_PURPLE};
        border: tall {COLOR_BORDER};
    }}
    Button:hover {{
        background: {COLOR_PURPLE_SEL};
    }}
    Button.-primary {{
        background: {COLOR_PURPLE};
        color: {COLOR_TEXT};
    }}
    Log {{
        background: #080a14;
        color: {COLOR_TEXT_DIM};
        border: tall {COLOR_BORDER_DIM};
    }}
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
        from .screens.library import LibraryScreen
        self.push_screen(LibraryScreen())
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
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            self.notify(f"dropped: {p.name} — transcribing")
            await run_command(self, f"transcribe {shquote(str(p))}")


AUDIO_EXTS = {
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus",
    ".mp4", ".mkv", ".webm", ".mov", ".aac",
}


def shquote(s: str) -> str:
    if " " in s or "'" in s:
        return '"' + s.replace('"', '\\"') + '"'
    return s
