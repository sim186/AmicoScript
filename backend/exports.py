"""Export formatters for transcription results.

Each function takes the result dict produced by the pipeline and returns
a UTF-8 string ready to be sent as a file download.
"""
import csv
import io
import json


# ---------------------------------------------------------------------------
# Time formatters
# ---------------------------------------------------------------------------

def _ms(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm (SRT timestamp format)."""
    ms = int(round(seconds * 1000))
    h = ms // 3_600_000
    ms %= 3_600_000
    m = ms // 60_000
    ms %= 60_000
    s = ms // 1_000
    ms %= 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_ts(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm (WebVTT uses a dot, SRT a comma)."""
    return _ms(seconds).replace(",", ".")


def _ts(seconds: float) -> str:
    """Format seconds as M:SS for human-readable display."""
    total = int(seconds)
    m = total // 60
    s = total % 60
    return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Format functions
# ---------------------------------------------------------------------------

def _format_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)


def _format_srt(result: dict) -> str:
    lines = []
    for i, seg in enumerate(result.get("segments", []), 1):
        speaker_prefix = f"[{seg['speaker']}] " if seg.get("speaker") else ""
        lines.append(str(i))
        lines.append(f"{_ms(seg['start'])} --> {_ms(seg['end'])}")
        lines.append(f"{speaker_prefix}{seg['text']}")
        lines.append("")
    return "\n".join(lines)


def _format_vtt(result: dict) -> str:
    """WebVTT — the subtitle format browsers accept in <track>.

    Speakers become voice spans (``<v Name>``), which players and screen
    readers understand, instead of being baked into the caption text.
    """
    lines = ["WEBVTT", ""]
    for i, seg in enumerate(result.get("segments", []), 1):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker") or ""
        lines.append(str(i))
        lines.append(f"{_vtt_ts(seg['start'])} --> {_vtt_ts(seg['end'])}")
        # A cue payload may not contain a blank line, and '-->' would start a
        # new cue, so both are neutralised.
        body = text.replace("\n\n", "\n").replace("-->", "→")
        lines.append(f"<v {_vtt_voice(speaker)}>{body}" if speaker else body)
        lines.append("")
    return "\n".join(lines)


def _vtt_voice(speaker: str) -> str:
    """Sanitise a speaker name for use inside a <v ...> cue tag."""
    return speaker.replace(">", "").replace("<", "").strip() or "Speaker"


# Leading characters that spreadsheets interpret as the start of a formula.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Defuse spreadsheet formula injection in transcript text.

    Transcript text is untrusted (it comes from whatever was said, or from a
    downloaded video's captions). A cell starting with '=' is executed on open
    by Excel and LibreOffice, so it is prefixed with an apostrophe.
    """
    text = "" if value is None else str(value)
    if text.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


def _format_csv(result: dict) -> str:
    """One row per segment — for coding transcripts in a spreadsheet or pandas."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["index", "start", "end", "start_time", "end_time", "speaker", "text", "translation", "edited"]
    )
    for i, seg in enumerate(result.get("segments", [])):
        writer.writerow([
            i,
            f"{float(seg.get('start', 0.0)):.3f}",
            f"{float(seg.get('end', 0.0)):.3f}",
            _ts(seg.get("start", 0.0)),
            _ts(seg.get("end", 0.0)),
            _csv_safe(seg.get("speaker", "")),
            _csv_safe((seg.get("text") or "").strip()),
            _csv_safe((seg.get("translation") or "").strip()),
            "yes" if seg.get("edited") else "",
        ])
    return buffer.getvalue()


def _format_txt(result: dict) -> str:
    lines = []
    prev_speaker = None
    for seg in result.get("segments", []):
        speaker = seg.get("speaker", "")
        if speaker and speaker != prev_speaker:
            if lines:
                lines.append("")
            lines.append(f"{speaker}:")
            prev_speaker = speaker
        ts = _ts(seg["start"])
        prefix = f"[{ts}] " if not speaker else f"  [{ts}] "
        lines.append(f"{prefix}{seg['text']}")
    return "\n".join(lines)


def _format_md(result: dict, title: str = "Transcript", date: str = "") -> str:
    lang = result.get("language", "").upper()
    dur = _ts(result.get("duration", 0))

    meta_parts = [f"**Duration:** {dur}", f"**Language:** {lang or 'auto'}"]

    # Collect unique speakers for metadata line
    speakers = []
    for seg in result.get("segments", []):
        sp = seg.get("speaker", "")
        if sp and sp not in speakers:
            speakers.append(sp)
    if speakers:
        meta_parts.append(f"**Speakers:** {', '.join(speakers)}")
    if date:
        meta_parts.append(f"**Date:** {date}")

    lines = [
        f"# {title}",
        "",
        " | ".join(meta_parts),
        "",
        "---",
        "",
    ]

    # Group consecutive same-speaker segments into runs
    runs = []
    for seg in result.get("segments", []):
        speaker = seg.get("speaker", "")
        text = seg.get("text", "").strip()
        if not text:
            continue
        if runs and runs[-1]["speaker"] == speaker:
            runs[-1]["text"] += " " + text
        else:
            runs.append({"speaker": speaker, "start": seg["start"], "text": text})

    for run in runs:
        speaker = run["speaker"]
        ts = _ts(run["start"])
        if speaker:
            lines.append(f"**{speaker}** · `{ts}`")
        else:
            lines.append(f"`{ts}`")
        lines.append("")
        lines.append(run["text"])
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Format registry
# ---------------------------------------------------------------------------

# fmt -> (formatter, media type, file extension, encoding)
# Markdown is handled separately because it also takes a title and a date.
_SIMPLE_FORMATS = {
    "json": (_format_json, "application/json", "json", "utf-8"),
    "srt": (_format_srt, "text/plain; charset=utf-8", "srt", "utf-8"),
    # WebVTT has its own media type; browsers reject text/plain in <track>.
    "vtt": (_format_vtt, "text/vtt; charset=utf-8", "vtt", "utf-8"),
    "txt": (_format_txt, "text/plain; charset=utf-8", "txt", "utf-8"),
    # utf-8-sig: without the BOM, Excel reads accented characters as mojibake.
    "csv": (_format_csv, "text/csv; charset=utf-8", "csv", "utf-8-sig"),
}

SUPPORTED_FORMATS = (*_SIMPLE_FORMATS.keys(), "md")


def render_export(
    fmt: str, result: dict, title: str = "Transcript", date: str = ""
) -> tuple[bytes, str, str]:
    """Render *result* in *fmt*. Returns (content, media type, file extension).

    Raises ValueError for an unknown format, so callers can turn that into a
    400 without repeating the list of supported formats.
    """
    if fmt == "md":
        return (
            _format_md(result, title=title, date=date).encode("utf-8"),
            "text/markdown; charset=utf-8",
            "md",
        )
    if fmt not in _SIMPLE_FORMATS:
        raise ValueError(
            f"Unknown format: {fmt}. Use {', '.join(sorted(SUPPORTED_FORMATS))}."
        )
    formatter, media_type, ext, encoding = _SIMPLE_FORMATS[fmt]
    return formatter(result).encode(encoding), media_type, ext


def _format_md_bulk(recordings: list[dict]) -> str:
    """Combine multiple transcripts into a single markdown document."""
    sections = []

    if len(recordings) > 1:
        toc_lines = ["# Table of Contents", ""]
        for i, rec in enumerate(recordings, 1):
            anchor = rec["title"].lower().replace(" ", "-").replace("/", "").replace(".", "")
            toc_lines.append(f"{i}. [{rec['title']}](#{anchor})")
        toc_lines.extend(["", "---", ""])
        sections.append("\n".join(toc_lines))

    for rec in recordings:
        sections.append(_format_md(rec["result"], title=rec["title"], date=rec.get("date", "")))

    return "\n\n---\n\n".join(sections)
