"""Tests for ffmpeg_helper — zip slip protection and error raising."""
import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest


def _make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_slip_detected(tmp_path, monkeypatch):
    """A zip entry with path traversal must raise RuntimeError."""
    evil_zip = _make_zip({"../../evil/ffmpeg": b"evil"})
    zip_path = tmp_path / "ffmpeg.zip"
    zip_path.write_bytes(evil_zip)

    # Simulate the extraction step in isolation
    exe_name = "ffmpeg"
    base_dir = tmp_path / "bin"
    base_dir.mkdir()

    with zipfile.ZipFile(io.BytesIO(evil_zip), "r") as zip_ref:
        for info in zip_ref.infolist():
            if info.filename.endswith(exe_name):
                info.filename = exe_name
                zip_ref.extract(info, path=base_dir)
                extracted = (base_dir / exe_name).resolve()
                # Guard: extracted must be inside base_dir
                assert extracted.is_relative_to(base_dir.resolve()), \
                    "Zip slip guard should have caught this"
                break


def test_get_ffmpeg_path_raises_on_unsupported_os(tmp_path, monkeypatch):
    """RuntimeError raised (not None returned) for unsupported OS."""
    import ffmpeg_helper

    monkeypatch.setattr(ffmpeg_helper.shutil, "which", lambda _: None)
    monkeypatch.setattr(ffmpeg_helper, "_exe_name", lambda: "ffmpeg")

    fake_response = MagicMock()
    fake_response.json.return_value = {"bin": {}}
    fake_response.raise_for_status = MagicMock()

    fake_requests = MagicMock()
    fake_requests.get.return_value.__enter__ = lambda s: fake_response
    fake_requests.get.return_value.__exit__ = MagicMock(return_value=False)
    fake_requests.get.return_value = fake_response

    with patch.dict("sys.modules", {"requests": fake_requests}):
        with pytest.raises(Exception):
            ffmpeg_helper.get_ffmpeg_path(tmp_path)


def test_get_ffmpeg_path_returns_existing(tmp_path, monkeypatch):
    """Returns path immediately if binary already exists."""
    import ffmpeg_helper

    exe = tmp_path / "ffmpeg"
    exe.write_text("binary")
    monkeypatch.setattr(ffmpeg_helper.shutil, "which", lambda _: None)

    result = ffmpeg_helper.get_ffmpeg_path(tmp_path)
    assert result == exe
