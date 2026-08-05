"""Tests for long-transcript handling in LLM analysis.

The bug being guarded against: a transcript larger than the model's context
window used to be sent whole, so the server silently dropped the beginning and
returned a summary of the tail with no error anywhere.
"""
import threading

import pytest

import state
from core import analysis


# --- token estimation and chunking -----------------------------------------


def test_estimate_tokens_scales_with_length():
    assert analysis.estimate_tokens("") == 0
    short = analysis.estimate_tokens("hello world")
    long = analysis.estimate_tokens("hello world " * 100)
    assert long > short * 50


def test_short_text_is_a_single_chunk():
    assert analysis.chunk_text("a short transcript", 1000) == ["a short transcript"]


def test_empty_text_produces_no_chunks():
    assert analysis.chunk_text("   ", 1000) == []


def test_long_text_is_split_into_chunks_that_fit():
    text = "\n\n".join(f"Paragraph number {i} with some spoken words in it." for i in range(200))
    chunks = analysis.chunk_text(text, 100)

    assert len(chunks) > 1
    assert all(analysis.estimate_tokens(c) <= 100 for c in chunks)


def test_chunking_preserves_all_the_words():
    text = "\n\n".join(f"Sentence {i} here." for i in range(120))
    chunks = analysis.chunk_text(text, 60)

    original_words = text.split()
    chunked_words = " ".join(chunks).split()
    assert chunked_words == original_words


def test_a_single_huge_paragraph_is_split_too():
    text = "word " * 5000  # no paragraph or sentence boundaries at all
    chunks = analysis.chunk_text(text, 100)
    assert len(chunks) > 1
    assert all(analysis.estimate_tokens(c) <= 100 for c in chunks)


def test_chunks_split_on_sentence_boundaries_when_there_are_no_paragraphs():
    text = " ".join(f"This is sentence number {i}." for i in range(200))
    chunks = analysis.chunk_text(text, 80)
    assert len(chunks) > 1
    # No chunk should begin mid-sentence with a lowercase fragment.
    assert all(c.strip()[0].isupper() for c in chunks)


# --- prompt construction ----------------------------------------------------


def test_map_and_reduce_prompts_are_built_for_each_type():
    for kind in ("summary", "action_items"):
        mapped = analysis._build_map_prompt(kind, "chunk text", 2, 5)
        assert "part 2 of 5" in mapped
        assert "chunk text" in mapped

        reduced = analysis._build_reduce_prompt(kind, ["one", "two"])
        assert "one" in reduced and "two" in reduced


def test_translate_has_no_reduce_step():
    with pytest.raises(ValueError):
        analysis._build_reduce_prompt("translate", ["a", "b"])


def test_unknown_analysis_type_is_rejected():
    with pytest.raises(ValueError):
        analysis._build_analysis_prompt("astrology", "text")
    with pytest.raises(ValueError):
        analysis._build_map_prompt("astrology", "text", 1, 1)


# --- job behaviour ----------------------------------------------------------


class _FakeLLM:
    """Records every prompt it is asked to complete."""

    def __init__(self, reply="RESULT"):
        self.prompts: list[str] = []
        self.targets: list = []
        self.reply = reply

    def __call__(self, target, prompt, on_delta=None, should_cancel=None):
        self.prompts.append(prompt)
        self.targets.append(target)
        if on_delta:
            on_delta(self.reply, self.reply)
        return self.reply, False


@pytest.fixture()
def analysis_job(monkeypatch):
    """Build an analysis job + Analysis row and return (job_id, analysis_id)."""
    import uuid

    from db import new_session
    from models import Analysis

    def _make(full_text: str, context_tokens: int = 8192, analysis_type: str = "summary"):
        analysis_id = str(uuid.uuid4())
        with new_session() as session:
            session.add(
                Analysis(id=analysis_id, recording_id="rec-1", analysis_type=analysis_type)
            )
            session.commit()

        job_id = str(uuid.uuid4())
        state.jobs[job_id] = {
            "id": job_id,
            "type": "analysis",
            "recording_id": "rec-1",
            "analysis_id": analysis_id,
            "status": "queued",
            "progress": 0.0,
            "message": "",
            "options": {
                "analysis_type": analysis_type,
                "target_language": "French" if analysis_type == "translate" else "",
                "custom_prompt": "",
                "output_language": "",
                "transcript_full_text": full_text,
                "llm_base_url": "http://llm.test",
                "llm_model_name": "test-model",
                "llm_api_key": "",
                "llm_context_tokens": context_tokens,
                "llm_max_output_tokens": 256,
            },
            "cancel_flag": threading.Event(),
            "logs": [],
            "temp_files": [],
            "result": None,
            "error": None,
            "created_at": 0.0,
            "sse_queue": None,
            "event_loop": None,
        }
        return job_id, analysis_id

    yield _make
    state.jobs.clear()


def _stored(analysis_id):
    from db import new_session
    from models import Analysis

    with new_session() as session:
        row = session.get(Analysis, analysis_id)
        return row.status, row.result_text


@pytest.mark.usefixtures("api_app")
def test_short_transcript_uses_one_llm_call(monkeypatch, analysis_job):
    fake = _FakeLLM()
    monkeypatch.setattr(analysis, "run_completion", fake)

    job_id, analysis_id = analysis_job("A short meeting transcript.")
    analysis._process_analysis_job(job_id)

    assert len(fake.prompts) == 1
    assert "<transcript>" in fake.prompts[0]
    assert _stored(analysis_id) == ("done", "RESULT")


@pytest.mark.usefixtures("api_app")
def test_long_transcript_is_mapped_then_reduced(monkeypatch, analysis_job):
    fake = _FakeLLM()
    monkeypatch.setattr(analysis, "run_completion", fake)

    text = "\n\n".join(f"Paragraph {i} of the discussion." for i in range(400))
    job_id, analysis_id = analysis_job(text, context_tokens=2048)
    analysis._process_analysis_job(job_id)

    assert len(fake.prompts) > 2, "expected several map calls plus a reduce call"
    assert any("part 1 of" in p for p in fake.prompts)
    assert any("Merge them into a single coherent summary" in p for p in fake.prompts)
    assert _stored(analysis_id)[0] == "done"


@pytest.mark.usefixtures("api_app")
def test_every_part_of_a_long_transcript_reaches_the_model(monkeypatch, analysis_job):
    """The whole point: no part of the recording is silently dropped."""
    fake = _FakeLLM()
    monkeypatch.setattr(analysis, "run_completion", fake)

    text = "\n\n".join(f"Unique marker {i} spoken aloud." for i in range(300))
    job_id, _ = analysis_job(text, context_tokens=2048)
    analysis._process_analysis_job(job_id)

    combined = "\n".join(fake.prompts)
    assert "Unique marker 0 " in combined
    assert "Unique marker 299 " in combined


@pytest.mark.usefixtures("api_app")
def test_long_translation_is_concatenated_not_merged(monkeypatch, analysis_job):
    calls = []

    def fake(target, prompt, on_delta=None, should_cancel=None):
        calls.append(prompt)
        return f"translated-{len(calls)}", False

    monkeypatch.setattr(analysis, "run_completion", fake)
    text = "\n\n".join(f"Paragraph {i} to translate." for i in range(400))
    job_id, analysis_id = analysis_job(text, context_tokens=2048, analysis_type="translate")
    analysis._process_analysis_job(job_id)

    # No reduce prompt for translation — merging would rewrite the translation.
    assert not any("Merge them" in p for p in calls)
    status, result = _stored(analysis_id)
    assert status == "done"
    assert result.startswith("translated-1")
    assert f"translated-{len(calls)}" in result


@pytest.mark.usefixtures("api_app")
def test_cancelling_mid_chunk_stops_and_saves_what_was_produced(monkeypatch, analysis_job):
    text = "\n\n".join(f"Paragraph {i}." for i in range(400))
    job_id, analysis_id = analysis_job(text, context_tokens=2048)

    calls = {"n": 0}

    def fake(target, prompt, on_delta=None, should_cancel=None):
        calls["n"] += 1
        if calls["n"] == 2:
            # What the real run_completion does when should_cancel() fires
            # partway through a stream: return what it has, flagged cancelled.
            state.jobs[job_id]["cancel_flag"].set()
            return f"part-{calls['n']}", True
        return f"part-{calls['n']}", False

    monkeypatch.setattr(analysis, "run_completion", fake)
    analysis._process_analysis_job(job_id)

    status, result = _stored(analysis_id)
    assert status == "error"
    assert "part-1" in result
    assert state.jobs[job_id]["status"] == "cancelled"


@pytest.mark.usefixtures("api_app")
def test_llm_failure_marks_the_analysis_as_error(monkeypatch, analysis_job):
    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(analysis, "run_completion", boom)
    job_id, analysis_id = analysis_job("short text")
    analysis._process_analysis_job(job_id)

    assert _stored(analysis_id)[0] == "error"
    assert state.jobs[job_id]["status"] == "error"


# --- transport --------------------------------------------------------------


def _target(**overrides):
    defaults = {"base_url": "http://llm.test", "model_name": "m", "max_output_tokens": 128}
    return analysis.LLMTarget(**{**defaults, **overrides})


def test_streaming_falls_back_to_a_plain_request(monkeypatch):
    """Some proxies swallow SSE; an empty stream must not become an empty result."""
    monkeypatch.setattr(analysis, "_stream_completion", lambda *a, **k: ("", False))
    monkeypatch.setattr(
        analysis, "_blocking_completion", lambda *a, **k: "non-streamed answer"
    )

    text, cancelled = analysis.run_completion(_target(), "prompt")
    assert text == "non-streamed answer"
    assert cancelled is False


def test_http_errors_are_not_retried_without_streaming(monkeypatch):
    import requests

    def raise_http(*args, **kwargs):
        raise requests.HTTPError("401 Unauthorized")

    monkeypatch.setattr(analysis, "_stream_completion", raise_http)
    monkeypatch.setattr(analysis, "_blocking_completion", lambda *a, **k: "should not happen")

    with pytest.raises(requests.HTTPError):
        analysis.run_completion(_target(), "prompt")


def test_cancelled_stream_is_reported_as_cancelled(monkeypatch):
    monkeypatch.setattr(analysis, "_stream_completion", lambda *a, **k: ("partial", True))
    text, cancelled = analysis.run_completion(_target(), "p")
    assert (text, cancelled) == ("partial", True)


def test_target_is_built_from_job_options():
    target = analysis.LLMTarget.from_options({
        "llm_base_url": "http://localhost:1234/",
        "llm_model_name": "qwen",
        "llm_api_key": "sk-x",
        "llm_provider": "lmstudio",
        "llm_max_output_tokens": 512,
    })
    assert target.base_url == "http://localhost:1234"
    assert target.provider_id == "lmstudio"
    assert target.max_output_tokens == 512


@pytest.mark.usefixtures("api_app")
def test_a_cloud_provider_refuses_to_run_without_consent(monkeypatch, analysis_job):
    """The transcript must not reach a hosted provider by accident."""
    fake = _FakeLLM()
    monkeypatch.setattr(analysis, "run_completion", fake)

    job_id, analysis_id = analysis_job("short text")
    state.jobs[job_id]["options"]["llm_provider"] = "openrouter"
    state.jobs[job_id]["options"]["llm_allow_cloud"] = False
    analysis._process_analysis_job(job_id)

    assert fake.prompts == []
    assert _stored(analysis_id)[0] == "error"
    assert "hosted service" in state.jobs[job_id]["error"]


@pytest.mark.usefixtures("api_app")
def test_a_cloud_provider_runs_once_allowed(monkeypatch, analysis_job):
    fake = _FakeLLM()
    monkeypatch.setattr(analysis, "run_completion", fake)

    job_id, analysis_id = analysis_job("short text")
    state.jobs[job_id]["options"]["llm_provider"] = "openrouter"
    state.jobs[job_id]["options"]["llm_allow_cloud"] = True
    analysis._process_analysis_job(job_id)

    assert len(fake.prompts) == 1
    assert fake.targets[0].provider_id == "openrouter"
    assert _stored(analysis_id)[0] == "done"
