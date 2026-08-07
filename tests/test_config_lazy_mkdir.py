"""config.py must not touch the disk at import time.

Creating the storage directories on import means merely importing the backend —
to read a version, to run a linter, to collect tests — silently creates
~/.amicoscript on the machine doing it. ensure_storage_dirs() is called from
startup instead, where it is a decision rather than a side effect.
"""
import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def reimportable_config():
    """Re-import config, then put the original module object back.

    Modules that did `import config` hold a reference to whichever object was
    in sys.modules when *they* were imported. Leaving a fresh one behind splits
    the module in two: the app reads its copy, a later monkeypatch writes to
    the other, and the patch appears to do nothing. Restoring it keeps that
    from leaking into the rest of the session.
    """
    original = sys.modules.get("config")
    original_pkg = sys.modules.get("backend.config")
    sys.modules.pop("config", None)
    sys.modules.pop("backend.config", None)
    try:
        yield
    finally:
        if original is not None:
            sys.modules["config"] = original
        if original_pkg is not None:
            sys.modules["backend.config"] = original_pkg


def test_config_import_does_not_mkdir(reimportable_config):
    mkdir_calls = []

    with patch.object(Path, "mkdir", side_effect=lambda **kw: mkdir_calls.append("mkdir")):
        importlib.import_module("config")

    assert mkdir_calls == []


def test_ensure_storage_dirs_creates_dirs(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "STORAGE_ROOT", tmp_path / "data")
    monkeypatch.setattr(config, "RECORDINGS_DIR", tmp_path / "data" / "recordings")

    config.ensure_storage_dirs()

    assert (tmp_path / "data").exists()
    assert (tmp_path / "data" / "recordings").exists()
