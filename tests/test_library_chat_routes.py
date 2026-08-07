"""Route-level tests for library chat.

Retrieval here is real — the FTS5 index over real chunks, built from real
transcripts. Only the LLM transport is stubbed, so what these tests exercise is
whether the right passages come back and whether the citations survive the trip.
"""
import pytest

pytestmark = pytest.mark.usefixtures("no_auth")


PRICING = [
    {"id": 0, "start": 0.0, "end": 40.0, "speaker": "Ada",
     "text": "Let us settle the pricing question today. " * 12},
    {"id": 1, "start": 40.0, "end": 95.0, "speaker": "Grace",
     "text": "We agreed to charge forty a seat for the team plan. " * 12},
]

HIRING = [
    {"id": 0, "start": 0.0, "end": 50.0, "speaker": "Ada",
     "text": "The hiring loop needs another interviewer for backend. " * 12},
]


@pytest.fixture()
def llm(monkeypatch):
    """A configured local LLM with a stubbed transport. Returns a recorder."""
    from core import library_chat
    from settings import load_settings, save_llm_settings, save_settings

    calls = {"prompts": [], "reply": "You agreed on forty a seat [2]."}

    def _fake_completion(target, prompt, *a, **k):
        calls["prompts"].append(prompt)
        return calls["reply"], False

    monkeypatch.setattr(library_chat, "run_completion", _fake_completion)
    save_llm_settings("http://llm.test", "test-model", "")
    yield calls
    settings = load_settings()
    for key in (
        "llm_base_url", "llm_model_name", "llm_api_key",
        "llm_provider", "llm_allow_cloud", "llm_embedding_model",
    ):
        settings.pop(key, None)
    save_settings(settings)


def _ask(client, question):
    return client.post("/api/library/chat", json={"question": question})


def _indexed(client, make_recording, segments, filename="rec.mp3"):
    rec_id = make_recording(filename=filename, segments=segments)
    client.post("/api/library/index/rebuild")
    return rec_id


# --- indexing ----------------------------------------------------------------


def test_rebuilding_indexes_transcripts_that_have_no_chunks(
    client, make_recording, sample_segments
):
    make_recording(segments=sample_segments)

    resp = client.post("/api/library/index/rebuild")

    assert resp.status_code == 200
    assert resp.json()["chunks"] >= 1
    assert client.get("/api/library/index").json()["recordings_pending"] == 0


def test_the_index_status_counts_what_is_waiting(client, make_recording, sample_segments):
    make_recording(segments=sample_segments)
    status = client.get("/api/library/index").json()
    assert status["recordings_with_transcripts"] == 1
    assert status["recordings_pending"] == 1
    assert status["chunks"] == 0


def test_editing_a_segment_rewrites_that_recordings_chunks(
    client, make_recording, sample_segments
):
    """A stale chunk would keep the old wording quotable in an answer."""
    rec_id = _indexed(client, make_recording, sample_segments)

    client.patch(
        f"/api/recordings/{rec_id}/transcript/segments/0",
        data={"text": "Completely different wording about zebras."},
    )

    from db import new_session
    from models import TranscriptChunk
    from sqlmodel import select

    with new_session() as session:
        text = " ".join(
            c.text for c in session.exec(
                select(TranscriptChunk).where(TranscriptChunk.recording_id == rec_id)
            ).all()
        )
    assert "zebras" in text
    assert "Welcome to the quarterly review" not in text


def test_deleting_a_recording_takes_its_chunks_with_it(
    client, make_recording, sample_segments
):
    rec_id = _indexed(client, make_recording, sample_segments)
    assert client.get("/api/library/index").json()["chunks"] >= 1

    client.delete(f"/api/recordings/{rec_id}")

    assert client.get("/api/library/index").json()["chunks"] == 0


# --- answering ---------------------------------------------------------------


def test_a_question_is_answered_from_the_matching_recording(
    client, make_recording, llm
):
    _indexed(client, make_recording, PRICING, filename="pricing.mp3")
    _indexed(client, make_recording, HIRING, filename="hiring.mp3")

    body = _ask(client, "What did we agree about pricing?").json()

    assert body["answer"] == "You agreed on forty a seat [2]."
    titles = {s["title"] for s in body["sources"]}
    assert "pricing.mp3" in titles


def test_sources_carry_the_timestamp_to_jump_to(client, make_recording, llm):
    _indexed(client, make_recording, PRICING, filename="pricing.mp3")

    source = _ask(client, "What did we agree about pricing?").json()["sources"][0]

    assert source["recording_id"]
    assert "timestamp" in source and ":" in source["timestamp"]
    assert source["end"] >= source["start"]


def test_the_cited_numbers_line_up_with_the_returned_sources(
    client, make_recording, llm
):
    """The [n] markers index into 'sources', so the order must not be shuffled."""
    _indexed(client, make_recording, PRICING, filename="pricing.mp3")
    _indexed(client, make_recording, HIRING, filename="hiring.mp3")
    llm["reply"] = "Two things were discussed [1][2]."

    body = _ask(client, "What did we agree about pricing and hiring?").json()

    assert body["cited"] == [1, 2]
    assert len(body["sources"]) >= 2
    for index in body["cited"]:
        assert body["sources"][index - 1]["text"]


def test_an_invented_citation_is_not_returned_as_a_source(
    client, make_recording, llm
):
    _indexed(client, make_recording, PRICING, filename="pricing.mp3")
    llm["reply"] = "As covered at length [42]."

    assert _ask(client, "What about pricing?").json()["cited"] == []


def test_the_prompt_carries_the_passages_and_the_question(client, make_recording, llm):
    _indexed(client, make_recording, PRICING, filename="pricing.mp3")

    _ask(client, "What did we agree about pricing?")

    prompt = llm["prompts"][0]
    assert "forty a seat" in prompt
    assert "Question: What did we agree about pricing?" in prompt


def test_a_question_matching_nothing_says_so_rather_than_inventing(
    client, make_recording, llm
):
    _indexed(client, make_recording, PRICING, filename="pricing.mp3")

    body = _ask(client, "What did we say about xylophones?").json()

    assert body["no_matches"] is True
    assert body["sources"] == []
    assert llm["prompts"] == []  # the model was never asked


def test_keyword_retrieval_works_with_no_embedding_model(client, make_recording, llm):
    """Semantic search is the optional half; chat must work without any setup."""
    _indexed(client, make_recording, PRICING, filename="pricing.mp3")

    body = _ask(client, "What did we agree about pricing?").json()

    assert body["used_semantic"] is False
    assert body["sources"]


# --- guards ------------------------------------------------------------------


def test_an_empty_question_is_rejected(client, llm):
    assert _ask(client, "   ").status_code == 400


def test_chat_refuses_before_anything_is_indexed(client, make_recording, llm):
    make_recording(segments=PRICING)
    resp = _ask(client, "What about pricing?")
    assert resp.status_code == 409
    assert "index" in resp.json()["detail"].lower()


def test_chat_refuses_when_no_model_is_configured(client, make_recording, sample_segments):
    _indexed(client, make_recording, sample_segments)
    resp = _ask(client, "What happened?")
    assert resp.status_code == 400
    assert "No LLM model configured" in resp.json()["detail"]


def test_chat_refuses_a_hosted_provider_without_consent(
    client, make_recording, llm
):
    from settings import load_settings, save_settings

    _indexed(client, make_recording, PRICING, filename="pricing.mp3")
    settings = load_settings()
    settings["llm_provider"] = "openrouter"
    settings["llm_allow_cloud"] = False
    save_settings(settings)

    resp = _ask(client, "What about pricing?")

    assert resp.status_code == 400
    assert "hosted" in resp.json()["detail"]
    assert llm["prompts"] == []


def test_embedding_refuses_without_an_embedding_model(client, make_recording, llm):
    _indexed(client, make_recording, PRICING, filename="pricing.mp3")
    resp = client.post("/api/library/index/embed")
    assert resp.status_code == 400
    assert "embedding model" in resp.json()["detail"]


def test_embedding_stores_vectors_and_reports_what_is_left(
    client, make_recording, monkeypatch, llm
):
    from core import embeddings
    from api.routes import library_chat as routes
    from settings import load_settings, save_settings

    _indexed(client, make_recording, PRICING, filename="pricing.mp3")
    settings = load_settings()
    settings["llm_embedding_model"] = "test-embed"
    save_settings(settings)

    monkeypatch.setattr(
        routes, "embed_texts", lambda texts, cfg: [embeddings.pack([1.0, 0.0, 0.0])] * len(texts)
    )

    resp = client.post("/api/library/index/embed").json()

    assert resp["embedded"] >= 1
    assert resp["remaining"] == 0
    assert client.get("/api/library/index").json()["semantic_available"] is True
