"""Export formatters for transcription results.

Each function takes the result dict produced by the pipeline and returns
a UTF-8 string ready to be sent as a file download.
"""
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
