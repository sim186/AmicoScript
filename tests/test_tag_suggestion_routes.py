"""Route-level tests for LLM tag suggestion.

The transport is stubbed throughout — what is under test is the wiring: the
guards that run before a transcript is sent anywhere, and the fact that a
suggestion is only ever a suggestion.
"""
import pytest

pytestmark = pytest.mark.usefixtures("no_auth")


@pytest.fixture()
def llm(monkeypatch):
    """Configure a local LLM and stub the transport. Returns a recorder."""
    from core import tagging
    from settings import _load_settings, _save_llm_settings, _save_settings

    calls = {"prompts": [], "reply": '["hiring", "q3 planning"]'}

    def _fake_completion(target, prompt, *a, **k):
        calls["prompts"].append(prompt)
        return calls["reply"], False

    monkeypatch.setattr(tagging, "run_completion", _fake_completion)
    _save_llm_settings("http://llm.test", "test-model", "")
    yield calls
    settings = _load_settings()
    # llm_provider and llm_allow_cloud too: the hosted-provider test sets them,
    # and leaving them behind makes every later test in the file refuse.
    for key in (
        "llm_base_url", "llm_model_name", "llm_api_key",
        "llm_provider", "llm_allow_cloud",
    ):
        settings.pop(key, None)
    _save_settings(settings)


def _suggest(client, rec_id):
    return client.post(f"/api/recordings/{rec_id}/suggest-tags")


def test_suggestions_come_back_for_a_transcribed_recording(
    client, make_recording, sample_segments, llm
):
    rec_id = make_recording(segments=sample_segments)

    resp = _suggest(client, rec_id)

    assert resp.status_code == 200
    assert [s["name"] for s in resp.json()["suggestions"]] == ["hiring", "q3 planning"]


def test_nothing_is_applied_without_the_user_saying_so(
    client, make_recording, sample_segments, llm
):
    """The whole design: the model proposes, the user disposes."""
    rec_id = make_recording(segments=sample_segments)

    _suggest(client, rec_id)

    assert client.get(f"/api/recordings/{rec_id}").json()["tags"] == []
    assert client.get("/api/tags").json() == []


def test_an_existing_tag_comes_back_with_its_id(
    client, make_recording, sample_segments, llm
):
    """So accepting the chip reuses that tag rather than creating a twin."""
    tag = client.post("/api/tags", data={"name": "Hiring"}).json()
    rec_id = make_recording(segments=sample_segments)

    suggestions = _suggest(client, rec_id).json()["suggestions"]

    by_name = {s["name"]: s["tag_id"] for s in suggestions}
    # Spelled the library's way, not the model's, and carrying the real id.
    assert by_name["Hiring"] == tag["id"]
    assert by_name["q3 planning"] is None


def test_the_prompt_shows_the_model_the_tags_the_library_already_uses(
    client, make_recording, sample_segments, llm
):
    client.post("/api/tags", data={"name": "budget"})
    rec_id = make_recording(segments=sample_segments)

    _suggest(client, rec_id)

    assert "budget" in llm["prompts"][0]


def test_a_tag_already_on_the_recording_is_not_suggested_again(
    client, make_recording, sample_segments, llm
):
    tag = client.post("/api/tags", data={"name": "hiring"}).json()
    rec_id = make_recording(segments=sample_segments)
    client.post(f"/api/recordings/{rec_id}/tags/{tag['id']}")

    names = [s["name"] for s in _suggest(client, rec_id).json()["suggestions"]]

    assert names == ["q3 planning"]


def test_a_recording_without_a_transcript_says_so(client, make_recording, llm):
    rec_id = make_recording()
    resp = _suggest(client, rec_id)
    assert resp.status_code == 404
    assert "transcription" in resp.json()["detail"]


def test_an_unknown_recording_is_a_404(client, llm):
    assert _suggest(client, "nope").status_code == 404


def test_it_refuses_when_no_model_is_configured(client, make_recording, sample_segments):
    rec_id = make_recording(segments=sample_segments)
    resp = _suggest(client, rec_id)
    assert resp.status_code == 400
    assert "No LLM model configured" in resp.json()["detail"]


def test_it_refuses_a_hosted_provider_without_consent(
    client, make_recording, sample_segments, monkeypatch, llm
):
    """A hosted model would receive the transcript — the one thing this app
    promises not to do unless asked."""
    from settings import _load_settings, _save_settings

    settings = _load_settings()
    settings["llm_provider"] = "openrouter"
    settings["llm_allow_cloud"] = False
    _save_settings(settings)

    resp = _suggest(client, make_recording(segments=sample_segments))

    assert resp.status_code == 400
    assert "hosted" in resp.json()["detail"]
    assert llm["prompts"] == []  # refused before anything was sent


def test_a_model_that_cannot_be_reached_is_reported_as_such(
    client, make_recording, sample_segments, monkeypatch, llm
):
    from core import tagging

    def _boom(*a, **k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(tagging, "run_completion", _boom)

    resp = _suggest(client, make_recording(segments=sample_segments))

    assert resp.status_code == 502
    assert "could not be reached" in resp.json()["detail"]


def test_an_unparseable_reply_is_an_empty_list_not_an_error(
    client, make_recording, sample_segments, llm
):
    llm["reply"] = "I'm sorry, I can't help with that."

    resp = _suggest(client, make_recording(segments=sample_segments))

    assert resp.status_code == 200
    # The sentence is too long to be a tag, so it is dropped rather than shown.
    assert resp.json()["suggestions"] == []
