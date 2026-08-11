"""Unit tests for the export formatters, including the new VTT and CSV."""
import csv
import io

import pytest

from exports import (
    _format_csv,
    _format_json,
    _format_md,
    format_md_bulk,
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


def test_md_without_frontmatter_keeps_the_inline_metadata_line():
    """The bulk export's per-transcript sections still use this form."""
    out = _format_md(RESULT, title="Standup", date="2026-08-05", frontmatter=False)
    assert out.startswith("# Standup")
    assert "**Speakers:** Ada, Grace" in out
    assert "**Date:** 2026-08-05" in out


# --- Markdown frontmatter ---------------------------------------------------


def _frontmatter_of(text: str) -> dict:
    """Parse the leading YAML block. Enough of a parser for these tests."""
    assert text.startswith("---\n")
    block = text.split("---\n", 2)[1]
    data, key = {}, None
    for line in block.splitlines():
        if line.startswith("  - "):
            data.setdefault(key, []).append(line[4:].strip().strip('"'))
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"')
            if value:
                data[key] = value
    return data


def test_md_leads_with_yaml_frontmatter():
    fm = _frontmatter_of(_format_md(RESULT, title="Standup", date="2026-08-05"))
    assert fm["title"] == "Standup"
    assert fm["date"] == "2026-08-05"
    assert fm["speakers"] == ["Ada", "Grace"]
    assert fm["language"] == "en"


def test_md_frontmatter_carries_both_forms_of_the_duration():
    fm = _frontmatter_of(_format_md(RESULT, title="Standup"))
    assert fm["duration"] == "1:05"
    assert fm["duration_seconds"] == "65.25"


def test_md_frontmatter_carries_recording_metadata():
    fm = _frontmatter_of(_format_md(
        RESULT,
        title="Standup",
        meta={"tags": ["team sync", "#weekly"], "folder": "Work", "source": "upload", "model": "small"},
    ))
    # Obsidian tags cannot contain spaces, and the '#' belongs in the body.
    assert fm["tags"] == ["team-sync", "weekly"]
    assert fm["folder"] == "Work"
    assert fm["model"] == "small"


def test_md_frontmatter_omits_keys_it_has_no_value_for():
    out = _format_md({"segments": [{"start": 0, "end": 1, "text": "hi"}]}, title="Note")
    assert "model:" not in out
    assert "tags:" not in out
    assert "speakers:" not in out
    assert "language:" not in out


@pytest.mark.parametrize("title", [
    'He said "hello"',          # would end the scalar
    "- not a list item",        # a bare '-' starts a sequence
    "key: value",               # a bare ': ' starts a mapping
    "no",                       # YAML 1.1 reads this as False
    "C:\\Users\\ada",           # backslashes need escaping
])
def test_md_frontmatter_survives_a_hostile_title(title):
    """Titles come from the user's filename or alias — they are not trusted."""
    yaml = pytest.importorskip("yaml")
    out = _format_md(RESULT, title=title, date="2026-08-05")
    parsed = yaml.safe_load(out.split("---\n")[1])
    assert parsed["title"] == title


def test_md_frontmatter_drops_a_newline_smuggled_into_a_title():
    out = _format_md(RESULT, title="Standup\nevil: true")
    assert "evil: true" in out.split("---\n")[1].splitlines()[0]  # same line, inside the quotes
    assert _frontmatter_of(out).get("evil") is None


def test_md_wikilinks_are_off_by_default():
    out = _format_md(RESULT, title="Standup")
    assert "[[" not in out


def test_md_wikilinks_link_speakers_in_the_body_and_the_frontmatter():
    out = _format_md(RESULT, title="Standup", wikilinks=True)
    assert "**[[Ada]]** · `0:00`" in out
    assert _frontmatter_of(out)["speakers"] == ["[[Ada]]", "[[Grace]]"]


def test_md_wikilinks_do_not_reach_tags():
    """Obsidian tags are not links; '[[#weekly]]' would be a broken one."""
    out = _format_md(RESULT, title="Standup", meta={"tags": ["weekly"]}, wikilinks=True)
    assert _frontmatter_of(out)["tags"] == ["weekly"]


def test_render_export_passes_metadata_through_to_markdown():
    content, _, _ = render_export(
        "md", RESULT, title="Standup", meta={"tags": ["weekly"]}, wikilinks=True
    )
    text = content.decode("utf-8")
    assert "[[Ada]]" in text
    assert "- \"weekly\"" in text


# --- bulk markdown ----------------------------------------------------------


def _bulk(n: int) -> list[dict]:
    return [
        {
            "title": f"Standup {i}",
            "date": f"2026-08-0{i}",
            "result": RESULT,
            "meta": {"tags": [f"day-{i}"]},
        }
        for i in range(1, n + 1)
    ]


def test_bulk_export_of_one_recording_is_just_that_note():
    out = format_md_bulk(_bulk(1))
    assert out.startswith("---\n")
    assert _frontmatter_of(out)["title"] == "Standup 1"
    assert "# Table of Contents" not in out


def test_bulk_export_has_exactly_one_frontmatter_block():
    """A second '---' block mid-file is body text, not properties."""
    out = format_md_bulk(_bulk(3))
    assert out.startswith("---\n")
    body = out.split("---\n", 2)[2]
    assert not any(line == "title:" for line in body.splitlines())
    assert "# Table of Contents" in body


def test_bulk_frontmatter_summarises_the_whole_collection():
    fm = _frontmatter_of(format_md_bulk(_bulk(3)))
    assert fm["recordings"] == "3"
    assert fm["date_from"] == "2026-08-01"
    assert fm["date"] == "2026-08-03"
    assert fm["speakers"] == ["Ada", "Grace"]
    assert fm["tags"] == ["day-1", "day-2", "day-3"]


def test_bulk_sections_keep_their_inline_metadata():
    out = format_md_bulk(_bulk(2))
    assert out.count("**Duration:** 1:05") == 2


def test_json_round_trips():
    import json

    assert json.loads(_format_json(RESULT))["duration"] == 65.25
