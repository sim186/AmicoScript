"""Inline text progress bar widget for jobs."""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget


class JobProgress(Widget):
    DEFAULT_CSS = """
    JobProgress { height: 1; padding: 0 1; }
    """

    progress: reactive[float] = reactive(0.0)
    label: reactive[str] = reactive("")

    def render(self) -> str:
        width = max(10, self.size.width - 20)
        filled = int(max(0.0, min(1.0, self.progress)) * width)
        bar = "█" * filled + "░" * (width - filled)
        pct = int(self.progress * 100)
        return f"{bar} {pct:3d}%  {self.label}"
