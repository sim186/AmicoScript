"""Unit tests for the export formatters, including the new VTT and CSV."""
import csv
import io

import pytest

from exports import (
    _format_csv,
    _format_json,
    _format_md,
    _format_srt,
    _format_txt,
    _format_vtt,
    render_export,
)

RESULT = {
    "language": "en",
    "duration": 65.25,
    "speakers": ["Ada", "Grace"],
    "segments": [
        {"id": 0, "start": 0.0, "end": 3.5, "text": "Welcome everyone.", "speaker": "Ada"},
        {"id": 1, "start": 3.5, "end": 61.125, "text": "Thanks for having me.", "speaker": "Grace"},
        {"id": 2, "start": 61.125, "end": 65.25, "text": "Let's begin.", "speaker": "Ada"},
    ],
}


# --- WebVTT -----------------------------------------------------------------


def test_vtt_starts_with_the_required_header():
    assert _format_vtt(RESULT).startswith("WEBVTT\n")


def test_vtt_uses_dot_separated_timestamps():
    out = _format_vtt(RESULT)
    assert "00:00:00.000 --> 00:00:03.500" in out
    assert "00:01:01.125 --> 00:01:05.250" in out
    assert ",500" not in out  # that is SRT's separator, not VTT's


def test_vtt_marks_speakers_as_voice_spans():
    assert "<v Ada>Welcome everyone." in _format_vtt(RESULT)


def test_vtt_omits_the_voice_tag_when_there_is_no_speaker():
    out = _format_vtt({"segments": [{"start": 0, "end": 1, "text": "Anonymous line."}]})
    assert "<v" not in out
    assert "Anonymous line." in out


def test_vtt_skips_empty_segments():
    out = _format_vtt({"segments": [
        {"start": 0, "end": 1, "text": "   ", "speaker": "Ada"},
        {"start": 1, "end": 2, "text": "Real text.", "speaker": "Ada"},
    ]})
    assert out.count("-->") == 1


def test_vtt_neutralises_a_cue_terminator_inside_the_text():
    """'-->' inside a caption would start a new cue and corrupt the file."""
    out = _format_vtt({"segments": [{"start": 0, "end": 1, "text": "a --> b", "speaker": ""}]})
    assert out.count("-->") == 1


def test_vtt_voice_name_cannot_break_out_of_the_tag():
    out = _format_vtt({"segments": [
        {"start": 0, "end": 1, "text": "hi", "speaker": "<script>x</script>"},
    ]})
    assert "<script>" not in out


# --- CSV --------------------------------------------------------------------


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_csv_has_a_header_and_one_row_per_segment():
    rows = _rows(_format_csv(RESULT))
    assert rows[0] == [
        "index", "start", "end", "start_time", "end_time",
        "speaker", "text", "translation", "edited",
    ]
    assert len(rows) == 4


def test_csv_carries_times_in_both_forms():
    rows = _rows(_format_csv(RESULT))
    assert rows[1][1] == "0.000"
    assert rows[2][3] == "0:03"
    assert rows[3][4] == "1:05"


def test_csv_quotes_text_containing_commas():
    out = _format_csv({"segments": [
        {"start": 0, "end": 1, "text": "one, two, three", "speaker": ""},
    ]})
    assert '"one, two, three"' in out
    assert _rows(out)[1][6] == "one, two, three"


@pytest.mark.parametrize("payload", ["=1+1", "+SUM(A1)", "-2+3", "@import"])
def test_csv_defuses_spreadsheet_formulas(payload):
    """Transcript text is untrusted; Excel executes a leading '=' on open."""
    out = _format_csv({"segments": [{"start": 0, "end": 1, "text": payload, "speaker": ""}]})
    assert _rows(out)[1][6] == "'" + payload


def test_csv_marks_edited_segments():
    out = _format_csv({"segments": [
        {"start": 0, "end": 1, "text": "fixed", "speaker": "", "edited": True},
        {"start": 1, "end": 2, "text": "untouched", "speaker": ""},
    ]})
    rows = _rows(out)
    assert rows[1][8] == "yes"
    assert rows[2][8] == ""


def test_csv_includes_translations():
    out = _format_csv({"segments": [
        {"start": 0, "end": 1, "text": "ciao", "speaker": "", "translation": "hello"},
    ]})
    assert _rows(out)[1][7] == "hello"


def test_csv_of_an_empty_transcript_is_just_the_header():
    assert len(_rows(_format_csv({"segments": []}))) == 1


# --- registry ---------------------------------------------------------------


@pytest.mark.parametrize(
    "fmt,media,ext",
    [
        ("json", "application/json", "json"),
        ("srt", "text/plain", "srt"),
        ("vtt", "text/vtt", "vtt"),
        ("txt", "text/plain", "txt"),
        ("csv", "text/csv", "csv"),
        ("md", "text/markdown", "md"),
    ],
)
def test_render_export_covers_every_supported_format(fmt, media, ext):
    content, media_type, extension = render_export(fmt, RESULT, title="Standup")
    assert isinstance(content, bytes) and content
    assert media in media_type
    assert extension == ext


def test_csv_is_encoded_with_a_bom_for_excel():
    content, _, _ = render_export("csv", {"segments": [
        {"start": 0, "end": 1, "text": "caffè corretto", "speaker": ""},
    ]})
    assert content.startswith(b"\xef\xbb\xbf")
    assert "caffè".encode("utf-8") in content


def test_render_export_rejects_an_unknown_format():
    with pytest.raises(ValueError) as excinfo:
        render_export("pdf", RESULT)
    assert "vtt" in str(excinfo.value)  # the message lists what is available


# --- existing formats keep working -----------------------------------------


def test_srt_is_unchanged_by_the_refactor():
    out = _format_srt(RESULT)
    assert out.startswith("1\n00:00:00,000 --> 00:00:03,500\n[Ada] Welcome everyone.")


def test_txt_groups_consecutive_speaker_runs():
    assert "Ada:" in _format_txt(RESULT)


def test_md_includes_the_metadata_line():
    out = _format_md(RESULT, title="Standup", date="2026-08-05")
    assert out.startswith("# Standup")
    assert "**Speakers:** Ada, Grace" in out
    assert "**Date:** 2026-08-05" in out


def test_json_round_trips():
    import json

    assert json.loads(_format_json(RESULT))["duration"] == 65.25
