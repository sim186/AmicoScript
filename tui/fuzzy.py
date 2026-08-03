"""Tiny subsequence fuzzy matcher with scoring.

score_match(query, text) -> int | None
    Returns a score (higher = better) or None if no subsequence match.
    Bonuses: prefix start, consecutive chars, word-boundary hits.

rank(query, items, key=str) -> list[(score, item)]
    Filter+rank a list. Empty query returns items in original order with score 0.
"""
from __future__ import annotations

from typing import Callable, Iterable, TypeVar

T = TypeVar("T")

# Tunable weights
_PREFIX_BONUS = 60
_BOUNDARY_BONUS = 25
_CONSECUTIVE_BONUS = 15
_BASE_HIT = 5
_LENGTH_PENALTY = 0.5  # subtracted per char of text length


def score_match(query: str, text: str) -> int | None:
    if not query:
        return 0
    q = query.lower()
    t = text.lower()
    qi = 0
    score = 0
    last_idx = -2
    for i, ch in enumerate(t):
        if qi >= len(q):
            break
        if ch == q[qi]:
            hit = _BASE_HIT
            if i == 0 and qi == 0:
                hit += _PREFIX_BONUS
            if i > 0 and not t[i - 1].isalnum():
                hit += _BOUNDARY_BONUS
            if i == last_idx + 1:
                hit += _CONSECUTIVE_BONUS
            score += hit
            last_idx = i
            qi += 1
    if qi < len(q):
        return None
    score -= int(len(t) * _LENGTH_PENALTY)
    return score


def rank(
    query: str,
    items: Iterable[T],
    key: Callable[[T], str] = str,
) -> list[tuple[int, T]]:
    out: list[tuple[int, T]] = []
    if not query:
        return [(0, it) for it in items]
    for it in items:
        s = score_match(query, key(it))
        if s is not None:
            out.append((s, it))
    out.sort(key=lambda x: x[0], reverse=True)
    return out
