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


# Question words and glue. A question typed at a search box is mostly these,
# and ANDing them finds nothing at all.
_STOPWORDS = frozenset("""
a about after all also am an and any are as at be because been before being
but by can could did do does doing done for from had has have he her hers him
his how i if in into is it its just me more most my no nor not of off on once
only or other our out over own said same she should so some such than that the
their them then there these they this those to too us very was we were what
when where which while who whom why will with would you your
""".split())


# Punctuation clinging to the outside of a word. Kept inside, so "covid-19"
# and "don't" survive intact.
_EDGE_PUNCT_RE = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)


def build_fts_or_match(query: str, max_terms: int = 12) -> str:
    """An FTS5 expression matching *any* content word of *query*.

    ``build_fts_match`` ANDs its terms, which is right for a search box: every
    word narrows the result. It is wrong for a question. "What did we decide
    about pricing?" ANDed matches a passage only if it contains "what" and
    "did" and "we" — so, in practice, nothing. Here the glue is dropped and
    what remains is ORed, leaving FTS5's ranking to sort out which passage has
    the most of it.
    """
    if not query or not query.strip():
        return ""

    terms: list[str] = []
    for match in _TOKEN_RE.finditer(query):
        phrase, word = match.group(1), match.group(2)
        term = (phrase if phrase is not None else word or "").strip()
        if not term or not _HAS_WORD_RE.search(term):
            continue
        # A quoted phrase is kept whole and always survives — the user asked
        # for those exact words.
        if phrase is None:
            # Strip the punctuation a question carries, or 'about?' is not
            # recognised as glue and 'pricing?' is quoted with its question
            # mark still attached.
            term = _EDGE_PUNCT_RE.sub("", term)
            if not term or term.casefold() in _STOPWORDS or len(term) < 2:
                continue
        terms.append(term)

    if not terms:
        # A question made entirely of stopwords ("what is it about?") has
        # nothing to search for; ORing the glue back in would match the whole
        # library equally, which is the same as matching nothing useful.
        return ""

    return " OR ".join(_quote(t) for t in terms[:max_terms])
