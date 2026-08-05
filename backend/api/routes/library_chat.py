"""Library chat — ask a question across every transcript, get cited answers."""

from core.embeddings import EmbeddingError, embed_texts
from core.library_chat import answer_question
from core.library_index import index_status, reindex_library
from db import get_session
from fastapi import APIRouter, Depends, HTTPException
from llm_providers import refusal_reason
from models import TranscriptChunk
from pydantic import BaseModel
from settings import _get_llm_settings
from sqlmodel import Session, select

router = APIRouter()

# Embedding the whole library in one request would hold a connection open for
# minutes on a large library, so it is done a slice at a time and the caller
# is told how much is left.
EMBED_BATCH_LIMIT = 200


class ChatRequest(BaseModel):
    question: str


@router.post("/api/library/chat")
def chat_with_library(body: ChatRequest, session: Session = Depends(get_session)) -> dict:
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(400, "A question is required.")

    cfg = _get_llm_settings()
    refusal = refusal_reason(cfg)
    if refusal:
        raise HTTPException(400, refusal)

    status = index_status(session)
    if status["chunks"] == 0:
        raise HTTPException(
            409,
            "Nothing is indexed for chat yet. Build the index first — "
            "transcribe something, or run Rebuild index.",
        )

    try:
        result = answer_question(session, question, cfg)
    except Exception as exc:
        raise HTTPException(502, f"The model could not be reached: {exc}") from exc

    if result["no_matches"]:
        return {
            "answer": "",
            "sources": [],
            "cited": [],
            "used_semantic": result["used_semantic"],
            "no_matches": True,
            "pending": status["recordings_pending"],
        }

    return {**result, "pending": status["recordings_pending"]}


@router.get("/api/library/index")
def get_index_status(session: Session = Depends(get_session)) -> dict:
    cfg = _get_llm_settings()
    status = index_status(session)
    status["embedding_model"] = cfg.get("llm_embedding_model", "")
    # Without an embedding model this is keyword-only retrieval — which works,
    # and the UI should say which one the user is getting.
    status["semantic_available"] = bool(
        status["embedding_model"] and status["chunks_embedded"]
    )
    return status


@router.post("/api/library/index/rebuild")
def rebuild_index(all: bool = False, session: Session = Depends(get_session)) -> dict:
    """Chunk transcripts that have no chunks yet, or all of them with ?all=true."""
    result = reindex_library(session, only_missing=not all)
    return {**result, **index_status(session)}


@router.post("/api/library/index/embed")
def embed_index(session: Session = Depends(get_session)) -> dict:
    """Embed a slice of the chunks that have none. Call until pending is 0."""
    cfg = _get_llm_settings()
    refusal = refusal_reason(cfg)
    if refusal:
        raise HTTPException(400, refusal)

    model = (cfg.get("llm_embedding_model") or "").strip()
    if not model:
        raise HTTPException(
            400,
            "No embedding model configured. Set one in AI Analysis settings to "
            "enable semantic search — keyword search works without it.",
        )

    pending = session.exec(
        select(TranscriptChunk)
        .where(TranscriptChunk.embedding == b"")
        .limit(EMBED_BATCH_LIMIT)
    ).all()
    if not pending:
        return {"embedded": 0, "remaining": 0, **index_status(session)}

    try:
        vectors = embed_texts([c.text for c in pending], cfg)
    except EmbeddingError as exc:
        raise HTTPException(502, str(exc)) from exc

    embedded = 0
    for chunk, vector in zip(pending, vectors):
        if not vector:
            continue
        chunk.embedding = vector
        chunk.embedding_model = model
        session.add(chunk)
        embedded += 1
    session.commit()

    remaining = len(
        session.exec(
            select(TranscriptChunk).where(TranscriptChunk.embedding == b"")
        ).all()
    )
    return {"embedded": embedded, "remaining": remaining, **index_status(session)}
