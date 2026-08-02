"""Tests for export formatters — safe segments access and edge cases."""
from backend.exports import _format_json, _format_md, _format_srt, _format_txt


def _make_result(**kwargs):
    base = {
        "language": "en",
        "duration": 10.0,
        "num_segments": 1,
        "speakers": [],
        "segments": [{"start": 0.0, "end": 5.0, "text": "Hello world", "speaker": ""}],
    }
    base.update(kwargs)
    return base


def test_format_srt_normal():
    out = _format_srt(_make_result())
    assert "00:00:00,000 --> 00:00:05,000" in out
    assert "Hello world" in out


def test_format_srt_empty_segments():
    out = _format_srt(_make_result(segments=[]))
    assert out == ""


def test_format_srt_missing_segments_key():
    result = {"language": "en", "duration": 5.0}
    out = _format_srt(result)
    assert out == ""


def test_format_txt_empty_segments():
    out = _format_txt(_make_result(segments=[]))
    assert out == ""


def test_format_txt_missing_segments_key():
    out = _format_txt({"language": "en"})
    assert out == ""


def test_format_md_missing_segments_key():
    out = _format_md({"language": "en", "duration": 5.0})
    assert out.startswith("# Transcript")
    assert "**Language:** EN" in out


def test_format_srt_with_speaker():
    result = _make_result(segments=[
        {"start": 0.0, "end": 2.0, "text": "Hi", "speaker": "Speaker 1"},
    ])
    out = _format_srt(result)
    assert "[Speaker 1]" in out


def test_format_json_roundtrip():
    import json
    result = _make_result()
    out = _format_json(result)
    parsed = json.loads(out)
    assert parsed["language"] == "en"
