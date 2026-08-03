"""Scrollable list of transcript segments."""
from __future__ import annotations

import re

from textual.binding import Binding
from textual.widgets import OptionList
from textual.widgets.option_list import Option


SPEAKER_COLORS = ["#22c55e", "#2dd4bf", "#f59e0b", "#7c79f0", "#ef4444", "#dde1ff"]

_TIMESTAMP_RE = re.compile(r"^\d+$|^\d{1,2}:\d{2}(:\d{2})?$")


def _fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def parse_timestamp(text: str) -> float | None:
    """Parse "83", "1:23" or "1:02:03" into seconds; None if not timestamp-shaped."""
    text = text.strip()
    if not _TIMESTAMP_RE.match(text):
        return None
    parts = [int(p) for p in text.split(":")]
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        m, s = parts
        return float(m * 60 + s)
    h, m, s = parts
    return float(h * 3600 + m * 60 + s)


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
    SegmentList {
        height: 1fr;
        background: #0c0e1a;
        border: none;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.segments: list[dict] = []
        self.speaker_index: dict[str, int] = {}

    def speaker_color(self, name: str) -> str:
        if name not in self.speaker_index:
            self.speaker_index[name] = len(self.speaker_index)
        return SPEAKER_COLORS[self.speaker_index[name] % len(SPEAKER_COLORS)]

    def load(self, segments: list[dict]) -> None:
        self.clear_options()
        self.segments = segments or []
        self.speaker_index.clear()
        for i, seg in enumerate(self.segments):
            self.add_option(Option(self._format_row(seg), id=str(i)))

    def _format_row(self, seg: dict) -> str:
        ts = _fmt_ts(float(seg.get("start", 0)))
        speaker = seg.get("speaker") or seg.get("speaker_label") or ""
        text = (seg.get("text") or "").strip()
        prefix = f"[#6b6e9a]{ts}[/]"
        if speaker:
            color = self.speaker_color(speaker)
            prefix += f"  [{color} b]{speaker}[/]"
        return f"{prefix}  [#dde1ff]{text}[/]"

    def selected_segment(self) -> dict | None:
        idx = self.highlighted
        if idx is None or not (0 <= idx < len(self.segments)):
            return None
        return self.segments[idx]

    def update_segment_text(self, index: int, text: str) -> None:
        """Patch one segment's text in place (after an edit/reset) without
        losing scroll position / highlight the way a full reload would."""
        if not (0 <= index < len(self.segments)):
            return
        self.segments[index]["text"] = text
        self.replace_option_prompt_at_index(index, self._format_row(self.segments[index]))

    def update_segment_speaker(self, index: int, speaker: str) -> None:
        if not (0 <= index < len(self.segments)):
            return
        self.segments[index]["speaker"] = speaker
        self.replace_option_prompt_at_index(index, self._format_row(self.segments[index]))

    def rename_speaker_everywhere(self, old_name: str, new_name: str) -> None:
        for i, seg in enumerate(self.segments):
            if (seg.get("speaker") or seg.get("speaker_label") or "") == old_name:
                seg["speaker"] = new_name
                self.replace_option_prompt_at_index(i, self._format_row(seg))

    def find_first(self, query: str, start_from: int = 0) -> int | None:
        """Case-insensitive substring search over segment text, wrapping
        around from ``start_from``. Returns the matching index, or None."""
        query = query.strip().lower()
        if not query or not self.segments:
            return None
        n = len(self.segments)
        for offset in range(n):
            idx = (start_from + offset) % n
            if query in (self.segments[idx].get("text") or "").lower():
                return idx
        return None

    def jump_to_time(self, seconds: float) -> int | None:
        """Return the index of the segment covering ``seconds`` — or the
        last one starting at or before it if none contains it exactly."""
        if not self.segments:
            return None
        best = 0
        for i, seg in enumerate(self.segments):
            start = float(seg.get("start", 0) or 0)
            end = float(seg.get("end", start) or start)
            if start <= seconds < end:
                return i
            if start <= seconds:
                best = i
        return best

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
