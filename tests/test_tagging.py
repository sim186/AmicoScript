"""Unit tests for LLM tag suggestion.

The parsing tests are the point of this file: a small local model's reply to
"return a JSON array" is only sometimes a JSON array.
"""
import pytest

from core.tagging import (
    MAX_SUGGESTIONS,
    build_prompt,
    clean_suggestions,
    parse_suggestions,
    sample_transcript,
)


# --- parsing what the model actually said ------------------------------------


def test_parses_a_plain_json_array():
    assert parse_suggestions('["hiring", "q3 planning"]') == ["hiring", "q3 planning"]


def test_parses_a_fenced_json_array():
    raw = 'Here you go:\n```json\n["hiring", "budget"]\n```\n'
    assert parse_suggestions(raw) == ["hiring", "budget"]


def test_parses_an_array_buried_in_prose():
    raw = 'Based on the transcript I would suggest ["onboarding", "design review"] as tags.'
    assert parse_suggestions(raw) == ["onboarding", "design review"]


def test_parses_a_bulleted_list():
    assert parse_suggestions("- hiring\n- budget\n* q3") == ["hiring", "budget", "q3"]


def test_parses_a_numbered_list():
    assert parse_suggestions("1. hiring\n2) budget") == ["hiring", "budget"]


def test_parses_a_single_comma_separated_line():
    assert parse_suggestions("hiring, budget, q3 planning") == [
        "hiring", "budget", "q3 planning",
    ]


@pytest.mark.parametrize("prose", [
    "I'm sorry, I can't help with that.",
    "The transcript is about a meeting, but I need more context.",
    "Sure! Here are some tags for your recording.",
])
def test_a_prose_reply_is_not_read_as_a_comma_separated_list(prose):
    """Splitting a sentence on commas produced tags like 'I'm sorry'."""
    assert parse_suggestions(prose) == []


def test_an_empty_reply_yields_nothing():
    assert parse_suggestions("") == []
    assert parse_suggestions("   \n ") == []


def test_a_json_object_is_not_mistaken_for_a_list():
    assert parse_suggestions('{"tags": "hiring"}') == []


def test_a_json_object_wrapping_the_list_is_unwrapped():
    """Told to reply with JSON, a model often decides an object is friendlier."""
    assert parse_suggestions('{"tags": ["hiring", "budget"]}') == ["hiring", "budget"]


def test_non_string_array_members_are_dropped():
    assert parse_suggestions('["hiring", null, {"a": 1}, "budget"]') == ["hiring", "budget"]


# --- cleaning ----------------------------------------------------------------


def test_hashes_and_quotes_are_stripped():
    assert clean_suggestions(["#hiring", '"budget"']) == ["hiring", "budget"]


def test_duplicates_are_removed_case_insensitively():
    assert clean_suggestions(["Hiring", "hiring", "HIRING"]) == ["Hiring"]


def test_the_librarys_own_spelling_wins():
    """Accepting the chip must reuse the existing tag, not sit beside it."""
    out = clean_suggestions(["standup"], existing_tags=["Standup"])
    assert out == ["Standup"]


def test_tags_already_on_the_recording_are_not_suggested_again():
    out = clean_suggestions(["hiring", "budget"], already_applied=["Hiring"])
    assert out == ["budget"]


def test_a_sentence_is_not_a_tag():
    long = "this is the model narrating what the meeting was about at length"
    assert clean_suggestions([long, "hiring"]) == ["hiring"]


def test_a_short_sentence_is_still_not_a_tag():
    """Under the character cap but over the word cap — prose, not a label."""
    assert clean_suggestions(["a bit of a chat", "hiring"]) == ["hiring"]


def test_the_list_is_capped():
    out = clean_suggestions([f"tag{i}" for i in range(20)])
    assert len(out) == MAX_SUGGESTIONS


def test_internal_whitespace_is_collapsed():
    assert clean_suggestions(["q3   planning\t"]) == ["q3 planning"]


def test_empty_entries_are_dropped():
    assert clean_suggestions(["", "  ", "#", "hiring"]) == ["hiring"]


# --- sampling ----------------------------------------------------------------


def test_a_short_transcript_is_sent_whole():
    text = "We discussed hiring."
    assert sample_transcript(text, 1000) == text


def test_a_long_transcript_is_sampled_across_its_whole_length():
    """Truncating to the first N tokens would tag a meeting by its small talk."""
    text = ("early " * 2000) + ("middle " * 2000) + ("late " * 2000)
    out = sample_transcript(text, 200)

    assert len(out) < len(text)
    assert "early" in out
    assert "late" in out  # the end is what a head-truncation would lose


def test_sampling_marks_where_it_cut():
    out = sample_transcript("word " * 20000, 200)
    assert "[…]" in out


# --- the prompt --------------------------------------------------------------


def test_the_prompt_lists_the_existing_tags():
    prompt = build_prompt("some transcript", ["hiring", "budget"])
    assert "budget, hiring" in prompt  # sorted, so the order is stable
    assert "near-duplicate" in prompt


def test_the_prompt_omits_the_reuse_instruction_for_an_empty_library():
    prompt = build_prompt("some transcript", [])
    assert "already uses these tags" not in prompt


def test_the_transcript_is_delimited():
    prompt = build_prompt("hello there", [])
    assert "<transcript>\nhello there\n</transcript>" in prompt


@pytest.mark.parametrize("generic", ["audio", "transcript", "recording"])
def test_the_prompt_warns_against_tags_that_fit_everything(generic):
    assert generic in build_prompt("x", [])
