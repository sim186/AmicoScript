"""Tests for automatic summarization of captured meetings."""
import pytest

import state

pytestmark = pytest.mark.usefixtures("no_auth")


@pytest.fixture()
def llm_configured(monkeypatch):
    from core import analysis
    from settings import _load_settings, _save_llm_settings, _save_settings

    # Stub the transport: a queued auto-summary is picked up by the app's real
    # worker loop, and it must not reach for the network during tests.
    monkeypatch.setattr(analysis, "run_completion", lambda *a, **k: ("SUMMARY", False))
    _save_llm_settings("http://llm.test", "test-model", "")
    yield
    settings = _load_settings()
    for key in ("llm_base_url", "llm_model_name", "llm_api_key"):
        settings.pop(key, None)
    _save_settings(settings)


@pytest.fixture()
def auto_summary_on():
    from settings import _set_auto_summarize_meetings

    _set_auto_summarize_meetings(True)
    yield
    _set_auto_summarize_meetings(False)


def _queue_for(recording_id: str) -> str:
    from core.analysis_jobs import maybe_queue_auto_summary

    return maybe_queue_auto_summary(recording_id)


def _analyses(recording_id: str) -> list[dict]:
    """Analysis rows as plain dicts — the ORM objects detach when the session closes."""
    from db import new_session
    from models import Analysis
    from sqlmodel import select

    with new_session() as session:
        rows = session.exec(
            select(Analysis).where(Analysis.recording_id == recording_id)
        ).all()
        return [
            {"analysis_type": r.analysis_type, "auto_generated": bool(r.auto_generated)}
            for r in rows
        ]


@pytest.mark.usefixtures("llm_configured", "auto_summary_on")
def test_a_finished_meeting_is_summarised(client, make_recording, sample_segments):
    rec_id = make_recording(source="meeting", segments=sample_segments)

    job_id = _queue_for(rec_id)

    assert job_id
    rows = _analyses(rec_id)
    assert len(rows) == 1
    assert rows[0]["analysis_type"] == "summary"
    assert rows[0]["auto_generated"] is True
    assert state.jobs[job_id]["options"]["analysis_type"] == "summary"
    assert "quarterly review" in state.jobs[job_id]["options"]["transcript_full_text"]


@pytest.mark.usefixtures("llm_configured", "auto_summary_on")
def test_uploads_are_not_summarised(client, make_recording, sample_segments):
    """Only captured calls — an uploaded file was a deliberate user action."""
    rec_id = make_recording(source="upload", segments=sample_segments)

    assert _queue_for(rec_id) == ""
    assert _analyses(rec_id) == []


@pytest.mark.usefixtures("llm_configured")
def test_nothing_happens_when_the_setting_is_off(client, make_recording, sample_segments):
    rec_id = make_recording(source="meeting", segments=sample_segments)

    assert _queue_for(rec_id) == ""
    assert _analyses(rec_id) == []


@pytest.mark.usefixtures("auto_summary_on")
def test_nothing_happens_without_a_configured_llm(client, make_recording, sample_segments):
    from settings import _load_settings, _save_settings

    settings = _load_settings()
    settings["llm_model_name"] = ""
    _save_settings(settings)

    rec_id = make_recording(source="meeting", segments=sample_segments)
    assert _queue_for(rec_id) == ""


@pytest.mark.usefixtures("llm_configured", "auto_summary_on")
def test_a_meeting_without_a_transcript_is_skipped(client, make_recording):
    rec_id = make_recording(source="meeting")  # no segments -> no transcript row
    assert _queue_for(rec_id) == ""


@pytest.mark.usefixtures("llm_configured", "auto_summary_on")
def test_summarising_twice_is_prevented(client, make_recording, sample_segments):
    rec_id = make_recording(source="meeting", segments=sample_segments)

    assert _queue_for(rec_id)
    assert _queue_for(rec_id) == ""
    assert len(_analyses(rec_id)) == 1


@pytest.mark.usefixtures("llm_configured", "auto_summary_on")
def test_a_failure_never_breaks_the_transcription(client, monkeypatch, make_recording, sample_segments):
    from core import analysis_jobs

    rec_id = make_recording(source="meeting", segments=sample_segments)
    monkeypatch.setattr(
        analysis_jobs, "create_analysis_job",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    # Returns empty rather than propagating — the transcript is already saved.
    assert analysis_jobs.maybe_queue_auto_summary(rec_id) == ""


def test_the_setting_round_trips_through_the_api(client):
    assert client.get("/api/settings").json()["auto_summarize_meetings"] is False

    client.post("/api/settings", data={"auto_summarize_meetings": "true"})
    assert client.get("/api/settings").json()["auto_summarize_meetings"] is True

    client.post("/api/settings", data={"auto_summarize_meetings": "false"})
    assert client.get("/api/settings").json()["auto_summarize_meetings"] is False


def test_saving_an_unrelated_setting_leaves_the_toggle_alone(client):
    client.post("/api/settings", data={"auto_summarize_meetings": "true"})
    client.post("/api/settings", data={"model": "medium"})
    assert client.get("/api/settings").json()["auto_summarize_meetings"] is True


def test_saving_settings_does_not_clobber_the_stored_hf_token(client):
    """The UI only ever sees a masked token; saving must not write the mask back."""
    from settings import _load_settings

    client.post("/api/settings", data={"hf_token": "hf_realtoken"})
    client.post("/api/settings", data={"hf_token": "__unchanged__", "model": "small"})

    assert _load_settings()["hf_token"] == "hf_realtoken"


def test_llm_settings_never_return_the_api_key(client):
    from settings import _load_settings

    client.post(
        "/api/llm/settings",
        data={
            "llm_base_url": "http://llm.test",
            "llm_model_name": "m",
            "llm_api_key": "sk-secret-value",
        },
    )
    body = client.get("/api/llm/settings").json()
    assert "llm_api_key" not in body
    assert body["llm_api_key_set"] is True

    # Saving again without touching the key preserves it.
    client.post(
        "/api/llm/settings",
        data={"llm_base_url": "http://llm.test", "llm_model_name": "m2"},
    )
    assert _load_settings()["llm_api_key"] == "sk-secret-value"


def test_llm_context_budget_is_configurable(client):
    client.post(
        "/api/llm/settings",
        data={
            "llm_base_url": "http://llm.test",
            "llm_model_name": "m",
            "llm_context_tokens": "16384",
            "llm_max_output_tokens": "2048",
        },
    )
    body = client.get("/api/llm/settings").json()
    assert body["llm_context_tokens"] == 16384
    assert body["llm_max_output_tokens"] == 2048


@pytest.mark.usefixtures("clean_settings")
def test_invalid_context_budget_falls_back_to_the_default(client):
    client.post(
        "/api/llm/settings",
        data={
            "llm_base_url": "http://llm.test",
            "llm_model_name": "m",
            "llm_context_tokens": "-5",
        },
    )
    # A nonsensical value leaves the stored budget untouched rather than
    # writing a setting that would break every analysis.
    assert client.get("/api/llm/settings").json()["llm_context_tokens"] == 8192
