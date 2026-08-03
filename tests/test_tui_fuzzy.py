"""Tests for the TUI palette fuzzy matcher."""
from __future__ import annotations

from tui.fuzzy import rank, score_match


def test_exact_prefix_outranks_substring():
    assert score_match("lib", "library") > score_match("lib", "available libs")


def test_no_match_returns_none():
    assert score_match("xyz", "library") is None


def test_consecutive_chars_boosted():
    """Consecutive subsequence beats sparse non-boundary subsequence."""
    consecutive = score_match("abc", "abcdef")
    spread = score_match("abc", "axxxbxxxcxxx")
    assert consecutive is not None and spread is not None
    assert consecutive > spread


def test_word_boundary_boost():
    """Acronym-style matches across word boundaries rank well."""
    boundary = score_match("abc", "a_b_c_d")
    nonboundary = score_match("abc", "azzbzzczz")
    assert boundary is not None and nonboundary is not None
    assert boundary > nonboundary


def test_empty_query_preserves_order():
    items = ["one", "two", "three"]
    out = rank("", items)
    assert [it for _s, it in out] == items


def test_rank_sorts_desc():
    items = ["report.md", "rapid.md", "readme.md"]
    out = rank("rea", items)
    assert out[0][1] == "readme.md"
    # Non-subsequence matches dropped.
    out2 = rank("zzz", items)
    assert out2 == []
