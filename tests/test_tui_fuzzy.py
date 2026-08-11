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


# --- library formatting -----------------------------------------------------


def test_short_durations_keep_their_seconds():
    """A 22-second clip used to render as "0h 00m", which reads as empty."""
    from tui.screens.library import _fmt_duration

    assert _fmt_duration(22) == "0:22"
    assert _fmt_duration(95) == "1:35"
    assert _fmt_duration(3661) == "1h 01m"
    assert _fmt_duration(0) == "--"
    assert _fmt_duration(None) == "--"


def test_the_status_map_covers_the_new_states():
    from tui.screens.library import RETRYABLE, STATUS_DISPLAY

    for status in ("interrupted", "cancelled", "downloading", "loading_model", "translating"):
        assert status in STATUS_DISPLAY, status
    assert "interrupted" in RETRYABLE
    assert "transcribing" not in RETRYABLE


def test_a_captured_meeting_is_marked_in_the_list():
    from tui.screens.library import _fmt_name

    meeting = _fmt_name({"filename": "call.wav", "source": "meeting"})
    upload = _fmt_name({"filename": "call.wav", "source": "upload"})
    assert str(meeting).startswith("◉")
    assert str(upload) == "call.wav"


def test_an_alias_wins_over_the_filename():
    from tui.screens.library import _fmt_name

    assert "Board review" in str(_fmt_name({"filename": "a.mp3", "alias": "Board review"}))
