"""Export formatters for transcription results.

Each function takes the result dict produced by the pipeline and returns
a UTF-8 string ready to be sent as a file download.
"""
import csv
import io
import json
import re


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


# ---------------------------------------------------------------------------
# YAML frontmatter
# ---------------------------------------------------------------------------

# A bare YYYY-MM-DD is a real date to a YAML parser, which is what Obsidian
# needs to treat the property as a date rather than a string.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Obsidian tags cannot contain whitespace, and a leading '#' belongs in the
# note body, not in the frontmatter value.
_TAG_SEPARATOR_RE = re.compile(r"\s+")


def _yaml_str(value: object) -> str:
    """Quote *value* as a YAML double-quoted scalar.

    Everything user-supplied goes through here — titles, speaker names and tag
    names all reach the export unfiltered. Double-quoting unconditionally means
    none of YAML's bare-scalar rules can be tripped by a title that happens to
    start with '- ', contain ': ' or be the word 'no'. Control characters are
    dropped rather than escaped: they have no place in a title, and a raw
    newline would end the scalar.
    """
    text = "" if value is None else str(value)
    text = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _obsidian_tag(name: str) -> str:
    """Normalise *name* into something Obsidian accepts as a tag."""
    return _TAG_SEPARATOR_RE.sub("-", str(name).lstrip("#").strip())


def _yaml_lines(key: str, values: list, wikilinks: bool = False) -> list[str]:
    """Render a YAML block sequence, or nothing when there is nothing to say."""
    items = [v for v in values if str(v).strip()]
    if not items:
        return []
    lines = [f"{key}:"]
    for item in items:
        text = f"[[{item}]]" if wikilinks else item
        lines.append(f"  - {_yaml_str(text)}")
    return lines


def _frontmatter(
    title: str,
    date: str,
    result: dict,
    speakers: list,
    meta: dict,
    wikilinks: bool,
) -> list[str]:
    """Build the YAML frontmatter block for a transcript note.

    Keys with no value are left out entirely — an empty ``model:`` reads as
    null in Obsidian's property panel and looks like data loss.
    """
    lines = ["---", f"title: {_yaml_str(title)}"]

    if date:
        lines.append(f"date: {date if _ISO_DATE_RE.match(date) else _yaml_str(date)}")

    duration = result.get("duration") or meta.get("duration")
    if duration:
        lines.append(f"duration: {_yaml_str(_ts(duration))}")
        lines.append(f"duration_seconds: {round(float(duration), 3)}")

    language = (result.get("language") or "").strip()
    if language:
        lines.append(f"language: {_yaml_str(language)}")

    # Speakers become links so a person's note collects every conversation
    # they appear in — the whole point of exporting into a vault.
    lines.extend(_yaml_lines("speakers", speakers, wikilinks=wikilinks))
    lines.extend(_yaml_lines("tags", [_obsidian_tag(t) for t in meta.get("tags", [])]))

    for key in ("folder", "source", "model"):
        value = str(meta.get(key) or "").strip()
        if value:
            lines.append(f"{key}: {_yaml_str(value)}")

    lines.append("---")
    return lines


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _format_md(
    result: dict,
    title: str = "Transcript",
    date: str = "",
    meta: dict | None = None,
    wikilinks: bool = False,
    frontmatter: bool = True,
) -> str:
    """Render *result* as Markdown.

    With *frontmatter* the metadata goes into a YAML block that Obsidian, Hugo,
    Jekyll and Quartz all read as note properties; without it the same facts go
    into the inline bold line, which is what the bulk export needs — a combined
    document can only carry one frontmatter block, at the very top.

    *wikilinks* turns speaker names into ``[[Name]]``. It is off by default
    because that syntax is literal noise anywhere other than a wiki-style vault.
    """
    meta = meta or {}

    # Collect unique speakers in the order they first speak.
    speakers = []
    for seg in result.get("segments", []):
        sp = seg.get("speaker", "")
        if sp and sp not in speakers:
            speakers.append(sp)

    lines = []
    if frontmatter:
        lines.extend(_frontmatter(title, date, result, speakers, meta, wikilinks))
        lines.extend(["", f"# {title}", ""])
    else:
        lang = (result.get("language") or "").upper()
        dur = _ts(result.get("duration", 0))
        meta_parts = [f"**Duration:** {dur}", f"**Language:** {lang or 'auto'}"]
        if speakers:
            meta_parts.append(f"**Speakers:** {', '.join(speakers)}")
        if date:
            meta_parts.append(f"**Date:** {date}")
        lines.extend([f"# {title}", "", " | ".join(meta_parts), "", "---", ""])

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
            name = f"[[{speaker}]]" if wikilinks else speaker
            lines.append(f"**{name}** · `{ts}`")
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
    fmt: str,
    result: dict,
    title: str = "Transcript",
    date: str = "",
    meta: dict | None = None,
    wikilinks: bool = False,
) -> tuple[bytes, str, str]:
    """Render *result* in *fmt*. Returns (content, media type, file extension).

    *meta* carries the facts that live on the recording rather than in the
    transcript — tags, folder, source, model — and only Markdown uses it.

    Raises ValueError for an unknown format, so callers can turn that into a
    400 without repeating the list of supported formats.
    """
    if fmt == "md":
        return (
            _format_md(
                result, title=title, date=date, meta=meta, wikilinks=wikilinks
            ).encode("utf-8"),
            "text/markdown; charset=utf-8",
            "md",
        )
    if fmt not in _SIMPLE_FORMATS:
        raise ValueError(
            f"Unknown format: {fmt}. Use {', '.join(sorted(SUPPORTED_FORMATS))}."
        )
    formatter, media_type, ext, encoding = _SIMPLE_FORMATS[fmt]
    return formatter(result).encode(encoding), media_type, ext


def _format_md_bulk(recordings: list[dict], wikilinks: bool = False) -> str:
    """Combine multiple transcripts into a single markdown document.

    One recording is just a note, frontmatter and all. Several become a
    collection: a single frontmatter block at the top — a second one further
    down the file is body text, not properties — a table of contents, and then
    each transcript with its metadata inline.
    """
    if len(recordings) == 1:
        rec = recordings[0]
        return _format_md(
            rec["result"],
            title=rec["title"],
            date=rec.get("date", ""),
            meta=rec.get("meta"),
            wikilinks=wikilinks,
        )

    every_speaker = []
    every_tag = []
    for rec in recordings:
        for seg in rec["result"].get("segments", []):
            sp = seg.get("speaker", "")
            if sp and sp not in every_speaker:
                every_speaker.append(sp)
        for tag in (rec.get("meta") or {}).get("tags", []):
            if tag not in every_tag:
                every_tag.append(tag)

    dates = sorted(rec.get("date", "") for rec in recordings if rec.get("date"))
    header = ["---", f"title: {_yaml_str('Transcripts')}"]
    if dates:
        # The span the collection covers; a single 'date' would be a guess.
        header.append(f"date: {dates[-1] if _ISO_DATE_RE.match(dates[-1]) else _yaml_str(dates[-1])}")
        header.append(f"date_from: {dates[0] if _ISO_DATE_RE.match(dates[0]) else _yaml_str(dates[0])}")
    header.append(f"recordings: {len(recordings)}")
    header.extend(_yaml_lines("speakers", every_speaker, wikilinks=wikilinks))
    header.extend(_yaml_lines("tags", [_obsidian_tag(t) for t in every_tag]))
    header.append("---")

    toc_lines = [*header, "", "# Table of Contents", ""]
    for i, rec in enumerate(recordings, 1):
        anchor = rec["title"].lower().replace(" ", "-").replace("/", "").replace(".", "")
        toc_lines.append(f"{i}. [{rec['title']}](#{anchor})")
    toc_lines.extend(["", "---", ""])

    sections = ["\n".join(toc_lines)]
    for rec in recordings:
        sections.append(
            _format_md(
                rec["result"],
                title=rec["title"],
                date=rec.get("date", ""),
                meta=rec.get("meta"),
                wikilinks=wikilinks,
                frontmatter=False,
            )
        )

    return "\n\n---\n\n".join(sections)
