"""File-browser screen: pick an audio/video file from the local filesystem.

Triggered by ``/import [start_path]``. Uses Textual's DirectoryTree but
restricts file selection to known audio/video extensions.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DirectoryTree, Input, Static

from ..app import AUDIO_EXTS, shquote
from ..widgets.chrome import CommandBar, ContextHint, TitleBar
from ..widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from ..app import AmicoTUI


class FilteredDirectoryTree(DirectoryTree):
    """Hide hidden dirs; only show audio/video files + directories."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:  # type: ignore[override]
        for p in paths:
            try:
                if p.name.startswith("."):
                    continue
                if p.is_dir():
                    yield p
                elif p.suffix.lower() in AUDIO_EXTS:
                    yield p
            except OSError:
                continue


class ImportScreen(Screen):
    BINDINGS = [
        Binding("escape", "pop", "Back"),
        Binding("q", "pop", "Back"),
        Binding("h", "go_home", "Home"),
        Binding("backspace", "go_up", "Up"),
    ]

    leader_chords = {
        "l": ("Library", "/library"),
        "j": ("Jobs", "/jobs"),
        "s": ("Settings", "/settings"),
        "q": ("Back", "/quit"),
    }

    DEFAULT_CSS = """
    ImportScreen { layout: vertical; }
    #pathline {
        height: 3;
        padding: 0 2;
        background: #12152a;
        color: #dde1ff;
        border-bottom: solid #2a2860;
    }
    #pathline Static {
        width: 6;
        height: 3;
        content-align: left middle;
        color: #7c79f0;
    }
    #pathline Input {
        height: 3;
        background: #12152a;
        color: #dde1ff;
        border: none;
    }
    DirectoryTree {
        height: 1fr;
        background: #0c0e1a;
        color: #dde1ff;
    }
    """

    def __init__(self, start: Path | None = None) -> None:
        super().__init__()
        self.start_path = (start or Path.home()).expanduser().resolve()
        if not self.start_path.exists():
            self.start_path = Path.home()
        self.title = "Import"

    def compose(self):
        yield TitleBar(id="titlebar")
        with Horizontal(id="pathline"):
            yield Static("path:", id="pathlabel")
            yield Input(value=str(self.start_path), id="pathinput")
        with Vertical():
            yield FilteredDirectoryTree(str(self.start_path), id="tree")
        yield ContextHint(
            "↑↓ navigate  ·  ↵ enter dir or pick file  ·  h home  ·  backspace up  ·  Esc cancel",
            id="ctxhint",
        )
        yield CommandBar(id="cmdbar")
        yield StatusBar(id="statusbar")

    def on_mount(self) -> None:
        self.query_one(DirectoryTree).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "pathinput":
            return
        p = Path(event.value).expanduser()
        if not p.is_dir():
            self.app.notify(f"not a directory: {p}")
            return
        self._reload(p)

    def _reload(self, p: Path) -> None:
        try:
            tree = self.query_one(DirectoryTree)
            tree.path = p  # type: ignore[assignment]
            tree.reload()
        except Exception:
            new_tree = FilteredDirectoryTree(str(p), id="tree")
            old = self.query_one(DirectoryTree)
            old.remove()
            self.mount(new_tree)
        self.query_one("#pathinput", Input).value = str(p)
        self.start_path = p

    def action_go_home(self) -> None:
        self._reload(Path.home())

    def action_go_up(self) -> None:
        self._reload(self.start_path.parent)

    def action_pop(self) -> None:
        self.app.pop_screen()

    async def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        p = Path(event.path)
        if p.suffix.lower() not in AUDIO_EXTS:
            self.app.notify(f"unsupported: {p.suffix}", severity="warning")
            return
        from ..commands import run_command
        self.app.notify(f"importing: {p.name}")
        self.app.pop_screen()
        await run_command(self.app, f"transcribe {shquote(str(p))}")
