import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

# Keep repository root first so imports like `backend.settings` resolve.
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Also expose backend modules for runtime-style imports (`import state`, `from core...`).
if str(BACKEND_DIR) not in sys.path:
    sys.path.append(str(BACKEND_DIR))

# ---------------------------------------------------------------------------
# Sandbox the whole test session
# ---------------------------------------------------------------------------
# backend/config.py resolves STORAGE_ROOT from $HOME at *import* time, and
# backend/settings.py writes settings.json next to it. Redirecting HOME here —
# before any test module imports backend code — keeps the suite from reading or
# writing the developer's real ~/.amicoscript library.
_TEST_HOME = tempfile.mkdtemp(prefix="amicoscript-test-home-")
os.environ["HOME"] = _TEST_HOME
os.environ["USERPROFILE"] = _TEST_HOME
# Empty owner short-circuits the GitHub release poller, so no test touches the
# network on app startup.
os.environ.setdefault("GITHUB_OWNER", "")
os.environ.setdefault("AMICOSCRIPT_EMBEDDED_WATCHER", "off")


@pytest.fixture()
def api_app():
    """The FastAPI app with a clean database, wired to the sandboxed HOME."""
    import db
    import state
    from config import ensure_storage_dirs
    from sqlmodel import SQLModel

    import main  # noqa: F401 — importing wires the routers onto the app

    ensure_storage_dirs()
    db.init_db()
    _truncate_all(db, SQLModel)
    state.jobs.clear()
    return main.app


@pytest.fixture()
def client(api_app):
    """TestClient with startup/shutdown run, as a real request would see it.

    The peer address is pinned to loopback so requests look the way they do on
    a normal local install; tests that need a remote caller override it per
    request with ``extensions={"client": (...)}``.
    """
    from fastapi.testclient import TestClient

    with TestClient(api_app, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


@pytest.fixture()
def remote_client(api_app):
    """A client whose peer address is a public IP — i.e. not on this machine.

    The peer address is what the auth layer reads, so this is the closest a
    test can get to "someone found the Traefik hostname".
    """
    from fastapi.testclient import TestClient

    with TestClient(api_app, client=("203.0.113.7", 51234)) as test_client:
        yield test_client


@pytest.fixture()
def no_auth(monkeypatch):
    """Disable authentication for tests that are not about authentication."""
    monkeypatch.setenv("AMICOSCRIPT_AUTH", "off")


@pytest.fixture(autouse=True)
def _clear_diarization_cache():
    """No test may inherit the pipeline another one loaded.

    The diarization pipeline is cached for the life of the process, which is
    what makes it fast in production and what would otherwise let one test's
    stubbed pyannote answer the next test's call.
    """
    import state

    state._cached_diarization = None
    state._cached_diarization_device = None
    state._cached_diarization_key = None
    yield
    state._cached_diarization = None
    state._cached_diarization_device = None
    state._cached_diarization_key = None


@pytest.fixture()
def idle_worker(monkeypatch):
    """Keep the background worker from consuming jobs this test queues.

    The worker loop is live in tests — the `client` fixture runs startup — so a
    job put on the queue really is picked up, and immediately fails, because
    there is no Whisper model here. A test that queues something and then
    asserts it is *queued* is racing that: under the load of a full suite run
    the worker wins, and the recording already reads 'error'.

    Use this in tests about queueing. Tests about processing should not.
    """
    import core.transcription

    monkeypatch.setattr(core.transcription, "_process_job", lambda job_id: None)


@pytest.fixture()
def clean_settings():
    """Empty settings.json before and after a test that writes to it."""
    from settings import _save_settings

    _save_settings({})
    yield
    _save_settings({})


def _truncate_all(db_module, sqlmodel_base) -> None:
    from sqlalchemy import text

    with db_module.engine.begin() as conn:
        for table in reversed(sqlmodel_base.metadata.sorted_tables):
            conn.execute(text(f"DELETE FROM {table.name}"))


@pytest.fixture()
def make_recording():
    """Insert a recording (+ optional transcript) and return its id."""
    import json
    import uuid

    from db import new_session
    from models import Recording, Transcript

    def _make(
        filename: str = "interview.mp3",
        status: str = "done",
        segments: list | None = None,
        source: str = "upload",
        file_path: str = "",
        folder_id: str | None = None,
    ) -> str:
        recording_id = str(uuid.uuid4())
        with new_session() as session:
            session.add(
                Recording(
                    id=recording_id,
                    filename=filename,
                    file_path=file_path or f"/tmp/{recording_id}.mp3",
                    status=status,
                    source=source,
                    folder_id=folder_id,
                    duration=12.5,
                    transcription_options=json.dumps({"model": "small"}),
                )
            )
            if segments is not None:
                result = {
                    "language": "en",
                    "duration": 12.5,
                    "num_segments": len(segments),
                    "speakers": sorted({s["speaker"] for s in segments if s.get("speaker")}),
                    "segments": segments,
                }
                session.add(
                    Transcript(
                        recording_id=recording_id,
                        full_text=" ".join(s.get("text", "") for s in segments),
                        json_data=json.dumps(result),
                    )
                )
            session.commit()
        return recording_id

    return _make


@pytest.fixture()
def sample_segments():
    return [
        {"id": 0, "start": 0.0, "end": 3.5, "text": "Welcome to the quarterly review.",
         "speaker": "SPEAKER_00"},
        {"id": 1, "start": 3.5, "end": 8.0, "text": "Revenue is up eleven percent.",
         "speaker": "SPEAKER_01"},
        {"id": 2, "start": 8.0, "end": 12.5, "text": "We should ship the migration by Friday.",
         "speaker": "SPEAKER_00"},
    ]
