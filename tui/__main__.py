"""Entry point: `python -m tui`."""
from __future__ import annotations

import sys


def main() -> int:
    try:
        from .config import parse_args
        from .app import AmicoTUI
        from .server import ServerManager
    except ImportError as e:
        print(
            f"Missing TUI dependency ({e.name or e}).\n"
            "Install with:  pip install -r tui/requirements.txt",
            file=sys.stderr,
        )
        return 1

    cfg = parse_args(sys.argv[1:])
    server = ServerManager(cfg.api_url, spawn=cfg.spawn_server)

    try:
        if not server.ensure_ready():
            print("Failed to reach backend; aborting.", file=sys.stderr)
            return 1
        app = AmicoTUI(cfg=cfg, server=server)
        app.run()
        return 0
    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
