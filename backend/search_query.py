"""Turn a user's search box input into a safe FTS5 MATCH expression.

The search endpoint used to pass the raw query straight into
``transcript_fts MATCH :q``. FTS5 reads that string as a *query language*, not
as text, so perfectly ordinary searches were syntax errors:

    hello "world      → unterminated string
    covid-19          → column filter / NOT expression
    AND               → parse error
    C++               → parse error

Every one of those raised OperationalError, and the endpoint quietly fell back
to a LIKE scan — so the user got different, worse results with no indication
why.

Here each term is wrapped in an FTS5 string literal, which makes it plain text
no matter what it contains, and terms are ANDed together. Double-quoted groups
in the input are preserved as phrase searches, because that is what a user
typing "climate policy" means.
"""
from __future__ import annotations

import re

# Split on whitespace, keeping "quoted phrases" as single units.
_TOKEN_RE = re.compile(r'"([^"]*)"|(\S+)')

# A term must contain at least one alphanumeric character; FTS5 tokenizes
# punctuation away, and an empty string literal ("") is itself a syntax error.
_HAS_WORD_RE = re.compile(r"\w", re.UNICODE)


def _quote(term: str) -> str:
    """Wrap *term* in an FTS5 string literal, doubling embedded quotes."""
    return '"' + term.replace('"', '""') + '"'


def build_fts_match(query: str, prefix_last: bool = True) -> str:
    """Return an FTS5 MATCH expression for *query*, or "" if it has no terms.

    ``prefix_last`` makes the final term a prefix search so results narrow as
    the user types ("meet" matches "meeting"). An empty return means the caller
    should skip FTS entirely rather than run a broken query.
    """
    if not query or not query.strip():
        return ""

    terms: list[str] = []
    for match in _TOKEN_RE.finditer(query):
        phrase, word = match.group(1), match.group(2)
        term = (phrase if phrase is not None else word or "").strip()
        if term and _HAS_WORD_RE.search(term):
            terms.append(term)

    if not terms:
        return ""

    quoted = [_quote(t) for t in terms]

    # Only the final term gets prefix treatment, and only when the user did not
    # quote it — an explicit "phrase" is a request for that exact wording.
    if prefix_last:
        last_is_phrase = query.rstrip().endswith('"')
        if not last_is_phrase and _HAS_WORD_RE.search(terms[-1][-1:]):
            quoted[-1] = quoted[-1] + "*"

    return " AND ".join(quoted)
