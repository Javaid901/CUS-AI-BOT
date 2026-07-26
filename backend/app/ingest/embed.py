"""
backend/app/ingest/embed.py

Embedding generation via Ollama (nomic-embed-text by default).

Uses Ollama's batch /api/embed endpoint for efficient multi-text embedding.
A Sentence-Transformers fallback is provided if Ollama is unavailable.
Models are kept warm via keep_alive to avoid cold-start delays.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import httpx
from app.config import settings
from app.models import Document
from app.utils.files import coerce_uuid
from app.utils.logging import log
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.ingest.embed_cache import EmbeddingCache

_EMBED_TIMEOUT = 120.0
_HTTP_CLIENT: httpx.Client | None = None
_HTTP_LOCK = threading.Lock()
_ST_MODEL = None


class EmbeddingError(Exception):
    pass


def _get_client() -> httpx.Client:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        with _HTTP_LOCK:
            if _HTTP_CLIENT is None:
                _HTTP_CLIENT = httpx.Client(timeout=_EMBED_TIMEOUT)
    return _HTTP_CLIENT


def _ollama_embed(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts in a single batch call using Ollama's /api/embed."""
    client = _get_client()
    try:
        resp = client.post(
            f"{settings.OLLAMA_BASE_URL}/api/embed",
            json={
                "model": settings.EMBED_MODEL,
                "input": texts,
                "keep_alive": f"{settings.OLLAMA_KEEP_ALIVE}s",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings")
        if not embeddings:
            raise EmbeddingError("No embeddings in response")
        return embeddings
    except Exception as exc:
        raise EmbeddingError(f"Ollama embed failed: {exc}") from exc


def embed_query(text: str) -> list[float]:
    """Embed a single query string."""
    try:
        return _ollama_embed([text])[0]
    except EmbeddingError:
        log.warning("Falling back to Sentence-Transformers for query embedding")
        return _sentence_transformers_embed([text])[0]


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed many chunk texts in one batch call."""
    try:
        return _ollama_embed(texts)
    except EmbeddingError:
        log.warning("Falling back to Sentence-Transformers for document embedding")
        return _sentence_transformers_embed(texts)


def _sentence_transformers_embed(texts: list[str]) -> list[list[float]]:
    global _ST_MODEL
    if _ST_MODEL is None:
        from sentence_transformers import SentenceTransformer
        _ST_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _ST_MODEL.encode(texts, normalize_embeddings=True).tolist()


def mark_document_failed(db: Session, document_id: str, error: str) -> None:
    doc = db.get(Document, coerce_uuid(document_id))
    if doc:
        doc.status = "failed"
        doc.error = error[:500]
        db.commit()


async def embed_documents_with_cache(
    texts: list[str],
    cache: EmbeddingCache | None = None,
) -> list[list[float]]:
    """Embed texts using cached results; only hit Ollama for cache misses.

    Returns a list of embedding vectors aligned 1:1 with input texts.
    Persists new embeddings back to cache after each batch.
    """
    from app.ingest.embed_cache import cache as _default_cache

    ec = cache or _default_cache

    hits_idx, hits_emb = await ec.get_many(texts)
    all_embeddings: list[list[float] | None] = [None] * len(texts)
    for i, emb in zip(hits_idx, hits_emb):
        all_embeddings[i] = emb

    uncached_indices = [i for i, e in enumerate(all_embeddings) if e is None]
    if not uncached_indices:
        return [e for e in all_embeddings if e is not None]

    uncached_texts = [texts[i] for i in uncached_indices]
    try:
        new_embeddings = await asyncio.to_thread(embed_documents, uncached_texts)
    except EmbeddingError:
        log.warning("Embedding failed for %d uncached texts", len(uncached_texts))
        raise

    await ec.set_many(uncached_texts, new_embeddings)
    await ec.save()

    for idx, emb in zip(uncached_indices, new_embeddings):
        all_embeddings[idx] = emb

    return [e for e in all_embeddings if e is not None]
