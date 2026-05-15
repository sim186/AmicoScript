"""TUI configuration: CLI flags, env vars, defaults."""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass


DEFAULT_API_URL = "http://127.0.0.1:8002"


@dataclass
class Config:
    api_url: str
    spawn_server: bool
    debug: bool


def parse_args(argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(
        prog="amicoscript-tui",
        description="Terminal interface for AmicoScript transcription.",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("AMICOSCRIPT_API_URL", DEFAULT_API_URL),
        help="Backend API base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        default=os.environ.get("AMICOSCRIPT_TUI_NO_SERVER", "0") == "1",
        help="Do not spawn a local server; attach to an already-running one.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )
    ns = parser.parse_args(argv)
    return Config(
        api_url=ns.api_url.rstrip("/"),
        spawn_server=not ns.no_server,
        debug=ns.debug,
    )
