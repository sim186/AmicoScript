#!/usr/bin/env python3
"""Refresh the vendored Redoc bundle used by website/api.html.

The API reference renders with a copy of Redoc committed under website/assets/
instead of a CDN script: the page then works offline, and a CDN outage or a
silently shipped breaking release cannot take the published reference down.
The cost is that upgrades are manual — this script is the upgrade.

    python scripts/update_redoc.py            # pin currently recorded below
    python scripts/update_redoc.py 2.5.4      # move to a new version

Remember to bump the version in the comment in website/api.html to match.
"""
from __future__ import annotations

import io
import sys
import tarfile
import urllib.request
from pathlib import Path

REDOC_VERSION = "2.5.3"
ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "website" / "assets"
FILES = ("redoc.standalone.js", "redoc.standalone.js.LICENSE.txt")


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else REDOC_VERSION
    url = f"https://registry.npmjs.org/redoc/-/redoc-{version}.tgz"
    print(f"Downloading {url} …")
    with urllib.request.urlopen(url, timeout=120) as response:
        payload = response.read()

    ASSETS.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        for name in FILES:
            member = tar.extractfile(f"package/bundles/{name}")
            if member is None:
                print(f"redoc {version} has no bundles/{name}", file=sys.stderr)
                return 1
            target = ASSETS / name
            target.write_bytes(member.read())
            print(f"Wrote {target.relative_to(ROOT)} ({target.stat().st_size:,} bytes)")

    print(f"Redoc {version} vendored. Update the version comment in website/api.html.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
