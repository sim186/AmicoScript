"""Unit tests for library chat: chunking, fusion, citations, prompt rules."""
import pytest

from core.embeddings import dot, pack, unpack
from core.library_chat import build_prompt, cited_indices, fuse
from core.library_index import MIN_CHUNK_CHARS, TARGET_CHUNK_CHARS, build_chunks
from search_query import build_fts_or_match


def _segments(count: int, words: int = 30, speaker: str = "Ada") -> list[dict]:
    return [
        {
            "start": float(i * 5),
            "end": float(i * 5 + 5),
            "text": " ".join(f"word{i}x{w}" for w in range(words)),
            "speaker": speaker,
        }
        for i in range(count)
    ]


# --- chunking ----------------------------------------------------------------


def test_a_short_transcript_is_one_chunk():
    chunks = build_chunks(_segments(2, words=5))
    assert len(chunks) == 1
    assert chunks[0]["ordinal"] == 0


def test_chunks_carry_the_span_they_were_spoken_at():
    """Without this an answer cannot cite a timestamp, which is the point."""
    chunks = build_chunks(_segments(2, words=5))
    assert chunks[0]["start"] == 0.0
    assert chunks[0]["end"] == 10.0


def test_a_long_transcript_is_split():
    chunks = build_chunks(_segments(40))
    assert len(chunks) > 1
    # Every chunk but the last is at least the target size.
    assert all(len(c["text"]) >= TARGET_CHUNK_CHARS for c in chunks[:-1])


def test_chunks_are_numbered_in_order():
    chunks = build_chunks(_segments(40))
    assert [c["ordinal"] for c in chunks] == list(range(len(chunks)))


def test_chunk_times_do_not_go_backwards():
    chunks = build_chunks(_segments(40))
    for previous, following in zip(chunks, chunks[1:]):
        assert following["start"] >= previous["start"]
        assert previous["end"] <= following["end"]


def test_consecutive_chunks_overlap():
    """A sentence split across the boundary stays findable from either side."""
    chunks = build_chunks(_segments(40))
    tail = chunks[0]["text"][-40:]
    assert tail in chunks[1]["text"]


def test_a_trailing_scrap_joins_the_previous_chunk():
    """'Yeah, exactly.' is not a passage worth retrieving on its own."""
    segments = _segments(40) + [
        {"start": 500.0, "end": 501.0, "text": "Yeah, exactly.", "speaker": "Grace"}
    ]
    chunks = build_chunks(segments)
    assert all(len(c["text"]) >= MIN_CHUNK_CHARS for c in chunks)
    assert chunks[-1]["text"].endswith("Yeah, exactly.")
    assert chunks[-1]["end"] == 501.0


def test_speakers_are_recorded_per_chunk():
    segments = [
        {"start": 0, "end": 2, "text": "Morning.", "speaker": "Ada"},
        {"start": 2, "end": 4, "text": "Morning!", "speaker": "Grace"},
        {"start": 4, "end": 6, "text": "Shall we start?", "speaker": "Ada"},
    ]
    assert build_chunks(segments)[0]["speakers"] == "Ada, Grace"


def test_empty_segments_are_skipped():
    segments = [
        {"start": 0, "end": 1, "text": "   ", "speaker": "Ada"},
        {"start": 1, "end": 2, "text": "Real words here.", "speaker": "Ada"},
    ]
    chunks = build_chunks(segments)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Real words here."


def test_a_transcript_with_no_segments_yields_no_chunks():
    assert build_chunks([]) == []


# --- question shaping --------------------------------------------------------


def test_a_question_ors_its_content_words():
    """ANDing them — the search box's behaviour — would match nothing."""
    expression = build_fts_or_match("What did we decide about pricing?")
    assert " OR " in expression
    assert "AND" not in expression
    assert '"pricing"' in expression
    assert '"what"' not in expression  # glue is dropped
    assert '"decide"' in expression


def test_a_quoted_phrase_survives_stopword_removal():
    expression = build_fts_or_match('who said "it is what it is"')
    assert '"it is what it is"' in expression


def test_a_question_of_pure_glue_has_nothing_to_search_for():
    assert build_fts_or_match("what is it about?") == ""


def test_an_empty_question_is_empty():
    assert build_fts_or_match("   ") == ""


def test_the_term_count_is_capped():
    expression = build_fts_or_match(" ".join(f"term{i}" for i in range(40)), max_terms=5)
    assert expression.count(" OR ") == 4


# --- fusion ------------------------------------------------------------------


def test_fusion_rewards_agreement_between_the_two_rankings():
    """A passage both methods like beats one that only keyword search found."""
    keyword = ["only_keyword", "agreed"]
    semantic = ["agreed", "only_semantic"]
    assert fuse([keyword, semantic], limit=1) == ["agreed"]


def test_fusion_still_favours_a_confident_single_ranking():
    """RRF does not require agreement — a clear first place survives being
    ranked last by the other method."""
    assert fuse([["a", "b", "c"], ["c", "b", "a"]], limit=2)[:1] == ["a"]


def test_fusion_keeps_a_hit_only_one_ranking_found():
    assert set(fuse([["a"], ["z"]], limit=2)) == {"a", "z"}


def test_fusion_of_one_ranking_preserves_its_order():
    assert fuse([["a", "b", "c"]], limit=3) == ["a", "b", "c"]


def test_fusion_of_nothing_is_nothing():
    assert fuse([], limit=5) == []


# --- citations ---------------------------------------------------------------


def test_citations_are_found_in_order_of_use():
    assert cited_indices("Revenue rose [2], and churn was flat [1].", 3) == [2, 1]


def test_a_repeated_citation_is_listed_once():
    assert cited_indices("[1] and again [1]", 2) == [1]


def test_a_citation_beyond_the_source_count_is_dropped():
    """A model citing [9] out of four sources invented that reference."""
    assert cited_indices("As discussed [9].", 4) == []


def test_an_uncited_answer_has_no_citations():
    assert cited_indices("I could not find that in your recordings.", 4) == []


def test_multiple_adjacent_citations_are_all_found():
    assert cited_indices("Both said so [1][3].", 3) == [1, 3]


# --- the prompt --------------------------------------------------------------


def _source(i: int) -> dict:
    return {
        "title": f"Standup {i}", "timestamp": "1:05", "speakers": "Ada",
        "text": f"passage {i}", "recording_id": f"rec{i}", "start": 65.0,
        "end": 90.0, "chunk_id": f"c{i}",
    }


def test_the_prompt_numbers_every_source():
    prompt = build_prompt("what happened?", [_source(1), _source(2)])
    assert "[1] Standup 1 at 1:05" in prompt
    assert "[2] Standup 2 at 1:05" in prompt


def test_the_prompt_forbids_outside_knowledge_and_demands_citations():
    prompt = build_prompt("q", [_source(1)])
    assert "only from these passages" in prompt
    assert "Cite the passages" in prompt


def test_the_prompt_tells_the_model_it_may_have_nothing_to_say():
    """Otherwise a model with irrelevant passages invents a relevant answer."""
    prompt = build_prompt("q", [_source(1)])
    assert "say so plainly" in prompt


def test_a_source_without_speakers_omits_the_label():
    source = {**_source(1), "speakers": ""}
    assert "speakers:" not in build_prompt("q", [source])


# --- vector storage ----------------------------------------------------------


def test_a_packed_vector_round_trips():
    vector = unpack(pack([3.0, 4.0]))
    assert len(vector) == 2
    assert vector[0] == pytest.approx(0.6, abs=1e-6)
    assert vector[1] == pytest.approx(0.8, abs=1e-6)


def test_packing_normalises_so_similarity_is_a_dot_product():
    a = unpack(pack([1.0, 0.0, 0.0]))
    assert dot(a, a) == pytest.approx(1.0, abs=1e-6)


def test_orthogonal_vectors_score_zero():
    a = unpack(pack([1.0, 0.0]))
    b = unpack(pack([0.0, 1.0]))
    assert dot(a, b) == pytest.approx(0.0, abs=1e-6)


def test_a_zero_vector_packs_to_nothing_rather_than_dividing_by_zero():
    assert pack([0.0, 0.0]) == b""


def test_vectors_of_different_lengths_do_not_compare():
    """A change of embedding model must not silently score against old rows."""
    assert dot([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_a_corrupt_blob_unpacks_to_nothing():
    assert unpack(b"abc") == []
    assert unpack(b"") == []
