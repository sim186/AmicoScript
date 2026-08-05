"""Building and maintaining the chunk index that library chat retrieves from.

A transcript is the wrong unit to retrieve: a two-hour recording matches
everything and cites nothing. Segments are the wrong unit too — a diarized
segment is often four words long, which carries no meaning on its own. So the
index sits in between: consecutive segments glued into passages of roughly a
paragraph, each keeping the timestamps it spans so an answer can point at the
minute it came from.

This table is derived. It is rebuilt from the transcript whenever the
transcript changes, and nothing here is the source of truth for anything.
"""
from __future__ import annotations

import json

from db import new_session
from models import Recording, Transcript, TranscriptChunk
from sqlmodel import Session, select
from utils.logging_utils import get_logger

logger = get_logger("amicoscript.library_index")

# Roughly a paragraph of speech. Long enough to stand on its own when quoted,
# short enough that a handful fit in a context window alongside the question.
TARGET_CHUNK_CHARS = 900

# A chunk shorter than this is folded into the previous one instead of standing
# alone — "Yeah, exactly." is not a retrievable passage.
MIN_CHUNK_CHARS = 120

# Carry the tail of each chunk into the next so a sentence split across the
# boundary is still findable from either side.
OVERLAP_CHARS = 120


def build_chunks(segments: list[dict]) -> list[dict]:
    """Group *segments* into passages. Returns dicts, touching no database."""
    chunks: list[dict] = []
    current: dict | None = None

    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = (seg.get("speaker") or "").strip()
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or start)

        if current is None:
            current = {
                "start": start,
                "end": end,
                "text": text,
                "speakers": [speaker] if speaker else [],
            }
            continue

        current["text"] += " " + text
        current["end"] = end
        if speaker and speaker not in current["speakers"]:
            current["speakers"].append(speaker)

        if len(current["text"]) >= TARGET_CHUNK_CHARS:
            chunks.append(current)
            tail = current["text"][-OVERLAP_CHARS:]
            current = {
                # The overlap belongs to the passage it was spoken in, so the
                # next chunk starts where the next segment does, not earlier.
                "start": end,
                "end": end,
                "text": tail,
                "speakers": list(current["speakers"][-1:]),
            }

    if current is not None:
        # A trailing scrap is appended to the previous chunk rather than kept
        # as a chunk of its own — but only the part that is not already there.
        if chunks and len(current["text"]) < MIN_CHUNK_CHARS:
            previous = chunks[-1]
            addition = current["text"][OVERLAP_CHARS:].strip()
            if addition:
                previous["text"] += " " + addition
            previous["end"] = current["end"]
            for speaker in current["speakers"]:
                if speaker not in previous["speakers"]:
                    previous["speakers"].append(speaker)
        elif current["text"].strip():
            chunks.append(current)

    for i, chunk in enumerate(chunks):
        chunk["ordinal"] = i
        chunk["speakers"] = ", ".join(chunk["speakers"])
    return chunks


def _segments_of(transcript: Transcript) -> list[dict]:
    try:
        data = json.loads(transcript.json_data)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    segments = data.get("segments")
    return segments if isinstance(segments, list) else []


def index_recording(recording_id: str, session: Session | None = None) -> int:
    """(Re)build the chunks for one recording. Returns how many were written.

    Replaces whatever was there: a transcript whose segments were edited must
    not leave the old wording searchable.
    """
    if session is not None:
        return _index_with(session, recording_id)
    with new_session() as own:
        return _index_with(own, recording_id)


def _index_with(session: Session, recording_id: str) -> int:
    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).first()

    for stale in session.exec(
        select(TranscriptChunk).where(TranscriptChunk.recording_id == recording_id)
    ).all():
        session.delete(stale)

    if transcript is None:
        session.commit()
        return 0

    chunks = build_chunks(_segments_of(transcript))
    for chunk in chunks:
        session.add(
            TranscriptChunk(
                recording_id=recording_id,
                ordinal=chunk["ordinal"],
                start=chunk["start"],
                end=chunk["end"],
                text=chunk["text"],
                speakers=chunk["speakers"],
            )
        )
    session.commit()
    return len(chunks)


def index_recording_quietly(recording_id: str) -> int:
    """index_recording, but a failure is logged instead of raised.

    Called from the transcription worker, where losing the search index is a
    far better outcome than losing the transcription that just finished.
    """
    try:
        return index_recording(recording_id)
    except Exception:
        logger.exception("Could not index recording %s for library chat", recording_id)
        return 0


def index_status(session: Session) -> dict:
    """How much of the library is indexed, and how much of it is embedded."""
    # Only count recordings that still exist; a deleted one leaves no chunks.
    live = {r.id for r in session.exec(select(Recording)).all()}
    transcribed = {
        t.recording_id for t in session.exec(select(Transcript)).all()
    } & live

    chunks = session.exec(select(TranscriptChunk)).all()
    indexed = {c.recording_id for c in chunks}
    embedded = sum(1 for c in chunks if c.embedding)

    return {
        "recordings_with_transcripts": len(transcribed),
        "recordings_indexed": len(indexed & transcribed),
        "recordings_pending": len(transcribed - indexed),
        "chunks": len(chunks),
        "chunks_embedded": embedded,
    }


def reindex_library(session: Session, only_missing: bool = True) -> dict:
    """Build chunks for transcripts that have none (or for all of them)."""
    live = {r.id for r in session.exec(select(Recording)).all()}
    transcribed = [
        t.recording_id
        for t in session.exec(select(Transcript)).all()
        if t.recording_id in live
    ]
    if only_missing:
        have = {c.recording_id for c in session.exec(select(TranscriptChunk)).all()}
        transcribed = [r for r in transcribed if r not in have]

    written = 0
    for recording_id in transcribed:
        written += _index_with(session, recording_id)

    # Chunks belonging to recordings that are gone would otherwise be cited in
    # an answer that cannot be opened.
    orphans = [
        c for c in session.exec(select(TranscriptChunk)).all()
        if c.recording_id not in live
    ]
    for orphan in orphans:
        session.delete(orphan)
    if orphans:
        session.commit()

    return {"recordings": len(transcribed), "chunks": written, "orphans_removed": len(orphans)}
