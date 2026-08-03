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
}


def _fmt_duration(seconds):
    if not seconds:
        return "--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    return f"{h:d}h {m:02d}m"


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
        Binding("R", "rename_row", "Rename"),
        Binding("m", "move_row", "Move"),
        Binding("t", "tag_row", "Tag"),
        Binding("y", "copy_name", "Copy name"),
        Binding("enter", "open", "Open"),
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

    def compose(self):
        with Vertical():
            yield DataTable(cursor_type="row", zebra_stripes=False)

    def on_mount(self) -> None:
        self.table = self.query_one(DataTable)
        self.table.add_columns("FILE", "DATE", "DUR", "MODEL", "TAGS", "STATUS")
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

    async def _delete_selected(self, rec_id: str) -> None:
        from ..widgets.confirm import ConfirmDialog
        confirmed = await self.app.push_screen_wait(
            ConfirmDialog(f"Delete recording {rec_id[:8]}…? This cannot be undone.")
        )
        if not confirmed:
            return
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        app.push_busy()
        try:
            await app.api.delete_recording(rec_id)
            app.notify(f"deleted {rec_id[:8]}")
            self.refresh_library()
        except Exception as e:
            app.notify(f"delete failed: {e}", severity="error")
        finally:
            app.pop_busy()

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
        from ..palette import _open_move_to_folder_picker
        _open_move_to_folder_picker(self.app, rec_id)  # type: ignore[arg-type]

    def action_tag_row(self) -> None:
        rec_id = self._selected_id()
        if rec_id is None:
            return
        from ..palette import _open_tag_toggle_picker
        _open_tag_toggle_picker(self.app, rec_id)  # type: ignore[arg-type]

    def action_copy_name(self) -> None:
        rec_id = self._selected_id()
        if rec_id is None or self.table is None:
            return
        row = self.table.get_row_at(self.table.cursor_row)
        name_cell = row[0]
        name = name_cell.plain if isinstance(name_cell, Text) else str(name_cell)
        if copy_to_clipboard(name):
            self.app.notify(f"copied: {name}")

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
        assert self.table is not None
        self.table.clear()
        self.row_keys.clear()
        total_dur = 0.0
        for r in items:
            name = r.get("alias") or r.get("filename") or f"#{r.get('id')}"
            model = r.get("model_size") or r.get("model") or ""
            dur = r.get("duration") or 0
            try:
                total_dur += float(dur or 0)
            except (TypeError, ValueError):
                pass
            self.table.add_row(
                Text(name, style="#dde1ff"),
                Text(_fmt_date(r.get("created_at")), style="#6b6e9a"),
                Text(_fmt_duration(dur), style="#6b6e9a"),
                Text(model, style="#7c79f0"),
                _fmt_tags(r.get("tags")),
                _fmt_status(r.get("status", "")),
            )
            self.row_keys.append(str(r["id"]))
        if self.on_loaded:
            self.on_loaded(len(items), total_dur)

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
        name_cell = row[0]
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
            "↑↓ navigate  ·  ↵ open  ·  R rename  ·  m move  ·  t tag  ·  d delete  ·  /search",
            id="ctxhint",
        )
        yield CommandBar(id="cmdbar")
        yield StatusBar(id="statusbar")

    def on_mount(self) -> None:
        self.query_one(LibraryPanel).query_one(DataTable).focus()

    def _on_loaded(self, count: int, total_dur: float) -> None:
        h = int(total_dur // 3600)
        m = int((total_dur % 3600) // 60)
        try:
            self.query_one("#ctxhint", ContextHint).set_text(
                f"{count} recordings  ·  {h}h {m:02d}m total  "
                f"|  ↑↓ navigate  ·  ↵ open  ·  R rename  ·  m move  ·  t tag  ·  d delete"
            )
        except Exception:
            pass

    def action_pop(self) -> None:
        self.app.pop_screen()


