"""Job logs are a bounded ring, so a chatty job cannot grow without limit.

The bound used to be applied on the first append, because the four job
factories seeded `logs` with a plain list and `append_job_log` had to convert
it. core.jobs.create_job now establishes it at construction, so these tests
build their jobs the way the application does rather than by hand — a job dict
assembled in a test would only prove that the repair still works.
"""
from collections import deque

import state
from api.routes.transcription import get_job_logs
from core.job_helpers import append_job_log
from core.jobs import JOB_LOG_LIMIT, create_job


def test_logs_are_capped():
    job_id = create_job(original_filename="chatty.mp3")
    for i in range(JOB_LOG_LIMIT + 200):
        append_job_log(job_id, "INFO", f"msg {i}")

    logs = state.jobs[job_id]["logs"]
    assert len(logs) == JOB_LOG_LIMIT
    # The oldest lines are the ones dropped, not the newest.
    assert logs[-1]["message"] == f"msg {JOB_LOG_LIMIT + 199}"


def test_a_new_job_starts_with_a_bounded_deque():
    """The invariant holds before anything is logged, not after the first line."""
    job_id = create_job()
    logs = state.jobs[job_id]["logs"]

    assert isinstance(logs, deque)
    assert logs.maxlen == JOB_LOG_LIMIT
    assert len(logs) == 0


def test_logs_preserve_order():
    job_id = create_job()
    for i in range(5):
        append_job_log(job_id, "INFO", f"msg {i}")

    messages = [e["message"] for e in state.jobs[job_id]["logs"]]
    assert messages == [f"msg {i}" for i in range(5)]


def test_get_job_logs_returns_the_tail():
    job_id = create_job()
    state.jobs[job_id]["status"] = "done"
    for i in range(3):
        append_job_log(job_id, "INFO", f"msg {i}")

    result = get_job_logs(job_id, limit=1)

    assert [entry["message"] for entry in result["logs"]] == ["msg 2"]
