from contextlib import contextmanager

import state
from core import job_helpers


class _FakeRec:
    def __init__(self):
        self.status = "queued"
        self.duration = None


class _FakeExecResult:
    def first(self):
        return None


class _FakeSession:
    def __init__(self):
        self.rec = _FakeRec()
        self.committed = False

    def get(self, _model, _id):
        return self.rec

    def exec(self, _stmt):
        return _FakeExecResult()

    def add(self, _obj):
        return None

    def commit(self):
        self.committed = True


def test_sync_job_to_db_retries_then_succeeds(monkeypatch):
    job_id = "job-retry"
    state.jobs[job_id] = {
        "recording_id": "rec-1",
        "status": "done",
        "result": {"duration": 2.5, "segments": [{"text": "hello"}]},
    }

    attempts = {"count": 0}
    final_session = _FakeSession()

    @contextmanager
    def _fake_new_session():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("db temporarily unavailable")
        yield final_session

    monkeypatch.setattr(job_helpers, "new_session", _fake_new_session)
    monkeypatch.setattr(job_helpers.time, "sleep", lambda _s: None)

    job_helpers.sync_job_to_db(job_id, retries=3)

    assert attempts["count"] == 3
    assert final_session.committed is True
