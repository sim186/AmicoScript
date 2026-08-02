"""The frontend must render with no network at all.

A CDN <script> that creeps back in breaks offline use silently — the app still
loads, it just comes up unstyled or without the waveform — so guard it here.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
INDEX = FRONTEND / "index.html"
VENDOR = FRONTEND / "vendor"

EXTERNAL_SRC = re.compile(
    r"""<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*["'](?P<url>[^"']+)["']""",
    re.I,
)


def _referenced_urls():
    return [m.group("url") for m in EXTERNAL_SRC.finditer(INDEX.read_text(encoding="utf-8"))]


def test_index_loads_no_remote_assets():
    remote = [u for u in _referenced_urls() if re.match(r"(https?:)?//", u, re.I)]
    assert remote == [], f"frontend/index.html must not load remote assets: {remote}"


def test_referenced_local_assets_exist():
    missing = [
        u
        for u in _referenced_urls()
        if not re.match(r"(https?:)?//|data:", u, re.I)
        and not (FRONTEND / u.split("?", 1)[0].lstrip("/")).exists()
    ]
    assert missing == [], f"frontend/index.html references missing files: {missing}"


@pytest.mark.parametrize(
    "name",
    [
        "tailwind-3.4.16.min.js",
        "marked-15.0.12.min.js",
        "wavesurfer-7.12.11.min.js",
        "inter.css",
    ],
)
def test_vendored_asset_present_and_nonempty(name):
    asset = VENDOR / name
    assert asset.is_file(), f"missing vendored asset {name}"
    assert asset.stat().st_size > 0, f"empty vendored asset {name}"


def test_vendored_font_css_is_fully_localised():
    css = (VENDOR / "inter.css").read_text(encoding="utf-8")
    assert "gstatic" not in css, "inter.css still points at fonts.gstatic.com"
    refs = set(re.findall(r"url\((?!data:)([^)]+)\)", css))
    assert refs, "inter.css declares no font files"
    missing = sorted(r for r in refs if not (VENDOR / r.strip("\"'")).exists())
    assert missing == [], f"inter.css references missing font files: {missing}"
