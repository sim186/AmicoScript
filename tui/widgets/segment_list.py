"""Scrollable list of transcript segments."""
from __future__ import annotations

from textual.binding import Binding
from textual.widgets import OptionList
from textual.widgets.option_list import Option


def _fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class SegmentList(OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("g,g", "first", show=False),
        Binding("G", "last", show=False),
        Binding("n", "next_speaker", "Next speaker"),
        Binding("N", "prev_speaker", "Prev speaker"),
    ]

    DEFAULT_CSS = """
    SegmentList { height: 1fr; border: tall $panel; }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.segments: list[dict] = []

    def load(self, segments: list[dict]) -> None:
        self.clear_options()
        self.segments = segments or []
        for i, seg in enumerate(self.segments):
            self.add_option(Option(self._format_row(seg), id=str(i)))

    def _format_row(self, seg: dict) -> str:
        ts = _fmt_ts(float(seg.get("start", 0)))
        speaker = seg.get("speaker") or seg.get("speaker_label") or ""
        text = (seg.get("text") or "").strip()
        prefix = f"[dim]{ts}[/dim]"
        if speaker:
            prefix += f"  [bold]{speaker}[/bold]"
        return f"{prefix}  {text}"

    def selected_segment(self) -> dict | None:
        idx = self.highlighted
        if idx is None or not (0 <= idx < len(self.segments)):
            return None
        return self.segments[idx]

    def action_first(self) -> None:
        if self.option_count:
            self.highlighted = 0

    def action_last(self) -> None:
        if self.option_count:
            self.highlighted = self.option_count - 1

    def action_next_speaker(self) -> None:
        idx = (self.highlighted or 0) + 1
        cur = self._speaker_at(self.highlighted or 0)
        while idx < len(self.segments):
            if self._speaker_at(idx) != cur:
                self.highlighted = idx
                return
            idx += 1

    def action_prev_speaker(self) -> None:
        idx = (self.highlighted or 0) - 1
        cur = self._speaker_at(self.highlighted or 0)
        while idx >= 0:
            if self._speaker_at(idx) != cur:
                self.highlighted = idx
                return
            idx -= 1

    def _speaker_at(self, i: int) -> str:
        if 0 <= i < len(self.segments):
            s = self.segments[i]
            return s.get("speaker") or s.get("speaker_label") or ""
        return ""
