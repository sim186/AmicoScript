"""Unified fuzzy palette with mode-based sub-pickers.

Modes (driven by input prefix):

* **free** — empty/plain text: fuzzy-match across commands.
* **command** — leading ``/``: filter commands. Tab completes when the
  prefix uniquely identifies a single command; if the command supports
  sub-picking (``/library`` / ``/folder`` / ``/tag``) completion adds a
  trailing space and switches the palette into the corresponding picker.
* **library** — ``/library <q>``: pick a recording → open its transcript.
* **folder** — ``/folder <q>``: pick a folder → open library scoped to it.
* **tag** — ``/tag <q>``: pick a tag → open library scoped to it.
* **transcript** — ``@<q>``: shortcut to the recording picker.

Recent selections boost rank in subsequent opens.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from .commands import COMMANDS, list_commands, run_command
from .fuzzy import score_match
from .widgets.command_input import CommandInput

if TYPE_CHECKING:
    from .app import AmicoTUI


MRU_MAX = 30
MRU_BONUS = 50

# Commands that, when typed with a trailing space, switch the palette to
# a sub-picker. ``new`` arg of /folder is preserved by falling back to
# raw command execution on Enter when no folder matches the query.
SUBPICKERS = {"library", "folder", "tag", "analyze", "models"}
# Map command name → mode key used internally (most are 1:1; /models → "model").
_MODE_BY_COMMAND = {
    "library": "library",
    "folder": "folder",
    "tag": "tag",
    "analyze": "analyze",
    "models": "model",
}


@dataclass
class Entry:
    kind: str            # "command" | "recording" | "folder" | "tag"
    key: str             # stable identifier for MRU
    display: str         # one-line label
    subtitle: str        # dim hint
    search_text: str     # text fuzzy-matched against
    on_select: Callable[["AmicoTUI"], Awaitable[None]]


class Palette(ModalScreen):
    """Floating palette anchored at top."""

    DEFAULT_CSS = """
    Palette {
        align: center middle;
        background: transparent;
    }
    #box {
        width: 80%;
        max-width: 100;
        height: auto;
        padding: 0;
        background: $boost;
        border-left: thick $primary;
    }
    #suggestions {
        height: auto;
        max-height: 14;
        background: $boost;
        border: none;
    }
    CommandInput {
        border: none;
        background: $boost;
        height: 1;
        padding: 0 1;
    }
    #hint {
        height: 1;
        color: $text-muted;
        background: $boost;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("tab", "tab", show=False, priority=True),
        Binding("shift+tab", "prev_suggestion", show=False, priority=True),
        Binding("down", "next_suggestion", show=False),
        Binding("up", "prev_suggestion", show=False),
    ]

    def __init__(
        self,
        initial: str = "",
        entries: list["Entry"] | None = None,
        on_pick: Callable[["AmicoTUI", "Entry"], Awaitable[None]] | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__()
        # Per-mode entry caches.
        self._commands: list[Entry] = []
        self._recordings: list[Entry] = []
        self._folders: list[Entry] = []
        self._tags: list[Entry] = []
        self._models: list[Entry] = []
        # Visible after filtering, in render order.
        self._visible: list[Entry] = []
        self._mode = "free"
        self._initial = initial
        # Optional ad-hoc mini-picker: a fixed entry list with a custom
        # on-pick handler (overrides each entry's on_select).
        self._ad_hoc_entries = entries
        self._ad_hoc_on_pick = on_pick
        self._ad_hoc_title = title

    def compose(self):
        with Vertical(id="box"):
            yield OptionList(id="suggestions")
            yield CommandInput(placeholder="search …")
            yield Static("", id="hint")

    async def on_mount(self) -> None:
        inp = self.query_one(CommandInput)
        inp.focus()
        if self._ad_hoc_entries is not None:
            # Mini-picker: no async loads, just render the fixed list.
            self._refresh("")
            self._update_hint("free")
            if self._ad_hoc_title:
                self.query_one("#hint", Static).update(self._ad_hoc_title)
            return
        self._load_commands()
        # Prefetch recordings — used by free, library, transcript modes.
        await self._load_recordings()
        if self._initial:
            inp.value = self._initial
            inp.cursor_position = len(self._initial)
            await self._on_query_change(self._initial)
        else:
            self._refresh("")
            self._update_hint("free")

    # --- data loaders ---------------------------------------------------

    def _load_commands(self) -> None:
        self._commands = [
            Entry(
                kind="command",
                key=f"command:{c.name}",
                display=f"/{c.name}",
                subtitle=c.help,
                search_text=f"/{c.name} {c.help}",
                on_select=_run_cmd(c.name),
            )
            for c in list_commands()
        ]

    async def _load_recordings(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            data = await app.api.library(limit=500)
            items = data.get("items", []) if isinstance(data, dict) else (data or [])
        except Exception:
            items = []
        out: list[Entry] = []
        for r in items:
            rid = str(r.get("id", ""))
            name = r.get("alias") or r.get("filename") or f"#{rid}"
            status = r.get("status", "")
            out.append(Entry(
                kind="recording",
                key=f"recording:{rid}",
                display=f"♪ {name}",
                subtitle=f"{status} · {rid[:8]}",
                search_text=f"{name} {rid}",
                on_select=_open_recording(rid),
            ))
        self._recordings = out

    async def _load_folders(self) -> None:
        if self._folders:
            return
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            folders = await app.api.folders()
        except Exception:
            folders = []
        self._folders = entries_from_folders(folders)

    async def _load_models(self) -> None:
        if self._models:
            return
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            data = await app.api.llm_models()
        except Exception:
            data = {}
        self._models = entries_from_models(data)

    async def _load_tags(self) -> None:
        if self._tags:
            return
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            tags = await app.api.tags()
        except Exception:
            tags = []
        self._tags = entries_from_tags(tags)

    # --- mode parsing ----------------------------------------------------

    def _parse(self, raw: str) -> tuple[str, str]:
        """Return (mode, query)."""
        if raw.startswith("@"):
            return ("transcript", raw[1:].lstrip())
        if raw.startswith("/"):
            rest = raw[1:]
            head, sep, tail = rest.partition(" ")
            if sep == " " and head in SUBPICKERS:
                return (_MODE_BY_COMMAND[head], tail)
            return ("command", rest)
        return ("free", raw)

    def _mode_label(self, mode: str) -> str:
        return {
            "free": "tab complete · ctrl+p commands · esc close",
            "command": "tab complete · enter run · esc close",
            "library": "type to filter · enter opens transcript · esc close",
            "folder": "type to filter · enter scopes library · esc close",
            "tag": "type to filter · enter scopes library · esc close",
            "transcript": "type to filter · enter opens transcript · esc close",
            "analyze": "pick a recording · enter chooses analysis type · esc close",
            "model": "pick a model · enter sets default · esc close",
        }.get(mode, mode)

    def _update_hint(self, mode: str) -> None:
        try:
            self.query_one("#hint", Static).update(self._mode_label(mode))
        except Exception:
            pass

    # --- input events ----------------------------------------------------

    async def on_input_changed(self, event) -> None:
        await self._on_query_change(event.value)

    async def _on_query_change(self, raw: str) -> None:
        mode, _q = self._parse(raw)
        if mode != self._mode:
            self._mode = mode
            self._update_hint(mode)
            if mode == "folder":
                await self._load_folders()
            elif mode == "tag":
                await self._load_tags()
            elif mode == "model":
                await self._load_models()
        self._refresh(raw)

    async def on_input_submitted(self, event) -> None:
        await self._activate_highlighted(fallback_text=event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.run_worker(self._activate(event.option.id), exclusive=False)

    # --- filtering & ranking ---------------------------------------------

    def _pool_for_mode(self, mode: str) -> list[Entry]:
        if self._ad_hoc_entries is not None:
            return self._ad_hoc_entries
        if mode == "command":
            return self._commands
        if mode == "library" or mode == "transcript" or mode == "analyze":
            return self._recordings
        if mode == "folder":
            return self._folders
        if mode == "tag":
            return self._tags
        if mode == "model":
            return self._models
        # free: commands first, then recordings
        return self._commands + self._recordings

    def _refresh(self, raw: str) -> None:
        mode, query = self._parse(raw)
        pool = self._pool_for_mode(mode)
        mru = list(getattr(self.app, "_palette_mru", []))
        mru_rank = {k: len(mru) - i for i, k in enumerate(mru)}

        scored: list[tuple[int, Entry]] = []
        if query:
            for e in pool:
                s = score_match(query, e.search_text)
                if s is None:
                    continue
                if e.key in mru_rank:
                    s += MRU_BONUS + mru_rank[e.key]
                scored.append((s, e))
            scored.sort(key=lambda x: x[0], reverse=True)
        else:
            mru_set = set(mru_rank)
            mru_entries = [e for e in pool if e.key in mru_set]
            mru_entries.sort(key=lambda e: -mru_rank[e.key])
            other = [e for e in pool if e.key not in mru_set]
            scored = [(0, e) for e in mru_entries + other]

        self._visible = [e for _s, e in scored[:200]]
        lst = self.query_one("#suggestions", OptionList)
        lst.clear_options()
        for e in self._visible:
            lst.add_option(Option(
                f"[b]{e.display}[/b]  [dim]{e.subtitle}[/dim]",
                id=e.key,
            ))
        if self._visible:
            lst.highlighted = 0

    # --- actions ---------------------------------------------------------

    def action_next_suggestion(self) -> None:
        lst = self.query_one("#suggestions", OptionList)
        if lst.option_count == 0:
            return
        lst.highlighted = (
            (lst.highlighted + 1) % lst.option_count
            if lst.highlighted is not None
            else 0
        )

    def action_prev_suggestion(self) -> None:
        lst = self.query_one("#suggestions", OptionList)
        if lst.option_count == 0:
            return
        lst.highlighted = (
            (lst.highlighted - 1) % lst.option_count
            if lst.highlighted is not None
            else lst.option_count - 1
        )

    async def action_tab(self) -> None:
        """Tab: in command mode, complete to a unique match; otherwise cycle."""
        inp = self.query_one(CommandInput)
        raw = inp.value
        mode, query = self._parse(raw)
        if mode == "command":
            # Find commands whose name starts with the typed query (case-insens).
            q = query.lower().split(" ", 1)[0]
            matches = [c.name for c in list_commands() if c.name.startswith(q)]
            if len(matches) == 1:
                completed = "/" + matches[0]
                suffix = " " if matches[0] in SUBPICKERS else ""
                inp.value = completed + suffix
                inp.cursor_position = len(inp.value)
                await self._on_query_change(inp.value)
                return
            if len(matches) > 1:
                # Complete to longest common prefix.
                lcp = _longest_common_prefix(matches)
                if lcp and lcp != q:
                    inp.value = "/" + lcp
                    inp.cursor_position = len(inp.value)
                    await self._on_query_change(inp.value)
                    return
        # Fallback: cycle suggestions.
        self.action_next_suggestion()

    # --- activation ------------------------------------------------------

    async def _activate_highlighted(self, fallback_text: str = "") -> None:
        lst = self.query_one("#suggestions", OptionList)
        if lst.option_count and lst.highlighted is not None:
            opt = lst.get_option_at_index(lst.highlighted)
            if opt and opt.id:
                await self._activate(opt.id)
                return
        # No match — if input looks like a raw command, run it.
        text = fallback_text.strip()
        if text.startswith("/"):
            self.app.pop_screen()
            await run_command(self.app, text)

    async def _activate(self, entry_key: str) -> None:
        entry = (
            next((e for e in self._visible if e.key == entry_key), None)
            or next((e for e in self._all_entries() if e.key == entry_key), None)
        )
        if entry is None:
            return
        _push_mru(self.app, entry.key)
        # Ad-hoc mini-picker (e.g. analysis-type chooser) — defer to caller.
        if self._ad_hoc_on_pick is not None:
            self.app.pop_screen()
            await self._ad_hoc_on_pick(self.app, entry)
            return
        # In analyze mode, picking a recording opens the type chooser.
        if self._mode == "analyze" and entry.kind == "recording":
            rec_id = entry.key.split(":", 1)[1]
            self.app.pop_screen()
            _open_analysis_type_picker(self.app, rec_id)
            return
        self.app.pop_screen()
        await entry.on_select(self.app)

    def _all_entries(self) -> list[Entry]:
        if self._ad_hoc_entries is not None:
            return list(self._ad_hoc_entries)
        return (
            self._commands
            + self._recordings
            + self._folders
            + self._tags
            + self._models
        )


# --- helpers --------------------------------------------------------


def _longest_common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    s1, s2 = min(strings), max(strings)
    for i, ch in enumerate(s1):
        if i >= len(s2) or s2[i] != ch:
            return s1[:i]
    return s1


# --- selection adapters --------------------------------------------------


def _run_cmd(name: str):
    async def go(app: "AmicoTUI") -> None:
        await run_command(app, name)
    return go


def _open_recording(rec_id: str):
    async def go(app: "AmicoTUI") -> None:
        from .screens.transcript import TranscriptScreen
        app.push_screen(TranscriptScreen(rec_id))
    return go


def _open_library_folder(folder_id: str):
    async def go(app: "AmicoTUI") -> None:
        from .screens.library import LibraryScreen
        app.push_screen(LibraryScreen(folder_id=folder_id, title=f"Folder · {folder_id[:8]}"))
    return go


def _open_library_tag(tag_id: str):
    async def go(app: "AmicoTUI") -> None:
        from .screens.library import LibraryScreen
        app.push_screen(LibraryScreen(tag_id=tag_id, title=f"Tag · {tag_id[:8]}"))
    return go


def _set_default_model(name: str):
    async def go(app: "AmicoTUI") -> None:
        try:
            await app.api.save_llm_settings(model_name=name)
            app.notify(f"default LLM model: {name}")
        except Exception as e:
            app.notify(f"failed to save: {e}", severity="error")
    return go


ANALYSIS_TYPES = [
    ("summary", "Summarise the transcript"),
    ("action_items", "Extract action items"),
    ("translate", "Translate transcript"),
    ("custom", "Run a custom prompt"),
]


def _open_analysis_type_picker(app: "AmicoTUI", rec_id: str) -> None:
    entries = [
        Entry(
            kind="analysis_type",
            key=f"analysis_type:{name}",
            display=f"✦ {name}",
            subtitle=desc,
            search_text=name,
            on_select=_noop,
        )
        for name, desc in ANALYSIS_TYPES
    ]

    async def on_pick(app: "AmicoTUI", entry: Entry) -> None:
        atype = entry.key.split(":", 1)[1]
        try:
            await app.api.create_analysis(rec_id, atype)
            app.notify(f"{atype} analysis queued for {rec_id[:8]}")
        except Exception as e:
            app.notify(f"analysis failed: {e}", severity="error")

    app.push_screen(Palette(entries=entries, on_pick=on_pick, title="choose analysis type"))


async def _noop(app: "AmicoTUI") -> None:
    return None


def entries_from_folders(folders: list[dict] | None) -> list[Entry]:
    return [
        Entry(
            kind="folder",
            key=f"folder:{f.get('id')}",
            display=f"▣ {f.get('name', '?')}",
            subtitle=f"folder · id {str(f.get('id'))[:8]}",
            search_text=str(f.get("name", "")),
            on_select=_open_library_folder(str(f.get("id"))),
        )
        for f in (folders or []) if f.get("id") is not None
    ]


def entries_from_tags(tags: list[dict] | None) -> list[Entry]:
    return [
        Entry(
            kind="tag",
            key=f"tag:{t.get('id')}",
            display=f"# {t.get('name', '?')}",
            subtitle=f"tag · id {str(t.get('id'))[:8]}",
            search_text=str(t.get("name", "")),
            on_select=_open_library_tag(str(t.get("id"))),
        )
        for t in (tags or []) if t.get("id") is not None
    ]


def entries_from_models(data) -> list[Entry]:
    items = data.get("models") if isinstance(data, dict) else (data or [])
    names: list[str] = []
    for it in items or []:
        if isinstance(it, str):
            names.append(it)
        elif isinstance(it, dict):
            n = it.get("name") or it.get("model")
            if n:
                names.append(str(n))
    return [
        Entry(
            kind="model",
            key=f"model:{n}",
            display=f"⚡ {n}",
            subtitle="set as default LLM model",
            search_text=n,
            on_select=_set_default_model(n),
        )
        for n in names
    ]


def seed_palette(pal: "Palette", text: str) -> None:
    """Helper for commands that re-open the palette pre-seeded."""
    try:
        inp = pal.query_one(CommandInput)
        inp.value = text
        inp.cursor_position = len(text)
    except Exception:
        pass


def _push_mru(app: "AmicoTUI", key: str) -> None:
    mru: deque = getattr(app, "_palette_mru", None)
    if mru is None:
        mru = deque(maxlen=MRU_MAX)
        app._palette_mru = mru  # type: ignore[attr-defined]
    try:
        mru.remove(key)
    except ValueError:
        pass
    mru.append(key)
