"""Library panel: list of recordings with keyboard navigation."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import DataTable

from .. import actions
from ..clipboard import copy_to_clipboard
from ..widgets.chrome import CommandBar, ContextHint, TitleBar

if TYPE_CHECKING:
    from ..app import AmicoTUI


STATUS_DISPLAY = {
    "pending":     ("○", "queued",      "#6b6e9a"),
    "queued":      ("○", "queued",      "#6b6e9a"),
    "transcribing":("⠸", "proc",        "#f59e0b"),
    "diarizing":   ("⠴", "diariz",      "#f59e0b"),
    "done":        ("●", "done",        "#22c55e"),
    "completed":   ("●", "done",        "#22c55e"),
    "error":       ("✗", "error",       "#ef4444"),
    # A restart stopped this one — the audio is still there, so it can be
    # transcribed again rather than being a dead end.
    "interrupted": ("⚠", "interrupt",   "#f97316"),
    "cancelled":   ("⊘", "cancelled",   "#6b6e9a"),
    "downloading": ("⇣", "download",    "#f59e0b"),
    "loading_model": ("⠿", "loading",   "#f59e0b"),
    "translating": ("⠧", "translate",   "#f59e0b"),
}

# Where a recording came from. An auto-captured call and a dragged-in file
# otherwise look identical in the list.
SOURCE_MARK = {
    "meeting": ("◉", "#fb7185"),
    "url": ("↗", "#38bdf8"),
}

# Statuses whose work is over, so re-running them is meaningful.
# Re-exported: the rule lives with the action that applies it.
RETRYABLE = actions.RETRYABLE


def _fmt_duration(seconds):
    """Human duration. Anything under an hour keeps its seconds.

    Formatting everything as hours+minutes rendered a 22-second clip as
    "0h 00m", which reads as empty rather than short.
    """
    if not seconds:
        return "--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:d}h {m:02d}m"
    return f"{m:d}:{sec:02d}"


def _fmt_date(value):
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d"
        )
    except ValueError:
        return value[:10]


def _fmt_status(status: str) -> Text:
    icon, label, color = STATUS_DISPLAY.get(status, ("·", status or "?", "#6b6e9a"))
    return Text(f"{icon} {label}", style=color)


def _fmt_name(record: dict) -> Text:
    """File name, prefixed with a mark when it was captured or imported."""
    name = record.get("alias") or record.get("filename") or f"#{record.get('id', '')}"
    mark, colour = SOURCE_MARK.get(record.get("source", ""), ("", ""))
    text = Text()
    if mark:
        text.append(f"{mark} ", style=colour)
    text.append(name, style="#dde1ff")
    return text


def _fmt_tags(tags) -> Text:
    if not tags:
        return Text("")
    out = Text()
    for i, t in enumerate(tags[:3]):
        if isinstance(t, dict):
            name = t.get("name", "")
            color = t.get("color_code") or "#7c79f0"
        else:
            name = str(t)
            color = "#7c79f0"
        if i:
            out.append(" ")
        out.append(f"[{name}]", style=color)
    return out


class LibraryPanel(Widget):
    """Recording list panel."""

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("G", "cursor_bottom", show=False),
        Binding("g,g", "cursor_top", show=False),
        Binding("d", "delete_row", "Delete"),
        Binding("ctrl+r", "retry_row", "Retry"),
        Binding("R", "rename_row", "Rename"),
        Binding("m", "move_row", "Move"),
        Binding("t", "tag_row", "Tag"),
        Binding("y", "copy_name", "Copy name"),
        Binding("enter", "open", "Open"),
        Binding("v", "toggle_select", "Select", show=False),
        Binding("x", "bulk_menu", "Bulk actions"),
    ]

    DEFAULT_CSS = """
    LibraryPanel { layout: vertical; height: 1fr; }
    DataTable { height: 1fr; background: #0c0e1a; }
    """

    def __init__(
        self,
        status_filter: str | None = None,
        folder_id: str | None = None,
        tag_id: str | None = None,
        on_loaded=None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.table: DataTable | None = None
        self.row_keys: list[str] = []
        self.status_filter = status_filter
        self.folder_id = folder_id
        self.tag_id = tag_id
        self.on_loaded = on_loaded
        self._items: list[dict] = []
        self.selected_ids: set[str] = set()

    def compose(self):
        with Vertical():
            yield DataTable(cursor_type="row", zebra_stripes=False)

    def on_mount(self) -> None:
        self.table = self.query_one(DataTable)
        self.table.add_columns("", "FILE", "DATE", "DUR", "MODEL", "TAGS", "STATUS")
        self.refresh_library()

    def on_show(self) -> None:
        if self.table is not None:
            self.refresh_library()

    # --- actions ----------------------------------------------------

    def action_refresh(self) -> None:
        self.refresh_library()

    def action_cursor_down(self) -> None:
        if self.table:
            self.table.action_cursor_down()

    def action_cursor_up(self) -> None:
        if self.table:
            self.table.action_cursor_up()

    def action_cursor_top(self) -> None:
        if self.table:
            self.table.move_cursor(row=0)

    def action_cursor_bottom(self) -> None:
        if self.table and self.table.row_count:
            self.table.move_cursor(row=self.table.row_count - 1)

    def action_delete_row(self) -> None:
        rec_id = self._selected_id()
        if rec_id is None:
            return
        self.run_worker(self._delete_selected(rec_id), exclusive=False)

    def action_retry_row(self) -> None:
        rec_id = self._selected_id()
        if rec_id is None:
            return
        self.run_worker(self._retry_selected(rec_id), exclusive=False)

    async def _retry_selected(self, rec_id: str) -> None:
        """Transcribe the highlighted recording again.

        The row is already loaded, so pass it: that is what lets the refusal
        for a still-running recording be explained without a round trip.
        """
        record = next((r for r in self._items if str(r["id"]) == rec_id), None)
        await actions.retry_recording(self.app, rec_id, record=record)

    async def _delete_selected(self, rec_id: str) -> None:
        await actions.delete_recording(self.app, rec_id)

    def action_rename_row(self) -> None:
        rec_id = self._selected_id()
        if rec_id is None:
            return
        self.run_worker(self._rename_selected(rec_id), exclusive=False)

    async def _rename_selected(self, rec_id: str) -> None:
        from ..widgets.prompt import PromptDialog
        current = self._selected_name() or ""
        new_name = await self.app.push_screen_wait(
            PromptDialog("Rename recording to:", initial=current)
        )
        if not new_name or new_name == current:
            return
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        app.push_busy()
        try:
            await app.api.update_recording(rec_id, alias=new_name)
            app.notify(f"renamed to {new_name}")
            self.refresh_library()
        except Exception as e:
            app.notify(f"rename failed: {e}", severity="error")
        finally:
            app.pop_busy()

    def action_move_row(self) -> None:
        rec_id = self._selected_id()
        if rec_id is None:
            return
        from ..palette import open_move_to_folder_picker
        open_move_to_folder_picker(self.app, rec_id)  # type: ignore[arg-type]

    def action_tag_row(self) -> None:
        rec_id = self._selected_id()
        if rec_id is None:
            return
        from ..palette import open_tag_toggle_picker
        open_tag_toggle_picker(self.app, rec_id)  # type: ignore[arg-type]

    def action_copy_name(self) -> None:
        rec_id = self._selected_id()
        if rec_id is None or self.table is None:
            return
        row = self.table.get_row_at(self.table.cursor_row)
        name_cell = row[1]
        name = name_cell.plain if isinstance(name_cell, Text) else str(name_cell)
        if copy_to_clipboard(name):
            self.app.notify(f"copied: {name}")

    # --- multi-select / bulk actions ---------------------------------

    def action_toggle_select(self) -> None:
        rec_id = self._selected_id()
        if rec_id is None:
            return
        self.selected_ids.symmetric_difference_update({rec_id})
        self._render_rows()
        if self.table and self.table.row_count:
            self.table.action_cursor_down()

    def action_bulk_menu(self) -> None:
        if not self.selected_ids:
            self.app.notify("select rows first (Space), then x for bulk actions")
            return
        n = len(self.selected_ids)
        from ..entries import Entry, noop
        from ..palette import Palette

        entries = [
            Entry(kind="bulk", key="bulk:delete", display=f"🗑  Delete {n} selected",
                  subtitle="", search_text="delete", on_select=noop),
            Entry(kind="bulk", key="bulk:export", display=f"⇩  Export {n} selected (combined markdown)",
                  subtitle="", search_text="export", on_select=noop),
            Entry(kind="bulk", key="bulk:move", display=f"▣  Move {n} selected to folder…",
                  subtitle="", search_text="move", on_select=noop),
            Entry(kind="bulk", key="bulk:tag", display=f"#  Tag {n} selected…",
                  subtitle="", search_text="tag", on_select=noop),
            Entry(kind="bulk", key="bulk:clear", display="Clear selection",
                  subtitle="", search_text="clear", on_select=noop),
        ]

        async def on_pick(app: "AmicoTUI", entry: Entry) -> None:
            action = entry.key.split(":", 1)[1]
            if action == "delete":
                self.run_worker(self._bulk_delete(), exclusive=False)
            elif action == "export":
                self.run_worker(self._bulk_export(), exclusive=False)
            elif action == "move":
                from ..palette import open_bulk_move_picker
                open_bulk_move_picker(self.app, list(self.selected_ids), self._after_bulk)  # type: ignore[arg-type]
            elif action == "tag":
                from ..palette import open_bulk_tag_picker
                open_bulk_tag_picker(self.app, list(self.selected_ids), self._after_bulk)  # type: ignore[arg-type]
            elif action == "clear":
                self.selected_ids.clear()
                self._render_rows()

        self.app.push_screen(Palette(entries=entries, on_pick=on_pick, title=f"bulk actions ({n} selected)"))

    def _after_bulk(self) -> None:
        self.selected_ids.clear()
        self.refresh_library()

    async def _bulk_delete(self) -> None:
        if await actions.delete_recordings(self.app, list(self.selected_ids)):
            self._after_bulk()

    async def _bulk_export(self) -> None:
        from pathlib import Path
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        app.push_busy()
        try:
            body, filename = await app.api.bulk_export_md(list(self.selected_ids))
            out = Path.cwd() / (filename or "transcripts.md")
            out.write_bytes(body)
            app.notify(f"saved: {out}")
            self._after_bulk()
        except Exception as e:
            app.notify(f"bulk export failed: {e}", severity="error")
        finally:
            app.pop_busy()

    def action_open(self) -> None:
        rec_id = self._selected_id()
        if rec_id is None:
            return
        from .transcript import TranscriptScreen
        self.app.push_screen(TranscriptScreen(rec_id))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open()

    # --- data load --------------------------------------------------

    def refresh_library(self) -> None:
        self.run_worker(self._load(), exclusive=True)

    async def _load(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        try:
            data = await app.api.library(
                limit=200,
                status=self.status_filter,
                folder_id=self.folder_id,
                tag_id=self.tag_id,
            )
        except Exception as e:
            self.app.notify(f"library load failed: {e}", severity="error")
            return
        items = data.get("items", []) if isinstance(data, dict) else data
        self._items = items
        live_ids = {str(r["id"]) for r in items}
        self.selected_ids &= live_ids
        self._render_rows()

    def _render_rows(self) -> None:
        assert self.table is not None
        cursor_row = self.table.cursor_row
        self.table.clear()
        self.row_keys.clear()
        total_dur = 0.0
        for r in self._items:
            rec_id = str(r["id"])
            options = r.get("transcription_options") or {}
            model = (
                r.get("model_size")
                or r.get("model")
                or (options.get("model") if isinstance(options, dict) else "")
                or ""
            )
            dur = r.get("duration") or 0
            try:
                total_dur += float(dur or 0)
            except (TypeError, ValueError):
                pass
            checked = "◉" if rec_id in self.selected_ids else " "
            self.table.add_row(
                Text(checked, style="#7c79f0" if rec_id in self.selected_ids else "#3a3d6a"),
                _fmt_name(r),
                Text(_fmt_date(r.get("created_at")), style="#6b6e9a"),
                Text(_fmt_duration(dur), style="#6b6e9a"),
                Text(model, style="#7c79f0"),
                _fmt_tags(r.get("tags")),
                _fmt_status(r.get("status", "")),
            )
            self.row_keys.append(rec_id)
        if self.table.row_count:
            self.table.move_cursor(row=min(cursor_row, self.table.row_count - 1))
        if self.on_loaded:
            self.on_loaded(len(self._items), total_dur, len(self.selected_ids))

    def _selected_id(self) -> str | None:
        if not self.table or self.table.row_count == 0:
            return None
        idx = self.table.cursor_row
        if 0 <= idx < len(self.row_keys):
            return self.row_keys[idx]
        return None

    def _selected_name(self) -> str | None:
        if not self.table or self.table.row_count == 0:
            return None
        row = self.table.get_row_at(self.table.cursor_row)
        name_cell = row[1]
        return name_cell.plain if isinstance(name_cell, Text) else str(name_cell)


class LibraryScreen(Screen):
    """Full-screen library view."""

    BINDINGS = [
        Binding("escape", "pop", "Back"),
    ]

    leader_chords = {
        "j": ("Jobs", "/jobs"),
        "s": ("Settings", "/settings"),
        "i": ("Import", "/import"),
        "h": ("Welcome", "/welcome"),
        "question_mark": ("Help", "/help"),
        "q": ("Quit", "/quit"),
    }

    DEFAULT_CSS = """
    LibraryScreen { layout: vertical; }
    LibraryPanel { height: 1fr; }
    """

    def __init__(
        self,
        status_filter: str | None = None,
        folder_id: str | None = None,
        tag_id: str | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__()
        self.status_filter = status_filter
        self.folder_id = folder_id
        self.tag_id = tag_id
        self.title = title or (
            "Library" if not status_filter else f"Library · {status_filter}"
        )

    def compose(self):
        from ..widgets.status_bar import StatusBar
        yield TitleBar(id="titlebar")
        with Vertical():
            yield LibraryPanel(
                status_filter=self.status_filter,
                folder_id=self.folder_id,
                tag_id=self.tag_id,
                on_loaded=self._on_loaded,
                id="library_panel",
            )
        yield ContextHint(
            "↑↓ navigate  ·  ↵ open  ·  v select  ·  x bulk  ·  R rename  ·  d delete  ·  /search",
            id="ctxhint",
        )
        yield CommandBar(id="cmdbar")
        yield StatusBar(id="statusbar")

    def on_mount(self) -> None:
        self.query_one(LibraryPanel).query_one(DataTable).focus()

    def _on_loaded(self, count: int, total_dur: float, selected: int = 0) -> None:
        h = int(total_dur // 3600)
        m = int((total_dur % 3600) // 60)
        sel = f"{selected} selected  ·  " if selected else ""
        try:
            self.query_one("#ctxhint", ContextHint).set_text(
                f"{count} recordings  ·  {h}h {m:02d}m total  ·  {sel}"
                f"↑↓ navigate  ·  ↵ open  ·  v select  ·  x bulk  ·  R rename  ·  d delete"
            )
        except Exception:
            pass

    def action_pop(self) -> None:
        self.app.pop_screen()


