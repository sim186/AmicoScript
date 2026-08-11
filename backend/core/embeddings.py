"""Embedding vectors from an OpenAI-compatible /v1/embeddings endpoint.

Semantic search needs vectors, and this app already talks to a local
OpenAI-compatible server for analysis — Ollama, LM Studio, llama.cpp and vLLM
all expose embeddings at the same base URL. So there is no second runtime to
install and no new Python dependency: the same server the user already pointed
AmicoScript at does the work, if they name an embedding model.

Nothing here is required. With no embedding model configured, library chat
falls back to keyword retrieval, which needs no setup at all.
"""
from __future__ import annotations

import math
import struct

import requests as _req
from llm_providers import build_headers, normalize_base_url

# Embedding a whole library is many requests; batching keeps it to a few.
BATCH_SIZE = 32

_TIMEOUT = 120


class EmbeddingError(RuntimeError):
    """The embedding endpoint could not be used."""


def embeddings_url(base_url: str) -> str:
    """The /v1/embeddings sibling of the chat endpoint."""
    normalized, _ = normalize_base_url(base_url)
    return f"{normalized.rstrip('/')}/v1/embeddings"


def pack(vector: list[float]) -> bytes:
    """Store a vector as unit-length float32.

    Normalising once at index time turns every later cosine similarity into a
    plain dot product, which matters when the scan is written in Python.
    """
    norm = math.sqrt(sum(v * v for v in vector))
    if not norm:
        return b""
    return struct.pack(f"<{len(vector)}f", *(v / norm for v in vector))


def unpack(blob: bytes) -> list[float]:
    if not blob or len(blob) % 4:
        return []
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def dot(a: list[float], b: list[float]) -> float:
    """Similarity between two unit vectors. Both are already normalised."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def embed_texts(texts: list[str], cfg: dict) -> list[bytes]:
    """Embed *texts*, returning one packed vector each (b"" for a failure).

    Raises EmbeddingError if the endpoint cannot be used at all, so a caller
    can tell "this server has no embedding model" apart from "that one string
    came back empty".
    """
    model = (cfg.get("llm_embedding_model") or "").strip()
    if not model:
        raise EmbeddingError(
            "No embedding model configured. Set one in AI Analysis settings "
            "to enable semantic search."
        )

    url = embeddings_url(cfg.get("llm_base_url", ""))
    headers = build_headers(cfg.get("llm_provider", ""), cfg.get("llm_api_key", ""))

    out: list[bytes] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        try:
            resp = _req.post(
                url, json={"model": model, "input": batch}, headers=headers, timeout=_TIMEOUT
            )
            resp.raise_for_status()
            payload = resp.json()
        except _req.RequestException as exc:
            raise EmbeddingError(f"The embedding endpoint could not be reached: {exc}") from exc
        except ValueError as exc:
            raise EmbeddingError(f"The embedding endpoint returned no JSON: {exc}") from exc

        data = payload.get("data")
        if not isinstance(data, list):
            raise EmbeddingError(f"Unexpected response from {url}: {payload!r}")

        # Providers are not required to preserve order, but they all send the
        # index back, so sort by it rather than trusting the sequence.
        ordered = sorted(
            (d for d in data if isinstance(d, dict)),
            key=lambda d: d.get("index", 0),
        )
        if len(ordered) != len(batch):
            raise EmbeddingError(
                f"Asked {url} for {len(batch)} embeddings and got {len(ordered)}"
            )
        for item in ordered:
            vector = item.get("embedding")
            out.append(pack(vector) if isinstance(vector, list) else b"")

    return out


def embed_query(text: str, cfg: dict) -> list[float]:
    """Embed one string, unpacked and ready to compare."""
    packed = embed_texts([text], cfg)
    return unpack(packed[0]) if packed else []
