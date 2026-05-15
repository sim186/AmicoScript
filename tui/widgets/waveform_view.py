"""Multi-row animated waveform widget."""
from __future__ import annotations

from rich.console import RenderableType
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget


# Web-UI palette gradient (low → high amplitude).
GRADIENT = ["#60a5fa", "#a78bfa", "#6c63ff", "#db2777", "#f472b6"]
PLAYED_GRADIENT = ["#475569", "#64748b", "#94a3b8"]


def _color_for(level: float, played: bool) -> str:
    palette = PLAYED_GRADIENT if played else GRADIENT
    idx = min(len(palette) - 1, max(0, int(level * len(palette))))
    return palette[idx]


class WaveformView(Widget):
    """Renders levels as a vertically-stacked block waveform with animated cursor."""

    DEFAULT_CSS = """
    WaveformView {
        height: 7;
        padding: 0 1;
        background: $panel;
    }
    """

    levels: reactive[list[float]] = reactive(list, layout=True)
    position: reactive[float] = reactive(0.0)
    rows: int = 5

    def render(self) -> RenderableType:
        if not self.levels:
            return Text("(no waveform — playback will still work)", style="dim")
        width = len(self.levels)
        cursor_col = max(0, min(width - 1, int(self.position * width)))
        text = Text()
        for row in range(self.rows, 0, -1):
            threshold = row / self.rows
            half = threshold - 1 / (2 * self.rows)
            for col, level in enumerate(self.levels):
                played = col <= cursor_col
                if level >= threshold:
                    char = "█"
                elif level >= half:
                    char = "▄"
                else:
                    char = " "
                style = Style(color=_color_for(level, played))
                if col == cursor_col:
                    style = style + Style(bgcolor="#ede9ff", color="#0f172a")
                text.append(char, style=style)
            text.append("\n")
        return text
