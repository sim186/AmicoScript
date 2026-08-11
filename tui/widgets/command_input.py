"""Free-text palette input. No forced ``/`` prefix.

Leading ``/`` is allowed and acts as a hint to the palette to filter
to commands only — see ``tui/palette.py``.
"""
from __future__ import annotations

from textual.widgets import Input


class CommandInput(Input):
    DEFAULT_CSS = """
    CommandInput {
        border: tall $accent;
        height: 3;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("placeholder", "search · type / for commands only")
        super().__init__(*args, **kwargs)
