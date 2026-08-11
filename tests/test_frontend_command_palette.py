"""The command palette is wiring, and wiring is what silently rots.

Every command reaches into the markup by id, and the palette module reaches
into its own overlay the same way. Nothing checks any of that at load time —
there is no build step — so a renamed element leaves a palette entry that opens
the palette, matches the query, and then does nothing at all. These tests read
the ids out of the modules and look for them in index.html.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend" / "index.html"
JS = ROOT / "frontend" / "js"

GET_BY_ID_RE = re.compile(r"""getElementById\(\s*['"]([\w-]+)['"]""")
EL_HELPER_RE = re.compile(r"""\bel\(\s*['"]([\w-]+)['"]""")
ID_ATTR_RE = re.compile(r"""\bid\s*=\s*["']([\w-]+)["']""")


def _markup_ids() -> set[str]:
    return set(ID_ATTR_RE.findall(INDEX.read_text(encoding="utf-8")))


@pytest.mark.parametrize("module", ["commands.js", "command-palette.js"])
def test_every_element_the_palette_reaches_for_exists(module):
    source = (JS / module).read_text(encoding="utf-8")
    wanted = set(GET_BY_ID_RE.findall(source)) | set(EL_HELPER_RE.findall(source))
    missing = sorted(wanted - _markup_ids())
    assert wanted, f"{module} no longer looks anything up by id — has it moved?"
    assert missing == [], f"{module} looks for elements index.html does not have: {missing}"


def test_the_palette_overlay_is_in_the_markup():
    """Without these the palette is unreachable, and nothing else would notice."""
    required = {
        "palette-overlay",
        "palette-input",
        "palette-results",
        "palette-trigger",
        "palette-trigger-key",
        "palette-status",
    }
    assert required <= _markup_ids()


def test_the_header_offers_the_palette_rather_than_the_old_search_box():
    html = INDEX.read_text(encoding="utf-8")
    assert "global-search-input" not in html, "the replaced search box is back"
    assert "global-search-dropdown" not in html


def test_the_prefixes_the_palette_documents_are_the_ones_it_implements():
    """The footer teaches / @ #; a divergence there is a lie in the UI."""
    palette = (JS / "command-palette.js").read_text(encoding="utf-8")
    modes = re.search(r"const MODES = \{(.*?)\};", palette, re.S)
    assert modes, "the palette no longer declares its prefixes in MODES"
    implemented = set(re.findall(r"'(.)':", modes.group(1)))

    footer = re.search(r'<div id="palette-footer">(.*?)</div>', INDEX.read_text(encoding="utf-8"), re.S)
    assert footer, "the palette footer is gone"
    documented = set(re.findall(r"<kbd>(.)</kbd>", footer.group(1))) & set("/@#")

    assert implemented == documented


def test_snippets_are_escaped_before_they_are_marked():
    """A transcript is user text and reaches the palette as HTML.

    /api/search wraps matches in <mark>, so the snippet cannot simply be
    assigned to innerHTML. It is escaped whole and only those two tags are put
    back; losing that step turns any transcript into a script tag.
    """
    palette = (JS / "command-palette.js").read_text(encoding="utf-8")
    marked = re.search(r"function markedSnippet\(text\) \{(.*?)\n\}", palette, re.S)
    assert marked, "markedSnippet is gone — how are snippets rendered now?"
    body = marked.group(1)
    assert "escHtml(" in body
    assert "&lt;mark&gt;" in body, "the escape step was skipped"
