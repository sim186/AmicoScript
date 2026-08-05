"""Tests for search query handling.

The previous version of this file re-implemented the escaping expression inside
the test body and asserted against its own copy, so it would have passed even
if the escaping had been deleted from the route. These call the real code.
"""
import sqlite3

import pytest

from search_query import build_fts_match

pytestmark = pytest.mark.usefixtures("no_auth")


@pytest.fixture()
def fts_db():
    """A real FTS5 index, so a bad MATCH expression fails the test loudly."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t(full_text TEXT)")
    con.execute(
        "CREATE VIRTUAL TABLE fts USING fts5(full_text, content='t', content_rowid='rowid')"
    )
    documents = [
        "hello world meeting notes",
        "covid-19 policy update",
        "the C++ compiler talk",
        "climate policy debate",
        "policy about the climate in general",
    ]
    for text in documents:
        cur = con.execute("INSERT INTO t VALUES (?)", (text,))
        con.execute("INSERT INTO fts(rowid, full_text) VALUES (?,?)", (cur.lastrowid, text))
    yield con
    con.close()


def _search(con, query: str) -> list[int]:
    expr = build_fts_match(query)
    if not expr:
        return []
    return [row[0] for row in con.execute("SELECT rowid FROM fts WHERE fts MATCH ?", (expr,))]


# --- inputs that used to raise OperationalError -----------------------------


@pytest.mark.parametrize(
    "query",
    [
        'hello "world',      # unterminated string literal
        "covid-19",          # read as a NOT expression
        "AND",               # bare operator
        "OR",
        "NOT",
        "C++",               # parse error
        "NEAR(a b)",         # function syntax
        "column:value",      # column filter
        "a*b",
        "(unbalanced",
        '"',
        "x' OR '1'='1",
        "^caret",
        "{brace}",
    ],
)
def test_hostile_queries_produce_a_runnable_expression(fts_db, query):
    expr = build_fts_match(query)
    if expr:
        fts_db.execute("SELECT rowid FROM fts WHERE fts MATCH ?", (expr,)).fetchall()


# --- search actually works --------------------------------------------------


def test_a_plain_word_matches(fts_db):
    assert _search(fts_db, "hello") == [1]


def test_terms_are_combined_with_and(fts_db):
    # "climate" alone matches two documents; adding "debate" narrows it to one.
    assert len(_search(fts_db, "climate")) == 2
    assert _search(fts_db, "climate debate") == [4]


def test_a_quoted_phrase_requires_adjacency(fts_db):
    assert _search(fts_db, '"climate policy"') == [4]


def test_the_last_term_is_a_prefix_search(fts_db):
    """So results narrow while the user is still typing."""
    assert _search(fts_db, "meet") == [1]
    assert build_fts_match("meet").endswith("*")


def test_a_quoted_final_term_is_not_prefixed(fts_db):
    assert not build_fts_match('"meeting"').endswith("*")
    assert _search(fts_db, '"meet"') == []


def test_punctuation_inside_a_term_is_literal(fts_db):
    assert _search(fts_db, "covid-19") == [2]
    assert _search(fts_db, "C++") == [3]


def test_injection_attempt_finds_nothing_rather_than_erroring(fts_db):
    assert _search(fts_db, "x' OR '1'='1") == []


# --- empty and degenerate input --------------------------------------------


@pytest.mark.parametrize("query", ["", "   ", "---", "!!!", '""'])
def test_queries_with_no_searchable_terms_return_empty(query):
    assert build_fts_match(query) == ""


def test_quotes_inside_a_term_are_doubled():
    """FTS5 escapes a quote inside a string literal by doubling it."""
    assert build_fts_match('say "hi', prefix_last=False) == '"say" AND """hi"'


def test_prefix_can_be_disabled():
    assert build_fts_match("meet", prefix_last=False) == '"meet"'


# --- LIKE escaping in the metadata half of the query ------------------------


def test_route_escapes_like_wildcards(client, make_recording):
    """A filename containing % or _ must not turn into a wildcard match."""
    make_recording(filename="file_001.mp3")
    make_recording(filename="fileX001.mp3")

    rows = client.get("/api/search", params={"q": "file_001"}).json()
    assert [r["filename"] for r in rows] == ["file_001.mp3"]


def test_route_clamps_the_limit(client, make_recording):
    for i in range(3):
        make_recording(filename=f"clamped{i}.mp3")

    assert len(client.get("/api/search", params={"q": "clamped", "limit": -1}).json()) == 1
    assert len(client.get("/api/search", params={"q": "clamped", "limit": 999}).json()) == 3
