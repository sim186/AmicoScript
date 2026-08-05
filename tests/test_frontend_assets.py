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


# ---------------------------------------------------------------------------
# ES module structure
# ---------------------------------------------------------------------------
# The UI is a set of plain ES modules under frontend/js/, loaded natively — no
# bundler, so nothing checks the wiring at build time. These tests do.

JS_DIR = FRONTEND / "js"
IMPORT_RE = re.compile(r"""^\s*import\s+(?:[^'"]*\bfrom\s+)?["'](?P<path>\.[^"']+)["']""", re.M)
# Identifiers called from inline on* attributes in the markup. The negative
# lookbehind skips method calls (obj.method()), which resolve on the object.
INLINE_HANDLER_RE = re.compile(r"""\bon\w+\s*=\s*"([^"]*)\"""")
IDENT_CALL_RE = re.compile(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(")
JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "typeof", "new", "function"}


def _module_files():
    return sorted(JS_DIR.glob("*.js"))


def test_index_uses_the_module_entry_point():
    html = INDEX.read_text(encoding="utf-8")
    assert '<script type="module" src="js/app.js">' in html

    # The 4,800-line inline block is gone and must not come back. A short
    # inline <script> is still fine (the Tailwind theme config is one).
    for body in re.findall(r"<script>(.*?)</script>", html, re.S):
        assert body.count("\n") < 40, "a large inline application script reappeared in index.html"


def test_every_module_import_resolves():
    missing = []
    for path in _module_files():
        for match in IMPORT_RE.finditer(path.read_text(encoding="utf-8")):
            target = (path.parent / match.group("path")).resolve()
            if not target.exists():
                missing.append(f"{path.name} -> {match.group('path')}")
    assert missing == [], f"unresolved module imports: {missing}"


def test_every_module_is_reachable_from_the_entry_point():
    """An orphaned module is dead code that still looks live."""
    entry = JS_DIR / "app.js"
    reachable, queue = {entry.name}, [entry]
    while queue:
        current = queue.pop()
        for match in IMPORT_RE.finditer(current.read_text(encoding="utf-8")):
            target = (current.parent / match.group("path")).resolve()
            if target.name not in reachable and target.exists():
                reachable.add(target.name)
                queue.append(target)

    orphans = sorted(p.name for p in _module_files() if p.name not in reachable)
    assert orphans == [], f"modules not reachable from app.js: {orphans}"


def test_inline_handlers_have_a_global_to_call():
    """Inline on* attributes resolve against window, which module scope is not.

    app.js republishes the handler functions; if one is renamed or dropped, the
    button silently does nothing at runtime. This catches it at test time.
    """
    html = INDEX.read_text(encoding="utf-8")
    entry = (JS_DIR / "app.js").read_text(encoding="utf-8")

    published = set()
    assign = re.search(r"Object\.assign\(window,\s*\{(.*?)\n\}\);", entry, re.S)
    assert assign, "app.js no longer republishes handlers onto window"
    for chunk in assign.group(1).split(","):
        name = chunk.strip()
        if name and name.isidentifier():
            published.add(name)

    # Anything the markup calls must either be published or be a browser builtin.
    builtins = {"event", "window", "document", "confirm", "alert", "parseInt", "Number", "String"}
    called = set()
    for attr in INLINE_HANDLER_RE.findall(html):
        called.update(IDENT_CALL_RE.findall(attr))

    unresolved = sorted(
        name for name in called
        if name not in published
        and name not in builtins
        and name not in JS_KEYWORDS
        and not name.startswith("_")
    )
    assert called, "no inline handlers found — has the markup changed shape?"
    assert unresolved == [], f"inline handlers call names no module publishes: {unresolved}"


def test_modules_are_smaller_than_the_file_they_replaced():
    """Guard against the whole UI drifting back into one file."""
    biggest = max((p.stat().st_size, p.name) for p in _module_files())
    assert biggest[0] < 60_000, f"{biggest[1]} is growing back into a monolith"
