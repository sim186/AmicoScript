"""Library panel: list of recordings with keyboard navigation."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header

from ..clipboard import copy_to_clipboard

if TYPE_CHECKING:
    from ..app import AmicoTUI


STATUS_ICON = {
    "pending": "○",
    "queued": "○",
    "transcribing": "◐",
    "diarizing": "◑",
    "done": "●",
    "completed": "●",
    "error": "✗",
}


def _fmt_duration(seconds):
    if not seconds:
        return "--:--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fmt_date(value):
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M"
        )
    except ValueError:
        return value[:16]


class LibraryPanel(Widget):
    """Recording list panel, embeddable in a tab."""

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("G", "cursor_bottom", show=False),
        Binding("g,g", "cursor_top", show=False),
        Binding("d", "delete_row", "Delete"),
        Binding("y", "copy_name", "Copy name"),
        Binding("enter", "open", "Open"),
    ]

    DEFAULT_CSS = """
    LibraryPanel { layout: vertical; height: 1fr; }
    DataTable { height: 1fr; }
    """

    def __init__(
        self,
        status_filter: str | None = None,
        folder_id: str | None = None,
        tag_id: str | None = None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.table: DataTable | None = None
        self.row_keys: list[str] = []
        self.status_filter = status_filter
        self.folder_id = folder_id
        self.tag_id = tag_id

    def compose(self):
        with Vertical():
            yield DataTable(cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.table = self.query_one(DataTable)
        self.table.add_columns("", "Name", "Duration", "Status", "Created")
        self.refresh_library()

    def on_show(self) -> None:
        # Re-fetch when tab re-activated.
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
        self.app.notify(f"/delete {rec_id} to confirm deletion")

    def action_copy_name(self) -> None:
        rec_id = self._selected_id()
        if rec_id is None or self.table is None:
            return
        row = self.table.get_row_at(self.table.cursor_row)
        name = str(row[1])
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
        for r in items:
            icon = STATUS_ICON.get(r.get("status", ""), "·")
            name = r.get("alias") or r.get("filename") or f"#{r.get('id')}"
            self.table.add_row(
                icon,
                name,
                _fmt_duration(r.get("duration")),
                r.get("status", ""),
                _fmt_date(r.get("created_at")),
            )
            self.row_keys.append(str(r["id"]))

    def _selected_id(self) -> str | None:
        if not self.table or self.table.row_count == 0:
            return None
        idx = self.table.cursor_row
        if 0 <= idx < len(self.row_keys):
            return self.row_keys[idx]
        return None


class LibraryScreen(Screen):
    """Full-screen library view. Pushed by leader chord or /library."""

    BINDINGS = [
        Binding("escape", "pop", "Back"),
    ]

    leader_chords = {
        "j": ("Jobs", "/jobs"),
        "s": ("Settings", "/settings"),
        "h": ("Welcome", "/welcome"),
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
        yield Header(show_clock=False)
        with Vertical():
            yield LibraryPanel(
                status_filter=self.status_filter,
                folder_id=self.folder_id,
                tag_id=self.tag_id,
                id="library_panel",
            )
            yield StatusBar(id="statusbar")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(LibraryPanel).query_one(DataTable).focus()

    def action_pop(self) -> None:
        self.app.pop_screen()


class JobsListScreen(LibraryScreen):
    """Library filtered to in-flight jobs."""

    leader_chords = {
        "l": ("Library", "/library"),
        "s": ("Settings", "/settings"),
        "h": ("Welcome", "/welcome"),
        "q": ("Quit", "/quit"),
    }

    def __init__(self) -> None:
        super().__init__(status_filter="transcribing")
        self.title = "Jobs"
