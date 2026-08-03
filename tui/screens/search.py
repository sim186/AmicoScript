"""Full-text search results screen."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..widgets.chrome import CommandBar, ContextHint, TitleBar
from ..widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from ..app import AmicoTUI


def _convert_mark(snippet: str) -> str:
    """Replace HTML <mark> spans with Rich amber-on-black style."""
    if not snippet:
        return ""
    snippet = re.sub(
        r"<mark>(.*?)</mark>",
        r"[on #f59e0b black]\1[/]",
        snippet,
        flags=re.DOTALL,
    )
    return snippet


class SearchScreen(Screen):
    BINDINGS = [
        Binding("escape", "pop", "Back"),
        Binding("q", "pop", "Back"),
        Binding("enter", "open", show=False),
    ]

    leader_chords = {
        "l": ("Library", "/library"),
        "j": ("Jobs", "/jobs"),
        "s": ("Settings", "/settings"),
        "h": ("Welcome", "/welcome"),
        "question_mark": ("Help", "/help"),
        "q": ("Quit", "/quit"),
    }

    DEFAULT_CSS = """
    SearchScreen { layout: vertical; }
    #queryline {
        height: 1;
        padding: 0 2;
        background: #12152a;
        color: #dde1ff;
        border-bottom: solid #2a2860;
    }
    #results { height: 1fr; background: #0c0e1a; }
    """

    def __init__(self, query: str) -> None:
        super().__init__()
        self.query_text = query
        self.results: list[dict] = []
        self.title = "Search"

    def compose(self):
        yield TitleBar(id="titlebar")
        yield Static(
            f"[#7c79f0]/search[/]  [#dde1ff]{self.query_text}[/]   [#6b6e9a]loading…[/]",
            id="queryline",
        )
        with Vertical():
            yield OptionList(id="results")
        yield ContextHint(
            "↑↓ navigate  ·  ↵ open recording  ·  /search <new query>  ·  Esc close",
            id="ctxhint",
        )
        yield CommandBar(id="cmdbar")
        yield StatusBar(id="statusbar")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()
        self.run_worker(self._load(), exclusive=True)

    async def _load(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            data = await app.api.search(self.query_text)
        except Exception as e:
            self.query_one("#queryline", Static).update(
                f"[#ef4444]error: {e}[/]"
            )
            return
        if isinstance(data, dict):
            rows = data.get("results") or data.get("hits") or []
        else:
            rows = data or []
        self.results = rows
        lst = self.query_one("#results", OptionList)
        lst.clear_options()
        files = set()
        for i, r in enumerate(rows):
            rid = str(r.get("recording_id") or r.get("id") or "")
            files.add(rid)
            snippet = _convert_mark(r.get("snippet") or r.get("text") or "")
            label = (
                f"[#7c79f0]{rid[:8]}[/]   "
                f"[#dde1ff]{snippet}[/]"
            )
            lst.add_option(Option(label, id=str(i)))
        self.query_one("#queryline", Static).update(
            f"[#7c79f0]/search[/]  [#dde1ff]{self.query_text}[/]   "
            f"[#6b6e9a]{len(rows)} results across {len(files)} files[/]"
        )
        if rows:
            lst.highlighted = 0

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        self.action_open()

    def action_open(self) -> None:
        lst = self.query_one("#results", OptionList)
        idx = lst.highlighted
        if idx is None or not (0 <= idx < len(self.results)):
            return
        rid = str(self.results[idx].get("recording_id") or self.results[idx].get("id") or "")
        if not rid:
            return
        from .transcript import TranscriptScreen
        self.app.push_screen(TranscriptScreen(rid))

    def action_pop(self) -> None:
        self.app.pop_screen()
