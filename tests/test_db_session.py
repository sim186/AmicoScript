"""Tests for db session lifecycle — commit on success, rollback on error."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from unittest.mock import MagicMock


def test_get_session_commits_on_success(monkeypatch):
    """get_session must commit when no exception is raised."""
    import db

    mock_session = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_session)
    mock_cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(db, "Session", MagicMock(return_value=mock_cm))

    gen = db.get_session()
    sess = next(gen)
    assert sess is mock_session
    try:
        next(gen)
    except StopIteration:
        pass
    mock_session.commit.assert_called_once()
    mock_session.rollback.assert_not_called()


def test_get_session_rolls_back_on_error(monkeypatch):
    """get_session must rollback when an exception propagates."""
    import db

    mock_session = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_session)
    mock_cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(db, "Session", MagicMock(return_value=mock_cm))

    gen = db.get_session()
    next(gen)
    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("oops"))
    mock_session.rollback.assert_called_once()
    mock_session.commit.assert_not_called()
