"""
backend/app/chat/service.py

Chat orchestration:
  - retrieve relevant chunks using hybrid retrieval pipeline
  - if nothing relevant or evidence is weak, return the canned fallback (no LLM call)
  - otherwise stream tokens from the LLM
  - yield structured events for the SSE layer: tokens, then a final "done" event
    carrying chat_id, cited_chunks, and retrieval diagnostics.
  - Also persists conversation + messages.

Performance:
  - Retrieval results are cached per query (TTL 60s) to avoid repeated embedding calls.
  - Generation uses a shared HTTP client and keep_alive for warm models.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.ingest.generator import GenerationError, stream_answer
from app.ingest.prompts import FALLBACK_MESSAGE, format_context
from app.ingest.retrieve import retrieve
from app.models import Conversation, Message
from app.orchestrator.cache import get_cache
from app.utils.logging import log
from sqlalchemy.orm import Session


def _dedupe_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeats of the same document, keeping the highest score."""
    best: dict[str, dict[str, Any]] = {}
    for c in citations:
        key = c.get("document_id") or c.get("document_title") or ""
        if not key:
            continue
        existing = best.get(key)
        if existing is None or (c.get("score") or 0) > (existing.get("score") or 0):
            best[key] = c
    return list(best.values())


def _new_conversation(db: Session, user_id) -> Conversation:
    conv = Conversation(id=uuid.uuid4(), user_id=user_id, title=None)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def _chat_id_or_new(db: Session, chat_id: str | None, user_id) -> Conversation:
    if chat_id:
        try:
            conv = db.get(Conversation, uuid.UUID(chat_id))
            if conv:
                return conv
        except (ValueError, AttributeError):
            pass
    return _new_conversation(db, user_id)


def to_citation(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": chunk.get("document_id"),
        "document_title": chunk.get("document_title"),
        "page_number": chunk.get("page_number"),
        "chunk_index": chunk.get("chunk_index"),
        "score": chunk.get("score"),
    }


def _relevant(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Keep chunks above the score threshold if scores are present.
    if not chunks:
        return []
    threshold = float(settings.SCORE_THRESHOLD or 0.0)
    if threshold > 0.0 and chunks[0].get("score") is not None:
        kept = [c for c in chunks if (c.get("score") or 0) >= threshold]
        return kept
    return chunks


async def run_chat(
    db: Session,
    user_id,
    message: str,
    chat_id: str | None,
    context: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Yields dicts:
      {"type": "token", "text": "..."}
      {"type": "done", "chat_id": "...", "cited_chunks": [...], "retrieval_debug": {...}}
      {"type": "error", "message": "..."}
    """
    conv = _chat_id_or_new(db, chat_id, user_id)
    user_msg = Message(conversation_id=conv.id, role="user", content=message)
    db.add(user_msg)
    db.commit()

    # Build retrieval context from conversation state if available
    retrieval_context = context or {}

    # Cache retrieval by query hash to avoid repeated embedding calls
    cache = get_cache()
    query_key = hashlib.md5(message.encode()).hexdigest()
    cached_chunks = await cache.get("rag", query_key)
    if cached_chunks is not None:
        chunks = cached_chunks
    else:
        chunks = _relevant(retrieve(message, context=retrieval_context))
        await cache.set("rag", query_key, chunks, ttl=60.0)

    cited = _dedupe_citations([to_citation(c) for c in chunks])

    # Build retrieval diagnostic info
    retrieval_debug = {}
    try:
        from app.ingest.retriever import get_diagnostics
        retrieval_debug = get_diagnostics()
    except Exception:
        pass

    assistant_text = ""
    if not chunks:
        assistant_text = FALLBACK_MESSAGE
        yield {"type": "token", "text": assistant_text}
    else:
        context_block = format_context(chunks)
        try:
            for token in stream_answer(message, context_block):
                assistant_text += token
                yield {"type": "token", "text": token}
        except GenerationError as exc:
            log.error("Generation failed: %s", exc)
            assistant_text = FALLBACK_MESSAGE
            yield {"type": "token", "text": assistant_text}

    # Persist assistant message + update conversation timestamp.
    try:
        db.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content=assistant_text,
                citations=json.dumps(cited),
                model=settings.LLM_MODEL,
            )
        )
        conv.updated_at = datetime.now(timezone.utc)
        if conv.title is None:
            conv.title = (message[:80]) or "Chat"
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("Failed to persist conversation: %s", exc)

    done_event: dict[str, Any] = {"type": "done", "chat_id": str(conv.id), "cited_chunks": cited}
    if retrieval_debug:
        done_event["retrieval_debug"] = retrieval_debug
    yield done_event
