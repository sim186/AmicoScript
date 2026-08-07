"""Distribution shim for the `amicoscript` wheel.

Only this file and `cli.py` live in the repo. The wheel is assembled by
hatchling (see `pyproject.toml`), which copies `backend/`, `frontend/`,
`scripts/meeting_watcher/` and `run.py` in beside them so the installed package
has the same shape the repo root does. That is the whole trick: `run.py` and
`backend/main.py` already locate their siblings relative to `__file__`, so
neither needs a packaging-specific code path.
"""
from __future__ import annotations

__all__ = ["__version__", "main"]


def _detect_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("amicoscript")
    except PackageNotFoundError:
        # Running from a source checkout that was never installed.
        from pathlib import Path

        try:
            return (Path(__file__).resolve().parents[1] / "VERSION").read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            return "0.0.0"


__version__ = _detect_version()


def main() -> int:
    from .cli import main as _main

    return _main()
