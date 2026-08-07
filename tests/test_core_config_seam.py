"""core/ can be told its configuration instead of reading it off disk.

Everything under core/ used to call the settings store itself, so exercising a
decision it makes meant redirecting $HOME and writing a settings.json — which
is what the whole test session does in conftest. The entry points that depend
on settings now accept them, defaulting to the store for the app's own callers.
"""
import state
from core.analysis_jobs import create_analysis_job, maybe_queue_auto_summary
from core.requeue import build_job

CLOUD_CONFIG = {
    "llm_provider": "openrouter",
    "llm_base_url": "https://openrouter.ai/api",
    "llm_model_name": "anthropic/claude-sonnet-4",
    "llm_api_key": "sk-test",
    "llm_allow_cloud": False,
    "llm_context_tokens": 8192,
    "llm_max_output_tokens": 1024,
    "llm_embedding_model": "",
}


def test_an_analysis_job_runs_against_the_config_it_is_given(client):
    """Not against whatever happens to be saved when the worker gets to it."""
    job_id, _ = create_analysis_job(
        recording_id="rec-1",
        analysis_type="summary",
        transcript_full_text="hello",
        llm_config={**CLOUD_CONFIG, "llm_model_name": "given-model"},
        enqueue=False,
    )

    assert state.jobs[job_id]["options"]["llm_model_name"] == "given-model"


def test_auto_summary_can_be_told_it_is_switched_off(make_recording):
    """No settings file is read to answer this."""
    rec_id = make_recording(source="meeting")

    assert maybe_queue_auto_summary(rec_id, enabled=False, llm_config=CLOUD_CONFIG) == ""


def test_auto_summary_refuses_a_cloud_provider_without_consent(
    api_app, make_recording, sample_segments
):
    """The refusal is the config's doing, so the config has to be reachable."""
    rec_id = make_recording(source="meeting", segments=sample_segments)

    assert maybe_queue_auto_summary(rec_id, enabled=True, llm_config=CLOUD_CONFIG) == ""


def test_auto_summary_queues_once_the_config_allows_it(
    api_app, make_recording, sample_segments
):
    """The mirror of the test above — same call, consent flipped on.

    Driven through a running loop because that is what the worker has: without
    one the submit fails and this would return "" for a reason that has nothing
    to do with the config.
    """
    import asyncio

    rec_id = make_recording(source="meeting", segments=sample_segments)

    async def _run():
        state._init_queue()
        state.event_loop = asyncio.get_running_loop()
        job_id = await asyncio.to_thread(
            maybe_queue_auto_summary,
            rec_id,
            enabled=True,
            llm_config={**CLOUD_CONFIG, "llm_allow_cloud": True},
        )
        await asyncio.sleep(0)  # let call_soon_threadsafe land
        return job_id, state.JOB_QUEUE.get_nowait()

    job_id, queued = asyncio.run(_run())

    assert job_id and queued == job_id
    assert state.jobs[job_id]["options"]["llm_model_name"] == CLOUD_CONFIG["llm_model_name"]


def test_a_requeued_job_can_be_given_its_token(api_app):
    job_id = build_job("rec-9", "a.mp3", "/tmp/a.mp3", {"model": "small"}, hf_token="hf_given")

    assert state.jobs[job_id]["options"]["hf_token"] == "hf_given"
