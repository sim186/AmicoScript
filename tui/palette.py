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
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from . import actions
from .actions import ANALYSIS_TYPES
from .commands import list_commands, run_command
from .fuzzy import score_match
from .widgets.command_input import CommandInput

if TYPE_CHECKING:
    from .app import AmicoTUI


MRU_MAX = 30
MRU_BONUS = 50

# Commands that, when typed with a trailing space, switch the palette to
# a sub-picker. ``new`` arg of /folder is preserved by falling back to
# raw command execution on Enter when no folder matches the query.
SUBPICKERS = {"library", "folder", "tag", "analyze", "models", "llm", "transcribe", "delete"}
# Map command name → mode key used internally (most are 1:1; /models → "model").
_MODE_BY_COMMAND = {
    "library": "library",
    "folder": "folder",
    "tag": "tag",
    "analyze": "analyze",
    "models": "model",
    "llm": "llm_model",
    "transcribe": "transcribe",
    "delete": "delete",
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
        background: rgba(12,14,26,0.85);
    }
    #box {
        width: 70%;
        max-width: 90;
        height: auto;
        padding: 0;
        background: #12152a;
        border: tall #4a47c0;
    }
    #header {
        height: 1;
        padding: 0 2;
        background: #12152a;
        color: #6b6e9a;
        border-bottom: solid #2a2860;
    }
    #suggestions {
        height: auto;
        max-height: 14;
        background: #12152a;
        border: none;
        color: #dde1ff;
    }
    #suggestions > .option-list--option-highlighted {
        background: #2d2a7a;
        color: #dde1ff;
    }
    CommandInput {
        border: none;
        background: #1a1d35;
        color: #dde1ff;
        height: 1;
        padding: 0 2;
        border-top: solid #4a47c0;
    }
    #hint {
        height: 1;
        color: #6b6e9a;
        background: #12152a;
        padding: 0 2;
        border-top: solid #2a2860;
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
        self._llm_models: list[Entry] = []
        # Visible after filtering, in render order.
        self._visible: list[Entry] = []
        self._mode = "free"
        self._initial = initial
        # Optional ad-hoc mini-picker: a fixed entry list with a custom
        # on-pick handler (overrides each entry's on_select).
        self._ad_hoc_entries = entries
        self._ad_hoc_on_pick = on_pick
        self._ad_hoc_title = title
        # Transcribe-mode file browser state.
        self._current_fs_path: Path = Path.home()
        self._fs_entries: list[Entry] = []
        self._transcribe_library_mode: bool = False
        self._transcribe_filter: str = ""
        self._recording_data: dict[str, dict] = {}

    def compose(self):
        with Vertical(id="box"):
            yield Static("command palette", id="header")
            yield OptionList(id="suggestions")
            yield CommandInput(placeholder="/")
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
        self._recording_data = {}
        out: list[Entry] = []
        for r in items:
            rid = str(r.get("id", ""))
            name = r.get("alias") or r.get("filename") or f"#{rid}"
            status = r.get("status", "")
            self._recording_data[rid] = r
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
            data = await app.api.whisper_models()
        except Exception:
            data = {}
        self._models = entries_from_models(data)

    async def _load_llm_models(self) -> None:
        if self._llm_models:
            return
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            data = await app.api.llm_models()
        except Exception:
            data = {}
        self._llm_models = entries_from_llm_models(data)

    async def _load_tags(self) -> None:
        if self._tags:
            return
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            tags = await app.api.tags()
        except Exception:
            tags = []
        self._tags = entries_from_tags(tags)

    # --- filesystem browser helpers (transcribe mode) --------------------

    def _resolve_fs_query(self, query: str) -> tuple[Path, str]:
        """Parse transcribe query into (directory_path, filter_string)."""
        if not query:
            return (getattr(self, '_current_fs_path', Path.home()), "")
        if query.startswith("/") or (query.startswith("~") and (len(query) == 1 or query[1] == "/")):
            p = Path(query).expanduser()
            if p.is_dir():
                return (p, "")
            parent = p
            while not parent.exists() and parent.parent != parent:
                parent = parent.parent
            parts = p.parts[len(parent.parts):]
            filter_str = "/".join(parts) if parts else ""
            return (parent, filter_str)
        return (getattr(self, '_current_fs_path', Path.home()), query)

    def _build_fs_entries(self, path: Path) -> list[Entry]:
        """List directory contents for the transcribe file browser."""
        from .app import AUDIO_EXTS
        entries: list[Entry] = []
        if path.parent != path:
            entries.append(Entry(
                kind="dir",
                key=f"dir:{path.parent}",
                display="📁 ..",
                subtitle=str(path.parent),
                search_text=".. parent",
                on_select=_noop,
            ))
        try:
            for p in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if p.name.startswith("."):
                    continue
                if p.is_dir():
                    entries.append(Entry(
                        kind="dir",
                        key=f"dir:{p}",
                        display=f"📁 {p.name}/",
                        subtitle="",
                        search_text=p.name,
                        on_select=_noop,
                    ))
                elif p.suffix.lower() in AUDIO_EXTS:
                    entries.append(Entry(
                        kind="file",
                        key=f"file:{p}",
                        display=f"♪ {p.name}",
                        subtitle="",
                        search_text=p.name,
                        on_select=_noop,
                    ))
        except (PermissionError, OSError):
            pass
        return entries

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
        if mode == "transcribe":
            if self._transcribe_library_mode:
                return "library recordings · type to filter · enter re-transcribe · esc close"
            path = getattr(self, '_current_fs_path', Path.home())
            return f"browsing: {path} · type to filter · enter dir or transcribe · @ for library · esc close"
        return {
            "free": "tab complete · ctrl+p commands · esc close",
            "command": "tab complete · enter run · esc close",
            "library": "type to filter · enter opens transcript · esc close",
            "folder": "type to filter · enter scopes library · esc close",
            "tag": "type to filter · enter scopes library · esc close",
            "transcript": "type to filter · enter opens transcript · esc close",
            "analyze": "pick a recording · enter chooses analysis type · esc close",
            "delete": "type to filter · enter deletes recording · esc close",
            "model": "pick a Whisper model · enter sets default · esc close",
            "llm_model": "pick an LLM model · enter sets default · esc close",
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
        mode, query = self._parse(raw)
        mode_changed = mode != self._mode
        if mode_changed:
            self._mode = mode
            if mode == "folder":
                await self._load_folders()
            elif mode == "tag":
                await self._load_tags()
            elif mode == "model":
                await self._load_models()
            elif mode == "llm_model":
                await self._load_llm_models()

        if mode == "transcribe":
            q = query.strip()
            if q.startswith("@"):
                self._transcribe_library_mode = True
                self._transcribe_filter = q[1:].lstrip()
            else:
                self._transcribe_library_mode = False
                self._current_fs_path, self._transcribe_filter = self._resolve_fs_query(q)
                self._fs_entries = self._build_fs_entries(self._current_fs_path)

        if mode_changed or mode == "transcribe":
            self._update_hint(mode)

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
        if mode == "library" or mode == "transcript" or mode == "analyze" or mode == "delete":
            return self._recordings
        if mode == "folder":
            return self._folders
        if mode == "tag":
            return self._tags
        if mode == "model":
            return self._models
        if mode == "llm_model":
            return self._llm_models
        if mode == "transcribe":
            if self._transcribe_library_mode:
                return self._recordings
            return self._fs_entries
        # free: commands first, then recordings
        return self._commands + self._recordings

    def _refresh(self, raw: str) -> None:
        mode, query = self._parse(raw)
        pool = self._pool_for_mode(mode)

        if mode == "transcribe":
            effective_query = self._transcribe_filter
        else:
            effective_query = query

        mru = list(getattr(self.app, "_palette_mru", []))
        mru_rank = {k: len(mru) - i for i, k in enumerate(mru)}

        scored: list[tuple[int, Entry]] = []
        if effective_query:
            for e in pool:
                s = score_match(effective_query, e.search_text)
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
                f"[b #7c79f0]{e.display:<14}[/]  [#6b6e9a]{e.subtitle}[/]",
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
        """Tab: complete to highlighted suggestion, then prefix; otherwise cycle."""
        inp = self.query_one(CommandInput)
        lst = self.query_one("#suggestions", OptionList)

        # Prefer completing to the currently highlighted suggestion.
        if lst.option_count and lst.highlighted is not None:
            opt = lst.get_option_at_index(lst.highlighted)
            if opt and opt.id:
                entry = next((e for e in self._visible if e.key == opt.id), None)
                if entry:
                    completed = _completion_text(entry)
                    if completed:
                        inp.value = completed
                        inp.cursor_position = len(inp.value)
                        await self._on_query_change(inp.value)
                        return

        # Fallback: prefix-based completion in command mode.
        raw = inp.value
        mode, query = self._parse(raw)
        if mode == "command":
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
                lcp = _longest_common_prefix(matches)
                if lcp and lcp != q:
                    inp.value = "/" + lcp
                    inp.cursor_position = len(inp.value)
                    await self._on_query_change(inp.value)
                    return
        # Final fallback: cycle suggestions.
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
            mode, _ = self._parse(text)
            if mode == "transcribe":
                return
            self.app.pop_screen()
            await run_command(self.app, text)

    async def _activate(self, entry_key: str) -> None:
        entry = (
            next((e for e in self._visible if e.key == entry_key), None)
            or next((e for e in self._all_entries() if e.key == entry_key), None)
        )
        if entry is None:
            return
        # Ad-hoc mini-pickers (analysis type, bulk actions, move/tag…) are
        # rebuilt fresh on every open and often reuse the same entry keys
        # across unrelated invocations (e.g. "bulk:delete" for whichever
        # recordings happen to be selected this time) — MRU-boosting those
        # would silently reorder the list and make a bare Enter trigger a
        # different action than the one actually on top. Only the
        # persistent command/recording palette benefits from MRU ranking.
        if self._ad_hoc_on_pick is not None:
            self.app.pop_screen()
            await self._ad_hoc_on_pick(self.app, entry)
            return
        _push_mru(self.app, entry.key)
        # In delete mode, picking a recording confirms, then deletes.
        if self._mode == "delete" and entry.kind == "recording":
            rec_id = entry.key.split(":", 1)[1]
            app: "AmicoTUI" = self.app  # type: ignore[assignment]
            self.app.pop_screen()
            await actions.delete_recording(app, rec_id)
            return

        # In analyze mode, picking a recording opens the type chooser.
        if self._mode == "analyze" and entry.kind == "recording":
            rec_id = entry.key.split(":", 1)[1]
            self.app.pop_screen()
            open_analysis_type_picker(self.app, rec_id)
            return
        # Transcribe mode: file browser or library recording re-transcribe.
        if self._mode == "transcribe":
            if entry.kind == "dir":
                new_path = Path(entry.key.split(":", 1)[1])
                self._current_fs_path = new_path
                self._transcribe_filter = ""
                self._fs_entries = self._build_fs_entries(new_path)
                inp = self.query_one(CommandInput)
                inp.value = f"/transcribe {new_path}/"
                inp.cursor_position = len(inp.value)
                self._refresh(inp.value)
                return
            elif entry.kind == "file":
                from .app import shquote
                file_path = entry.key.split(":", 1)[1]
                self.app.pop_screen()
                await run_command(self.app, f"transcribe {shquote(file_path)}")
                return
            elif entry.kind == "recording" and self._transcribe_library_mode:
                rid = entry.key.split(":", 1)[1]
                rec_data = self._recording_data.get(rid, {})
                file_path = rec_data.get("file_path", "")
                if file_path:
                    from .app import shquote
                    self.app.pop_screen()
                    await run_command(self.app, f"transcribe {shquote(file_path)}")
                else:
                    self.app.notify(f"source file not available for {rid[:8]}", severity="warning")
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
            + self._llm_models
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


def _completion_text(entry: Entry) -> str:
    """Generate the slash-command text for a completed entry."""
    if entry.kind == "command":
        cmd_name = entry.key.split(":", 1)[1]
        suffix = " " if cmd_name in SUBPICKERS else ""
        return "/" + cmd_name + suffix
    if entry.kind == "recording":
        name = entry.display.removeprefix("♪ ").strip()
        return "/library " + name
    if entry.kind == "folder":
        name = entry.display.removeprefix("▣ ").strip()
        return "/folder " + name
    if entry.kind == "tag":
        name = entry.display.removeprefix("# ").strip()
        return "/tag " + name
    if entry.kind == "model":
        return "/models " + entry.display.strip()
    if entry.kind == "llm_model":
        return "/llm " + entry.display.strip()
    if entry.kind in ("file", "dir"):
        path = entry.key.split(":", 1)[1]
        suffix = "/" if entry.kind == "dir" else ""
        return "/transcribe " + path + suffix
    return ""


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


def _set_whisper_model(name: str):
    async def go(app: "AmicoTUI") -> None:
        try:
            await app.api.save_whisper_model(name)
            app.notify(f"whisper model set to {name}")
        except Exception as e:
            app.notify(f"failed to save: {e}", severity="error")
    return go


def _set_llm_model(name: str):
    async def go(app: "AmicoTUI") -> None:
        try:
            await app.api.save_llm_settings(model_name=name)
            app.notify(f"LLM model set to {name}")
        except Exception as e:
            app.notify(f"failed to save: {e}", severity="error")
    return go


def open_analysis_type_picker(app: "AmicoTUI", rec_id: str) -> None:
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
        await actions.create_analysis(app, rec_id, entry.key.split(":", 1)[1])

    app.push_screen(Palette(entries=entries, on_pick=on_pick, title="choose analysis type"))


async def _noop(app: "AmicoTUI") -> None:
    return None


def _folder_entries(folders: list[dict] | None, include_none: bool = True) -> list[Entry]:
    entries: list[Entry] = []
    if include_none:
        entries.append(Entry(
            kind="folder",
            key="folder:",
            display="▢ (no folder)",
            subtitle="remove from any folder",
            search_text="no folder none",
            on_select=_noop,
        ))
    entries += [
        Entry(
            kind="folder",
            key=f"folder:{f.get('id')}",
            display=f"▣ {f.get('name', '?')}",
            subtitle=f"folder · id {str(f.get('id'))[:8]}",
            search_text=str(f.get("name", "")),
            on_select=_noop,
        )
        for f in (folders or []) if f.get("id") is not None
    ]
    return entries


def _tag_entries(tags: list[dict] | None, applied_ids: set[str] | None = None) -> list[Entry]:
    applied_ids = applied_ids or set()
    return [
        Entry(
            kind="tag",
            key=f"tag:{t.get('id')}",
            display=f"{'●' if str(t.get('id')) in applied_ids else '○'} {t.get('name', '?')}",
            subtitle="applied — enter removes" if str(t.get("id")) in applied_ids else "enter adds",
            search_text=str(t.get("name", "")),
            on_select=_noop,
        )
        for t in (tags or []) if t.get("id") is not None
    ]


def _open_move_to_folder_picker(app: "AmicoTUI", rec_id: str) -> None:
    async def build_and_push() -> None:
        try:
            folders = await app.api.folders()
        except Exception as e:
            app.notify(f"folders load failed: {e}", severity="error")
            return
        entries = _folder_entries(folders)

        async def on_pick(app: "AmicoTUI", entry: Entry) -> None:
            folder_id = entry.key.split(":", 1)[1]
            try:
                await app.api.update_recording(rec_id, folder_id=folder_id)
                app.notify("moved to folder" if folder_id else "removed from folder")
                screen = app.screen
                if hasattr(screen, "refresh_library"):
                    screen.refresh_library()
            except Exception as e:
                app.notify(f"move failed: {e}", severity="error")

        app.push_screen(Palette(entries=entries, on_pick=on_pick, title=f"move {rec_id[:8]} to…"))

    app.run_worker(build_and_push(), exclusive=False)


def _open_tag_toggle_picker(app: "AmicoTUI", rec_id: str) -> None:
    async def build_and_push() -> None:
        try:
            rec = await app.api.recording(rec_id)
            all_tags = await app.api.tags()
        except Exception as e:
            app.notify(f"tags load failed: {e}", severity="error")
            return
        if not all_tags:
            app.notify("no tags yet — create one with /tag new <name>")
            return
        applied_ids = {str(t.get("id")) for t in (rec.get("tags") or [])}
        entries = _tag_entries(all_tags, applied_ids)

        async def on_pick(app: "AmicoTUI", entry: Entry) -> None:
            tag_id = entry.key.split(":", 1)[1]
            try:
                if tag_id in applied_ids:
                    await app.api.remove_tag(rec_id, tag_id)
                    app.notify("tag removed")
                else:
                    await app.api.add_tag(rec_id, tag_id)
                    app.notify("tag added")
                screen = app.screen
                if hasattr(screen, "refresh_library"):
                    screen.refresh_library()
            except Exception as e:
                app.notify(f"tag update failed: {e}", severity="error")

        app.push_screen(Palette(entries=entries, on_pick=on_pick, title=f"toggle tags on {rec_id[:8]}"))

    app.run_worker(build_and_push(), exclusive=False)


def open_bulk_move_picker(app: "AmicoTUI", rec_ids: list[str], on_done) -> None:
    """Move several recordings to one folder. ``on_done()`` is called after."""
    async def build_and_push() -> None:
        try:
            folders = await app.api.folders()
        except Exception as e:
            app.notify(f"folders load failed: {e}", severity="error")
            return
        entries = _folder_entries(folders)

        async def on_pick(app: "AmicoTUI", entry: Entry) -> None:
            folder_id = entry.key.split(":", 1)[1]
            app.push_busy()
            errors = 0
            for rec_id in rec_ids:
                try:
                    await app.api.update_recording(rec_id, folder_id=folder_id)
                except Exception:
                    errors += 1
            app.pop_busy()
            ok = len(rec_ids) - errors
            app.notify(f"moved {ok}/{len(rec_ids)}" + (f" ({errors} failed)" if errors else ""))
            on_done()

        app.push_screen(Palette(entries=entries, on_pick=on_pick, title=f"move {len(rec_ids)} to…"))

    app.run_worker(build_and_push(), exclusive=False)


def open_bulk_tag_picker(app: "AmicoTUI", rec_ids: list[str], on_done) -> None:
    """Add one tag to several recordings. ``on_done()`` is called after."""
    async def build_and_push() -> None:
        try:
            all_tags = await app.api.tags()
        except Exception as e:
            app.notify(f"tags load failed: {e}", severity="error")
            return
        if not all_tags:
            app.notify("no tags yet — create one with /tag new <name>")
            return
        entries = _tag_entries(all_tags)

        async def on_pick(app: "AmicoTUI", entry: Entry) -> None:
            tag_id = entry.key.split(":", 1)[1]
            app.push_busy()
            errors = 0
            for rec_id in rec_ids:
                try:
                    await app.api.add_tag(rec_id, tag_id)
                except Exception:
                    errors += 1
            app.pop_busy()
            ok = len(rec_ids) - errors
            app.notify(f"tagged {ok}/{len(rec_ids)}" + (f" ({errors} failed)" if errors else ""))
            on_done()

        app.push_screen(Palette(entries=entries, on_pick=on_pick, title=f"tag {len(rec_ids)} recordings…"))

    app.run_worker(build_and_push(), exclusive=False)

    app.run_worker(build_and_push(), exclusive=False)


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
    entries: list[Entry] = []
    for it in items or []:
        if isinstance(it, dict):
            mid = str(it.get("id", ""))
            if not mid:
                continue
            name = it.get("name", mid)
            params = it.get("params", "")
            ram = it.get("ram", "")
            subtitle = f"Whisper · {params} · {ram} · accuracy {it.get('accuracy', '?')}/5"
        elif isinstance(it, str):
            mid = it
            name = it
            subtitle = "Whisper model"
        else:
            continue
        entries.append(Entry(
            kind="model",
            key=f"model:{mid}",
            display=f"{name}",
            subtitle=subtitle,
            search_text=f"{mid} {name}",
            on_select=_set_whisper_model(mid),
        ))
    return entries


def entries_from_llm_models(data) -> list[Entry]:
    items = data if isinstance(data, list) else (data.get("models") if isinstance(data, dict) else [])
    entries: list[Entry] = []
    for it in items or []:
        if isinstance(it, str):
            mid = it
            name = it
        elif isinstance(it, dict):
            mid = str(it.get("id") or it.get("name") or it.get("model") or "")
            name = str(it.get("name") or it.get("id") or mid)
        else:
            continue
        if not mid:
            continue
        entries.append(Entry(
            kind="llm_model",
            key=f"llm_model:{mid}",
            display=f"{name}",
            subtitle="set as default LLM model",
            search_text=mid,
            on_select=_set_llm_model(mid),
        ))
    return entries


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
