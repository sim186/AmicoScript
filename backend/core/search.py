"""One query over everything the library knows about a recording.

The search box used to see two things: the words in a transcript, and the
names of files, folders and tags. Everything the LLM produced — the summary
of a two-hour meeting, its action items, its translation — was invisible to
it, even though that is the part a user actually remembers reading.

This module answers a query from five places at once:

===========  ==========================================================
transcript   the spoken words, FTS5 over ``transcript.full_text``
summary      LLM output, FTS5 over ``analysis.result_text``
title        the file name, or the alias the user renamed it to
tag          the name of a tag on the recording, LLM-suggested or not
folder       the name of the folder holding it
===========  ==========================================================

A recording that matches in several places is still **one** result: the
caller gets a single row per recording, showing the strongest match, with
``matched_in`` listing every place the query was found. Hits from different
places therefore have to be ranked against each other, which is what
``_KIND_WEIGHT`` below is for.

The ranking is deliberately dumb and explainable. Ordering within one place
is left to whatever produced it (FTS5's ``rank``, name order for the rest),
and a place never outranks a better one: matching in three places lifts a
result within its band, never above it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from models import Recording
from search_query import build_fts_match
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from utils.logging_utils import get_logger

logger = get_logger("amicoscript.search")

TRANSCRIPT = "transcript"
SUMMARY = "summary"
TITLE = "title"
TAG = "tag"
FOLDER = "folder"

# Highest first: this is both the ranking order and the order the sources are
# consulted in, so the first place to claim a recording is the strongest one
# and gets to supply the snippet.
_KIND_WEIGHT = {TITLE: 5, TRANSCRIPT: 4, SUMMARY: 3, TAG: 2, FOLDER: 1}

# One band per kind. A position inside a band is worth one point and a second
# matching place is worth _MULTI_BONUS, both far below _BAND so neither can
# push a result out of the band its best match earned.
_BAND = 10_000
_MULTI_BONUS = 200

MAX_LIMIT = 100
# How deep each source is read before merging. Deduplication means the merged
# list is shorter than the sum of its parts, so each source is read past what
# the caller asked for — but not without bound, since a one-letter query
# matches most of the library.
_MAX_SCAN = 500


@dataclass
class _Hit:
    """One recording, and every place this query was found in it."""

    recording_id: str
    kind: str
    snippet: str
    score: int
    matched_in: list[str] = field(default_factory=list)


def _like_pattern(query: str) -> str:
    """A LIKE pattern matching *query* literally, wildcards and all."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _rows(session: Session, sql: str, **params) -> list:
    """Run *sql*, or return nothing if the table it needs is not there.

    An FTS index can be missing on a library whose migration was skipped (the
    step logs and moves on rather than refusing to open the database). Search
    degrading to the sources that do exist beats a 500.
    """
    try:
        return session.exec(text(sql), params=params).all()
    except OperationalError as exc:
        logger.warning("Search source unavailable, skipping it: %s", exc)
        return []


# ---------------------------------------------------------------------------
# The five sources
# ---------------------------------------------------------------------------
# Each returns rows of (recording_id, snippet), already in the order it wants
# to be ranked in.


def _transcript_hits(session: Session, expr: str, scan: int) -> list:
    if not expr:
        return []
    return _rows(
        session,
        """
        SELECT t.recording_id AS recording_id,
               snippet(transcript_fts, 0, '<mark>', '</mark>', '…', 20) AS snippet
        FROM transcript_fts
        JOIN transcript t ON transcript_fts.rowid = t.rowid
        WHERE transcript_fts MATCH :q
        ORDER BY rank
        LIMIT :scan
        """,
        q=expr,
        scan=scan,
    )


def _summary_hits(session: Session, expr: str, scan: int) -> list:
    """Matches in LLM output — summaries, action items, translations.

    Rows whose analysis has not answered yet hold an empty ``result_text`` and
    match nothing, so no filter on status is needed here.
    """
    if not expr:
        return []
    return _rows(
        session,
        """
        SELECT a.recording_id AS recording_id,
               snippet(analysis_fts, 0, '<mark>', '</mark>', '…', 20) AS snippet
        FROM analysis_fts
        JOIN analysis a ON analysis_fts.rowid = a.rowid
        WHERE analysis_fts MATCH :q
        ORDER BY rank
        LIMIT :scan
        """,
        q=expr,
        scan=scan,
    )


def _title_hits(session: Session, like: str, scan: int) -> list:
    """File names, and the aliases users rename them to.

    The alias is what the library shows once it is set, so searching the
    original file name alone would miss the only name the user has seen.
    """
    return _rows(
        session,
        r"""
        SELECT r.id AS recording_id,
               CASE WHEN r.alias LIKE :ql ESCAPE '\' THEN r.alias ELSE r.filename END AS snippet
        FROM recording r
        WHERE r.filename LIKE :ql ESCAPE '\' OR r.alias LIKE :ql ESCAPE '\'
        ORDER BY r.created_at DESC
        LIMIT :scan
        """,
        ql=like,
        scan=scan,
    )


def _tag_hits(session: Session, like: str, scan: int) -> list:
    return _rows(
        session,
        r"""
        SELECT rt.recording_id AS recording_id, t.name AS snippet
        FROM tag t
        JOIN recordingtag rt ON rt.tag_id = t.id
        WHERE t.name LIKE :ql ESCAPE '\'
        ORDER BY t.name
        LIMIT :scan
        """,
        ql=like,
        scan=scan,
    )


def _folder_hits(session: Session, like: str, scan: int) -> list:
    return _rows(
        session,
        r"""
        SELECT r.id AS recording_id, f.name AS snippet
        FROM folder f
        JOIN recording r ON r.folder_id = f.id
        WHERE f.name LIKE :ql ESCAPE '\'
        ORDER BY f.name
        LIMIT :scan
        """,
        ql=like,
        scan=scan,
    )


def _text_like_hits(session: Session, query: str, scan: int, table: str, column: str) -> list:
    """Substring fallback for a text source whose FTS expression is empty.

    ``build_fts_match`` returns "" for a query FTS5 cannot express — one made
    only of punctuation, say. LIKE has no such trouble, and finding ":-)" by
    scanning is better than the search box going blank on it. The snippet is
    cut around the match by hand, since there is no FTS index here to ask.
    """
    return _rows(
        session,
        rf"""
        SELECT s.recording_id AS recording_id,
               substr(s.{column}, MAX(1, INSTR(LOWER(s.{column}), LOWER(:needle)) - 40), 120) AS snippet
        FROM {table} s
        WHERE s.{column} LIKE :ql ESCAPE '\'
        LIMIT :scan
        """,
        ql=_like_pattern(query),
        needle=query,
        scan=scan,
    )


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def _collect(sources: list[tuple[str, list]]) -> list[_Hit]:
    """Fold per-source rows into one hit per recording, ranked.

    *sources* arrives in ``_KIND_WEIGHT`` order, so the first source to see a
    recording is its strongest match and the one that supplies the snippet.
    """
    hits: dict[str, _Hit] = {}
    for kind, rows in sources:
        for position, row in enumerate(rows):
            existing = hits.get(row.recording_id)
            if existing is None:
                hits[row.recording_id] = _Hit(
                    recording_id=row.recording_id,
                    kind=kind,
                    snippet=row.snippet or "",
                    score=_KIND_WEIGHT[kind] * _BAND - position,
                    matched_in=[kind],
                )
            elif kind not in existing.matched_in:
                existing.matched_in.append(kind)
                existing.score += _MULTI_BONUS

    return sorted(hits.values(), key=lambda h: h.score, reverse=True)


def search_library(
    session: Session, query: str, limit: int = 20, offset: int = 0
) -> list[dict]:
    """Search transcripts, LLM output and metadata for *query*.

    Returns one row per recording, strongest match first. ``snippet`` may
    contain ``<mark>`` around the matching words and is otherwise raw library
    text, so callers must treat it as untrusted and escape it before display.
    """
    query = (query or "").strip()
    if not query:
        return []

    safe_limit = max(1, min(limit, MAX_LIMIT))
    safe_offset = max(0, offset)
    scan = min(safe_limit + safe_offset, _MAX_SCAN)

    # Never hand raw user input to MATCH — see backend/search_query.py.
    expr = build_fts_match(query)
    like = _like_pattern(query)

    if expr:
        transcripts = _transcript_hits(session, expr, scan)
        summaries = _summary_hits(session, expr, scan)
    else:
        transcripts = _text_like_hits(session, query, scan, "transcript", "full_text")
        summaries = _text_like_hits(session, query, scan, "analysis", "result_text")

    ranked = _collect([
        (TITLE, _title_hits(session, like, scan)),
        (TRANSCRIPT, transcripts),
        (SUMMARY, summaries),
        (TAG, _tag_hits(session, like, scan)),
        (FOLDER, _folder_hits(session, like, scan)),
    ])

    results = []
    for hit in ranked[safe_offset : safe_offset + safe_limit]:
        recording = session.get(Recording, hit.recording_id)
        if not recording:
            continue
        results.append(
            {
                "recording_id": hit.recording_id,
                "filename": recording.filename,
                "alias": recording.alias,
                "duration": recording.duration,
                "status": recording.status,
                "kind": hit.kind,
                "matched_in": hit.matched_in,
                "snippet": _label(hit),
            }
        )
    return results


# The snippet of a metadata match is just a name, which says nothing on its
# own about *which* name it is. The labels predate this module and the TUI
# search screen still reads them, so they are kept exactly as they were.
_LABELS = {TITLE: "Title: ", TAG: "Tag: ", FOLDER: "Folder: "}


def _label(hit: _Hit) -> str:
    return _LABELS.get(hit.kind, "") + hit.snippet
