"""Every environment knob the worker reads, in one place.

These were scattered across four modules as bare ``os.environ.get`` calls with
their defaults inlined at the point of use, which made them undiscoverable:
there was no way to answer "what can I tune?" short of grepping for
``AMICO``, and no single place to document what a value means or what happens
when it is nonsense.

Each knob is a function rather than a constant so it is read when it is used.
The worker starts long before some of these matter, and a value frozen at
import time cannot be changed by a test — or by anything else.
"""
from __future__ import annotations

import os

_FALSEY = {"0", "false", "no", "off"}

#: A download is network-bound and touches neither the model nor the GPU, so
#: several can run while one job transcribes. Bounded because each one is also
#: a yt-dlp subprocess and a chunk of disk.
DEFAULT_DOWNLOAD_CONCURRENCY = 2
MAX_DOWNLOAD_CONCURRENCY = 8

DEFAULT_COOKIE_BROWSERS = ["chrome", "firefox", "safari", "edge"]


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in _FALSEY


def resume_interrupted_jobs() -> bool:
    """Whether a restart requeues the work it interrupted (AMICOSCRIPT_RESUME_JOBS).

    Off restores the old fail-fast behaviour, where an interrupted recording
    was simply marked failed.
    """
    return _flag("AMICOSCRIPT_RESUME_JOBS", True)


def download_concurrency() -> int:
    """How many source downloads may run at once (AMICOSCRIPT_DOWNLOAD_CONCURRENCY).

    Clamped rather than validated: a nonsense value should slow the app down
    or speed it up a little, never stop it from starting.
    """
    try:
        value = int(os.environ.get("AMICOSCRIPT_DOWNLOAD_CONCURRENCY", ""))
    except ValueError:
        return DEFAULT_DOWNLOAD_CONCURRENCY
    return max(1, min(value, MAX_DOWNLOAD_CONCURRENCY))


def word_timestamps_default() -> bool:
    """Per-word timing when a job does not ask either way (AMICO_WORD_TIMESTAMPS).

    Off by default: it makes transcription measurably slower and only the
    waveform view uses it.
    """
    return _flag("AMICO_WORD_TIMESTAMPS", False)


def ytdlp_auto_cookies() -> bool:
    """Whether to retry a blocked download with browser cookies (AMICO_YTDLP_AUTO_COOKIES).

    On by default because the common failure — "sign in to confirm you are not
    a bot" — is fixed by cookies the user already has.
    """
    return _flag("AMICO_YTDLP_AUTO_COOKIES", True)


def ytdlp_cookie_browsers() -> list[str]:
    """Browsers to try cookies from, in order (AMICO_YTDLP_COOKIE_BROWSERS)."""
    raw = os.environ.get("AMICO_YTDLP_COOKIE_BROWSERS", "")
    browsers = [b.strip().lower() for b in raw.split(",") if b.strip()]
    return browsers or list(DEFAULT_COOKIE_BROWSERS)
