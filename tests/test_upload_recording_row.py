"""The Recording row is the job's destination, not a nice-to-have.

_create_recording_row used to end in `except Exception: pass`. When the insert
failed the upload still returned 200, the worker transcribed the whole file,
and sync_job_to_db found no row to attach the transcript to and returned
quietly. The user waited out a full transcription and got nothing — no
transcript, no error, nothing in the log.
"""
import pytest

import state
from config import RECORDINGS_DIR


@pytest.fixture()
def failing_recording_insert(monkeypatch):
    """Make the Recording insert fail the way a locked or full database would."""
    import api.routes.transcription as transcription_routes

    def _boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(transcription_routes, "_create_recording_row", _boom)


def test_upload_fails_loudly_when_the_recording_cannot_be_saved(
    client, failing_recording_insert, idle_worker
):
    before = set(state.jobs)

    resp = client.post(
        "/api/transcribe",
        files={"file": ("meeting.mp3", b"ID3audio", "audio/mpeg")},
    )

    assert resp.status_code == 500
    assert "Could not save this recording" in resp.json()["detail"]
    # And no job was queued to transcribe into a row that does not exist.
    assert set(state.jobs) == before


def test_a_rejected_upload_leaves_no_orphan_audio(
    client, failing_recording_insert, idle_worker
):
    """The file is already in managed storage by the time the insert runs."""
    before = set(p.name for p in RECORDINGS_DIR.iterdir()) if RECORDINGS_DIR.exists() else set()

    client.post("/api/transcribe", files={"file": ("meeting.mp3", b"ID3audio", "audio/mpeg")})

    after = set(p.name for p in RECORDINGS_DIR.iterdir()) if RECORDINGS_DIR.exists() else set()
    assert after == before


def test_a_healthy_upload_still_creates_the_row_and_the_job(client, idle_worker):
    from db import new_session
    from models import Recording

    resp = client.post(
        "/api/transcribe",
        files={"file": ("meeting.mp3", b"ID3audio", "audio/mpeg")},
    )

    assert resp.status_code == 200
    payload = resp.json()
    with new_session() as session:
        assert session.get(Recording, payload["recording_id"]) is not None
    assert state.jobs[payload["job_id"]]["recording_id"] == payload["recording_id"]
