#!/usr/bin/env python3
"""Write the FastAPI app's OpenAPI schema to website/openapi.json.

The public API reference (website/api.html) is a Redoc page that renders this
file, so the docs are generated from the routes themselves rather than
hand-maintained — the hand-written endpoint list that used to live in
website/docs.html had already drifted from the code.

Run it after changing a route:

    python scripts/generate_openapi.py

`--check` regenerates in memory and fails if the committed file is stale, which
is what the test suite and CI use.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
OUTPUT = ROOT / "website" / "openapi.json"


def build_schema() -> dict:
    """Import the app and return its OpenAPI document.

    Importing backend/main.py is enough: FastAPI derives the schema from the
    routers at that point, and no startup hook runs, so nothing here touches the
    database, the storage directory, or the network.
    """
    # backend modules import each other by bare name (`import state`), the same
    # way they do at runtime under uvicorn.
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    # backend/config.py resolves the storage root from $HOME at import time.
    # Empty GITHUB_OWNER short-circuits the release poller for good measure —
    # neither is reached without startup, but a generator that can only be run
    # offline would be a poor thing to put in CI.
    os.environ.setdefault("GITHUB_OWNER", "")
    os.environ.setdefault("AMICOSCRIPT_EMBEDDED_WATCHER", "off")

    import main  # noqa: E402 — path setup above has to happen first

    return main.app.openapi()


def render(schema: dict) -> str:
    """Stable JSON: sorted keys and a trailing newline, so the committed file
    only changes when the API does."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if website/openapi.json is missing or out of date",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT,
        help=f"where to write the schema (default: {OUTPUT.relative_to(ROOT)})",
    )
    args = parser.parse_args()

    rendered = render(build_schema())

    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != rendered:
            print(
                f"{args.output} is out of date — run: python scripts/generate_openapi.py",
                file=sys.stderr,
            )
            return 1
        print(f"{args.output} is up to date.")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    paths = len(json.loads(rendered).get("paths", {}))
    print(f"Wrote {args.output} ({paths} paths).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
