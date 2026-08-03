"""File-browser screen: pick an audio/video file from the local filesystem.

Triggered by ``/import [start_path]``. Uses Textual's DirectoryTree but
restricts file selection to known audio/video extensions.  A ``/`` search
box lets you recursively fuzzy-find files under the current directory.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DirectoryTree, Input, OptionList, Static
from textual.widgets.option_list import Option

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
        Binding("slash", "focus_search", "Search"),
        Binding("ctrl+f", "focus_search", "Search"),
    ]

    # No Space-h chord here — bare "h" already means "filesystem home" on
    # this screen; a second "Welcome" meaning behind the leader would clash.
    leader_chords = {
        "l": ("Library", "/library"),
        "j": ("Jobs", "/jobs"),
        "s": ("Settings", "/settings"),
        "question_mark": ("Help", "/help"),
        "q": ("Quit", "/quit"),
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
    #searchline {
        height: 3;
        padding: 0 2;
        background: #0c0e1a;
        color: #dde1ff;
        border-bottom: solid #2a2860;
        display: none;
    }
    #searchline Static {
        width: 6;
        height: 3;
        content-align: left middle;
        color: #7c79f0;
    }
    #searchline Input {
        height: 3;
        background: #0c0e1a;
        color: #dde1ff;
        border: none;
    }
    DirectoryTree {
        height: 1fr;
        background: #0c0e1a;
        color: #dde1ff;
    }
    #results {
        height: 1fr;
        background: #0c0e1a;
        color: #dde1ff;
        border: none;
        display: none;
    }
    #results > .option-list--option-highlighted {
        background: #2d2a7a;
        color: #dde1ff;
    }
    #results > .option-list--option-hover {
        background: #1a1d35;
    }
    """

    def __init__(self, start: Path | None = None) -> None:
        super().__init__()
        self.start_path = (start or Path.home()).expanduser().resolve()
        if not self.start_path.exists():
            self.start_path = Path.home()
        self.title = "Import"
        self._search_timer = None

    def compose(self):
        yield TitleBar(id="titlebar")
        with Horizontal(id="pathline"):
            yield Static("path:", id="pathlabel")
            yield Input(value=str(self.start_path), id="pathinput")
        with Horizontal(id="searchline"):
            yield Static("find:", id="searchlabel")
            yield Input(placeholder="type to search recursively…", id="searchinput")
        with Vertical(id="browser"):
            yield FilteredDirectoryTree(str(self.start_path), id="tree")
            yield OptionList(id="results")
        yield ContextHint(
            "↑↓ navigate  ·  ↵ enter dir or pick file  ·  / search  ·  h home  ·  backspace up  ·  Esc cancel",
            id="ctxhint",
        )
        yield CommandBar(id="cmdbar")
        yield StatusBar(id="statusbar")

    def on_mount(self) -> None:
        self.query_one(DirectoryTree).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "pathinput":
            p = Path(event.value).expanduser()
            if not p.is_dir():
                self.app.notify(f"not a directory: {p}")
                return
            self._reload(p)
        elif event.input.id == "searchinput":
            results = self.query_one("#results", OptionList)
            if results.display and results.option_count:
                opt = results.get_option_at_index(results.highlighted or 0)
                if opt and opt.id:
                    await self._import_path(Path(opt.id))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "searchinput":
            if self._search_timer:
                self._search_timer.stop()
            self._search_timer = self.set_timer(0.2, self._debounced_search)

    def _debounced_search(self) -> None:
        query = self.query_one("#searchinput", Input).value.strip()
        self.run_worker(self._do_search(query), exclusive=True, name="search")

    async def _do_search(self, query: str) -> None:
        tree = self.query_one("#tree", DirectoryTree)
        results = self.query_one("#results", OptionList)
        ctx = self.query_one("#ctxhint", ContextHint)

        if not query:
            tree.display = True
            results.display = False
            ctx.set_text(
                "↑↓ navigate  ·  ↵ enter dir or pick file  ·  / search  ·  h home  ·  backspace up  ·  Esc cancel"
            )
            return

        tree.display = False
        results.display = True
        results.clear_options()

        found = []
        qlower = query.lower()
        max_results = 200
        max_depth = 5
        root = self.start_path

        try:
            stack = [(root, 0)]
            while stack:
                current, depth = stack.pop()
                if depth > max_depth:
                    continue
                try:
                    for entry in current.iterdir():
                        if entry.is_dir(follow_symlinks=False):
                            stack.append((entry, depth + 1))
                        elif entry.is_file(follow_symlinks=False):
                            if entry.suffix.lower() in AUDIO_EXTS and qlower in entry.name.lower():
                                found.append(entry)
                                if len(found) >= max_results:
                                    stack = []
                                    break
                except PermissionError:
                    continue
        except Exception:
            pass

        found.sort(key=lambda p: p.name.lower())
        for p in found:
            try:
                rel = str(p.relative_to(root))
            except ValueError:
                rel = str(p)
            results.add_option(
                Option(f"♪ {p.name}  [#6b6e9a]{rel}[/]", id=str(p))
            )

        if results.option_count:
            results.highlighted = 0

        ctx.set_text(
            f"{len(found)} matches  ·  ↑↓ navigate  ·  ↵ import  ·  / search  ·  Esc clear"
        )

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
        # clear any active search
        self.query_one("#searchinput", Input).value = ""
        searchline = self.query_one("#searchline", Horizontal)
        searchline.display = False
        tree = self.query_one("#tree", DirectoryTree)
        results = self.query_one("#results", OptionList)
        tree.display = True
        results.display = False

    def action_go_home(self) -> None:
        self._reload(Path.home())

    def action_go_up(self) -> None:
        self._reload(self.start_path.parent)

    def action_pop(self) -> None:
        searchline = self.query_one("#searchline", Horizontal)
        if searchline.display:
            self.action_clear_search()
            return
        self.app.pop_screen()

    def action_focus_search(self) -> None:
        searchline = self.query_one("#searchline", Horizontal)
        searchline.display = True
        self.query_one("#searchinput", Input).focus()

    def action_clear_search(self) -> None:
        self.query_one("#searchinput", Input).value = ""
        searchline = self.query_one("#searchline", Horizontal)
        searchline.display = False
        tree = self.query_one("#tree", DirectoryTree)
        results = self.query_one("#results", OptionList)
        tree.display = True
        results.display = False
        tree.focus()
        self.query_one("#ctxhint", ContextHint).set_text(
            "↑↓ navigate  ·  ↵ enter dir or pick file  ·  / search  ·  h home  ·  backspace up  ·  Esc cancel"
        )

    async def _import_path(self, p: Path) -> None:
        if p.suffix.lower() not in AUDIO_EXTS:
            self.app.notify(f"unsupported: {p.suffix}", severity="warning")
            return
        from ..commands import run_command
        self.app.notify(f"importing: {p.name}")
        self.app.pop_screen()
        await run_command(self.app, f"transcribe {shquote(str(p))}")

    async def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        await self._import_path(Path(event.path))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "results":
            if event.option.id:
                self.run_worker(self._import_path(Path(event.option.id)), exclusive=False)
