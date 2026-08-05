"""Question answering across the whole library, with citations.

Three steps. Find the passages that might answer the question; show them to the
model as numbered sources; turn the [1] markers in its reply back into
recordings and timestamps the UI can jump to.

Retrieval is hybrid where it can be. Keyword search (the FTS5 index over
chunks) always works and needs no setup, but it cannot find "what did we decide
about pricing" in a passage that says "we'll charge forty a seat". Embeddings
find that, and are used whenever an embedding model is configured. The two
rankings are combined with reciprocal rank fusion, which needs no calibration
between two scores that are not on the same scale.

The model is told to answer only from the sources and to cite. That is not a
guarantee — nothing in a prompt is — so the answer is returned alongside the
passages it cited, and the UI shows them. An answer whose citations do not
support it is then visibly wrong rather than quietly wrong.
"""
from __future__ import annotations

import re

from core.analysis import LLMTarget, run_completion
from core.embeddings import EmbeddingError, dot, embed_query, unpack
from models import Recording, TranscriptChunk
from search_query import build_fts_or_match
from sqlalchemy import text as _text
from sqlmodel import Session, select
from utils.logging_utils import get_logger

logger = get_logger("amicoscript.library_chat")

# How many passages reach the model. Enough to cover a question that spans a
# few conversations, few enough to leave room for the answer.
TOP_K = 8

# How deep each ranking goes before they are fused. Wider than TOP_K so a
# passage ranked poorly by one method can still be rescued by the other.
CANDIDATE_K = 30

# Reciprocal rank fusion's smoothing constant, from the original paper. It
# stops the top hit of one ranking from dominating the other outright.
RRF_K = 60

_CITATION_RE = re.compile(r"\[(\d+)\]")


def _timestamp(seconds: float) -> str:
    total = int(seconds)
    if total >= 3600:
        return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    return f"{total // 60}:{total % 60:02d}"


def keyword_candidates(session: Session, question: str, limit: int = CANDIDATE_K) -> list[str]:
    """Chunk ids matching *question* by keyword, best first."""
    expression = build_fts_or_match(question)
    if not expression:
        return []
    try:
        rows = session.exec(
            _text(
                """
                SELECT c.id AS id
                FROM chunk_fts
                JOIN transcriptchunk c ON chunk_fts.rowid = c.rowid
                WHERE chunk_fts MATCH :q
                ORDER BY rank
                LIMIT :lim
                """
            ),
            params={"q": expression, "lim": limit},
        ).all()
    except Exception:
        # A missing FTS table or a MATCH the tokenizer rejects must not take
        # the whole answer down — semantic retrieval may still have this one.
        logger.exception("Keyword retrieval failed for library chat")
        return []
    return [row.id for row in rows]


def semantic_candidates(
    session: Session, question: str, cfg: dict, limit: int = CANDIDATE_K
) -> list[str]:
    """Chunk ids closest to *question* in embedding space, best first.

    Returns nothing when the library has no embeddings — that is the ordinary
    state before anyone has built them, not an error.
    """
    query_vector = embed_query(question, cfg)
    if not query_vector:
        return []

    scored: list[tuple[float, str]] = []
    for chunk in session.exec(
        select(TranscriptChunk).where(TranscriptChunk.embedding != b"")
    ).all():
        vector = unpack(chunk.embedding)
        if len(vector) != len(query_vector):
            # A different embedding model, or a half-migrated index. Skipping
            # beats comparing vectors that do not share a space.
            continue
        scored.append((dot(query_vector, vector), chunk.id))

    scored.sort(reverse=True)
    return [chunk_id for _, chunk_id in scored[:limit]]


def fuse(rankings: list[list[str]], limit: int = TOP_K) -> list[str]:
    """Reciprocal rank fusion of several ranked id lists."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + position + 1)
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [item for item, _ in ordered[:limit]]


def retrieve(session: Session, question: str, cfg: dict) -> tuple[list[dict], bool]:
    """The passages to answer *question* from. Returns (sources, used_semantic)."""
    keyword = keyword_candidates(session, question)

    semantic: list[str] = []
    used_semantic = False
    if (cfg.get("llm_embedding_model") or "").strip():
        try:
            semantic = semantic_candidates(session, question, cfg)
            used_semantic = bool(semantic)
        except EmbeddingError as exc:
            # Configured but unreachable: answer from keywords and say so
            # rather than refusing a question that keyword search can handle.
            logger.warning("Semantic retrieval unavailable: %s", exc)

    chunk_ids = fuse([r for r in (keyword, semantic) if r])
    if not chunk_ids:
        return [], used_semantic

    chunks = {
        c.id: c
        for c in session.exec(
            select(TranscriptChunk).where(TranscriptChunk.id.in_(chunk_ids))
        ).all()
    }
    recordings = {
        r.id: r
        for r in session.exec(
            select(Recording).where(
                Recording.id.in_({c.recording_id for c in chunks.values()})
            )
        ).all()
    }

    sources = []
    for chunk_id in chunk_ids:
        chunk = chunks.get(chunk_id)
        if chunk is None:
            continue
        recording = recordings.get(chunk.recording_id)
        if recording is None:
            # The recording was deleted between indexing and now; citing it
            # would produce a link that opens nothing.
            continue
        sources.append({
            "chunk_id": chunk.id,
            "recording_id": chunk.recording_id,
            "title": recording.alias or recording.filename,
            "start": chunk.start,
            "end": chunk.end,
            "timestamp": _timestamp(chunk.start),
            "speakers": chunk.speakers,
            "text": chunk.text,
        })
    return sources, used_semantic


def build_prompt(question: str, sources: list[dict]) -> str:
    """Numbered sources, then the question, then the rules."""
    blocks = []
    for i, source in enumerate(sources, 1):
        speakers = f", speakers: {source['speakers']}" if source["speakers"] else ""
        blocks.append(
            f"[{i}] {source['title']} at {source['timestamp']}{speakers}\n{source['text']}"
        )
    joined = "\n\n".join(blocks)

    return (
        "You are answering a question about someone's personal library of audio "
        "transcripts. Below are the passages that most closely match the "
        "question, each with a number.\n\n"
        "Rules:\n"
        "- Answer only from these passages. Do not use outside knowledge.\n"
        "- Cite the passages you used inline, like [1] or [2][3].\n"
        "- If the passages do not answer the question, say so plainly. Do not "
        "guess, and do not pad the answer with what is merely related.\n"
        "- Quote sparingly and keep the answer short.\n\n"
        f"<sources>\n{joined}\n</sources>\n\n"
        f"Question: {question}"
    )


def cited_indices(answer: str, source_count: int) -> list[int]:
    """The 1-based source numbers the answer actually cites, in order of use."""
    seen: list[int] = []
    for match in _CITATION_RE.finditer(answer):
        index = int(match.group(1))
        # A model citing [9] out of four sources is hallucinating a reference;
        # dropping it is better than showing the user a source it never read.
        if 1 <= index <= source_count and index not in seen:
            seen.append(index)
    return seen


def answer_question(session: Session, question: str, cfg: dict) -> dict:
    """Retrieve, ask, and return the answer with the sources behind it."""
    sources, used_semantic = retrieve(session, question, cfg)
    if not sources:
        return {
            "answer": "",
            "sources": [],
            "cited": [],
            "used_semantic": used_semantic,
            "no_matches": True,
        }

    target = LLMTarget.from_options(cfg)
    answer, _ = run_completion(target, build_prompt(question, sources))
    answer = (answer or "").strip()

    cited = cited_indices(answer, len(sources))

    # The sources keep the order and the numbering they were shown in, because
    # the [n] markers in the answer point at them. Sorting the cited ones to
    # the front would silently make every citation in the text wrong.
    return {
        "answer": answer,
        "sources": sources,
        "cited": cited,
        "used_semantic": used_semantic,
        "no_matches": False,
    }
