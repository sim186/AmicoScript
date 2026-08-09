"""The published API reference is generated, so it has to stay generated.

website/api.html renders website/openapi.json, which scripts/generate_openapi.py
produces from the FastAPI routes. The reference the site publishes is only as
honest as that file, and the failure mode is silent: add a route, forget to
regenerate, and the docs quietly describe a build nobody is running. That is
exactly how the hand-written endpoint list this replaced ended up wrong.
"""

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBSITE = ROOT / "website"
SPEC = WEBSITE / "openapi.json"
GENERATOR = ROOT / "scripts" / "generate_openapi.py"


def _generator():
    """Load scripts/generate_openapi.py as a module.

    In-process rather than as a subprocess: conftest points HOME at a sandbox,
    and a child interpreter started with that HOME loses the user site-packages
    the dependencies may live in.
    """
    spec = importlib.util.spec_from_file_location("generate_openapi", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_schema_matches_the_routes():
    generator = _generator()
    expected = generator.render(generator.build_schema())
    assert SPEC.exists(), "website/openapi.json is missing — run scripts/generate_openapi.py"
    assert SPEC.read_text(encoding="utf-8") == expected, (
        "website/openapi.json is out of date — run: python scripts/generate_openapi.py"
    )


def test_every_operation_is_tagged():
    """An untagged route lands in a nameless bucket at the bottom of the page."""
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    declared = {tag["name"] for tag in spec.get("tags", [])}
    untagged, unknown = [], []
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            tags = operation.get("tags", [])
            if not tags:
                untagged.append(f"{method.upper()} {path}")
            unknown += [t for t in tags if t not in declared]
    assert not untagged, f"routes with no tag: {untagged}"
    assert not unknown, f"tags with no description in OPENAPI_TAGS: {sorted(set(unknown))}"


def test_api_page_assets_are_local():
    """Redoc is vendored so the reference renders offline and survives a CDN
    outage — see scripts/update_redoc.py."""
    html = (WEBSITE / "api.html").read_text(encoding="utf-8")
    scripts = re.findall(r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", html, re.I)
    # The GoatCounter beacon is deliberately remote; it renders nothing.
    remote = [s for s in scripts if re.match(r"(https?:)?//", s, re.I) and "gc.zgo.at" not in s]
    assert remote == [], f"website/api.html must not load remote scripts: {remote}"

    local = [s for s in scripts if not re.match(r"(https?:)?//|data:", s, re.I)]
    missing = [s for s in local if not (WEBSITE / s.split("?", 1)[0].lstrip("/")).exists()]
    assert missing == [], f"website/api.html references missing files: {missing}"
