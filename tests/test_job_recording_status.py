"""Only a job that owns the recording may write the recording's status.

`sync_job_to_db` used to copy whatever status the job was carrying onto the
Recording row, for every kind of job. That is right for a transcription — its
status *is* the recording's — and wrong for the other two, which never owned
the row. A summary the LLM refused, or an analysis the user cancelled, left the
transcript perfectly intact and the recording reading `error`: the library
listed it as failed, and because `error` is RETRYABLE the UI offered to
transcribe the whole thing again.
"""
import pytest

import state
from core.job_helpers import handle_job_error, sync_job_to_db
from core.jobs import OWNS_RECORDING_STATUS, JobType, create_job

pytestmark = pytest.mark.usefixtures("api_app")


def _status_of(recording_id: str) -> str:
    from db import new_session
    from models import Recording

    with new_session() as session:
        return session.get(Recording, recording_id).status


def _fail(job_id: str) -> None:
    handle_job_error(job_id, RuntimeError("the model refused"))


# --- the jobs that do not own the recording ----------------------------------


@pytest.mark.parametrize("job_type", ["analysis", "translate"])
def test_a_failed_job_that_does_not_own_the_recording_leaves_its_status(
    job_type, make_recording
):
    recording_id = make_recording(status="done")
    job_id = create_job(job_type=job_type, recording_id=recording_id, analysis_id="a1")

    _fail(job_id)

    assert _status_of(recording_id) == "done"


@pytest.mark.parametrize("job_type", ["analysis", "translate"])
def test_cancelling_such_a_job_leaves_the_recording_alone_too(job_type, make_recording):
    """cancel_job terminalises the job itself, then syncs — same path, same rule."""
    recording_id = make_recording(status="done")
    job_id = create_job(job_type=job_type, recording_id=recording_id)
    state.jobs[job_id]["status"] = "cancelled"

    sync_job_to_db(job_id)

    assert _status_of(recording_id) == "done"


def test_a_finished_analysis_does_not_mark_the_recording_done_either(make_recording):
    """The rule is symmetric: the row is not this job's to describe, ever."""
    recording_id = make_recording(status="error")
    job_id = create_job(job_type="analysis", recording_id=recording_id, analysis_id="a1")
    state.jobs[job_id]["status"] = "done"

    sync_job_to_db(job_id)

    assert _status_of(recording_id) == "error"


# --- the jobs that do -------------------------------------------------------


@pytest.mark.parametrize("job_type", ["transcribe", "download_transcribe"])
def test_a_failed_transcription_still_marks_the_recording_failed(
    job_type, make_recording
):
    recording_id = make_recording(status="transcribing")
    job_id = create_job(job_type=job_type, recording_id=recording_id)

    _fail(job_id)

    assert _status_of(recording_id) == "error"


def test_a_finished_transcription_still_marks_the_recording_done(make_recording):
    recording_id = make_recording(status="transcribing")
    job_id = create_job(job_type="transcribe", recording_id=recording_id)
    state.jobs[job_id]["status"] = "done"

    sync_job_to_db(job_id)

    assert _status_of(recording_id) == "done"


# --- and the rule stays exhaustive ------------------------------------------


def test_every_job_type_is_decided_one_way_or_the_other():
    """So the next job type added cannot silently inherit the wrong answer.

    A new type left out of OWNS_RECORDING_STATUS is a deliberate 'does not own
    it'; this only asks that the choice was made in the enum's company.
    """
    undecided = {t for t in JobType if t not in OWNS_RECORDING_STATUS} - {
        JobType.TRANSLATE,
        JobType.ANALYSIS,
    }

    assert undecided == set()


def test_the_default_job_type_owns_the_recording():
    """`job.get("type", JobType.TRANSCRIBE)` is the fallback at every read site.

    Expired jobs and rows written before `type` existed have no key, and the
    only kind of job that predates it is a transcription.
    """
    assert JobType.TRANSCRIBE in OWNS_RECORDING_STATUS
