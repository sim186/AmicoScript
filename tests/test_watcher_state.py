"""core.watcher_state — the single owner of "when did this recording start"."""
import time

from db import new_session


def test_get_started_at_is_none_when_nothing_recording(api_app):
    from core.watcher_state import get_started_at

    with new_session() as session:
        assert get_started_at(session) is None


def test_report_recording_true_stamps_and_persists_started_at(api_app):
    from core.watcher_state import get_started_at, report_recording

    before = time.time()
    with new_session() as session:
        started_at = report_recording(session, True)
    after = time.time()

    assert before <= started_at <= after
    with new_session() as session:
        assert get_started_at(session) == started_at


def test_report_recording_true_again_does_not_renew_started_at(api_app):
    from core.watcher_state import report_recording

    with new_session() as session:
        first = report_recording(session, True)

    with new_session() as session:
        second = report_recording(session, True)

    assert second == first


def test_report_recording_false_clears_started_at(api_app):
    from core.watcher_state import get_started_at, report_recording

    with new_session() as session:
        report_recording(session, True)

    with new_session() as session:
        report_recording(session, False)

    with new_session() as session:
        assert get_started_at(session) is None
