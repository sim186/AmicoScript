"""Clipboard helpers: pyperclip primary, OSC 52 fallback for SSH/remote.

OSC 52 is a terminal escape sequence supported by most modern terminals
(iTerm2, WezTerm, Kitty, Alacritty, recent xterm, Windows Terminal). It
writes to the system clipboard even when no local pyperclip backend is
available — useful when running over SSH.
"""
from __future__ import annotations

import base64
import os
import sys


OSC52_MAX_BYTES = 100_000  # most terminals cap around this


def copy_to_clipboard(text: str) -> bool:
    """Copy text via pyperclip, fall back to OSC 52. Return True on success."""
    if not text:
        return False
    if _try_pyperclip(text):
        return True
    return _try_osc52(text)


def _try_pyperclip(text: str) -> bool:
    try:
        import pyperclip  # type: ignore
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def _try_osc52(text: str) -> bool:
    payload = text.encode("utf-8")
    if len(payload) > OSC52_MAX_BYTES:
        payload = payload[:OSC52_MAX_BYTES]
    b64 = base64.b64encode(payload).decode("ascii")
    seq = f"\x1b]52;c;{b64}\x07"
    # Inside tmux, escape sequences must be wrapped to pass through.
    if os.environ.get("TMUX"):
        seq = f"\x1bPtmux;\x1b{seq}\x1b\\"
    try:
        sys.stdout.write(seq)
        sys.stdout.flush()
        return True
    except Exception:
        return False
