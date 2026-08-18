"""
backend/app/chat/service.py

Chat orchestration:
  - retrieve relevant chunks using hybrid retrieval pipeline
  - if nothing relevant or evidence is weak, return the canned fallback (no LLM call)
  - otherwise generate the answer fully (no live token streaming), validate it
    (poisoned/echoed output or a confessed-unknown is replaced/trimmed to the
    clean fallback), then yield the validated text as a token event
  - yield structured events for the SSE layer: tokens, then a final "done" event
    carrying chat_id, cited_chunks, and retrieval diagnostics.
  - Also persists conversation + messages.

Performance:
  - Retrieval results are cached per query (TTL 60s) to avoid repeated embedding calls.
  - Generation uses a shared HTTP client and keep_alive for warm models.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.chat.fallback import build_fallback_response
from app.ingest.generator import GenerationError, stream_answer_async
from app.ingest.prompts import format_context
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


def _valid_user_uuid(value) -> bool:
    """True when a user_id is a UUID-conforming string.

    conversations.user_id is UUID-typed; non-UUID ids (test harnesses,
    anonymous callers) must be stored as NULL instead of corrupting the row.
    """
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _new_conversation(db: Session, user_id) -> Conversation:
    user_uuid = user_id if _valid_user_uuid(user_id) else None
    conv = Conversation(id=uuid.uuid4(), user_id=user_uuid, title=None)
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


def _chunk_score(chunk: dict[str, Any]) -> float:
    """Best available relevance score for a retrieved chunk."""
    for key in ("rerank_score", "combined_score", "embedding_score"):
        val = chunk.get(key)
        if val is not None:
            return float(val)
    return 0.0


def to_citation(chunk: dict[str, Any]) -> dict[str, Any]:
    from app.ingest.prompts import display_title
    return {
        "document_id": chunk.get("document_id"),
        "document_title": display_title(chunk.get("document_title") or ""),
        "page_number": chunk.get("page_number"),
        "chunk_index": chunk.get("chunk_index"),
        "score": _chunk_score(chunk),
    }


def _relevant(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Keep chunks above the score threshold if scores are present.
    if not chunks:
        return []
    threshold = float(settings.SCORE_THRESHOLD or 0.0)
    if threshold > 0.0 and _chunk_score(chunks[0]) > 0.0:
        kept = [c for c in chunks if _chunk_score(c) >= threshold]
        return kept
    return chunks


def _scope_chunks(chunks: list[dict[str, Any]], context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Drop chunks that clearly belong to a DIFFERENT programme.

    A "BCA fee" answer must not carry MCA numbers: when the conversation is
    scoped to a single programme and a chunk's title/heading names other
    programme(s) but never the target, it is treated as foreign. Comparisons
    (context["programmes"] with 2+ entries) keep every target.
    """
    context = context or {}
    programmes = context.get("programmes") or []
    programme = context.get("programme")
    if not chunks or not programme or len(programmes) >= 2:
        return chunks
    try:
        from app.orchestrator.context import PROGRAMME_ALIASES, PROGRAMME_PATTERN
    except Exception:
        return chunks

    target = str(programme).lower()
    if target not in PROGRAMME_ALIASES.values():
        return chunks

    def _named(text: str | None) -> set[str]:
        if not text:
            return set()
        found: set[str] = set()
        for match in PROGRAMME_PATTERN.finditer(str(text)):
            pid = PROGRAMME_ALIASES.get(match.group(0).lower())
            if pid:
                found.add(pid)
        return found

    kept: list[dict[str, Any]] = []
    for chunk in chunks:
        named = _named(chunk.get("document_title")) | _named(chunk.get("heading"))
        others = named - {target}
        if others and target not in named:
            continue  # foreign chunk (names other programmes, never the target)
        kept.append(chunk)
    return kept or chunks  # never empty the evidence set


def _scope_note(context: dict[str, Any] | None) -> str | None:
    """Hard answer-scope constraints injected into the generation prompt.

    When the conversation is scoped to a programme (with optional scheme /
    semester), the model must answer ONLY about that programme and never
    present another programme's data as belonging to it.
    """
    context = context or {}
    programmes = context.get("programmes") or []
    programme = context.get("programme")
    if not programme and not programmes:
        return None
    if programmes and len(programmes) >= 2:
        labels = [str(p).upper() for p in programmes]
        return (
            "Answer-scope constraints:\n"
            f"- The user is comparing these programmes: {', '.join(labels)}.\n"
            "- Cover ONLY these programmes; keep their details separate and labelled.\n"
            "- Never present one programme's data as belonging to another."
        )
    parts = [f"- The user is asking specifically about {str(programme).upper()}."]
    scheme = context.get("academic_scheme")
    if scheme:
        parts.append(f"- The academic scheme in conversation is {str(scheme).upper()}.")
    semester = context.get("semester")
    if semester is not None:
        parts.append(f"- The semester in conversation is {semester}.")
    parts.append(f"- Answer ONLY about {str(programme).upper()}. If the excerpts also mention other programmes, use only the parts about {str(programme).upper()}.")
    parts.append("- Never present another programme's fee, eligibility, subjects, or dates as belonging to it.")
    return "Answer-scope constraints:\n" + "\n".join(parts)


def _fallback_events(message: str) -> tuple[list[dict[str, Any]], str]:
    """Build the enriched fallback (text + authority card + next steps).

    Guarantees a useful response for anything the assistant cannot answer:
    no dead-end text, no hallucination, no empty stream.
    """
    fb = build_fallback_response(message)
    events = [{"type": "token", "text": fb["text"]}]
    if fb.get("card"):
        events.append(fb["card"])
    if fb.get("options"):
        events.append(fb["options"])
    return events, fb["text"]


def _llm_confessed_unknown(text: str) -> bool:
    """True when the generated answer is the canonical 'not in knowledge
    base' sentence (possibly with a polite prefix / trailing noise)."""
    normalized = " ".join(text.lower().replace("'", "").split()).rstrip(".!\"")
    marker = "couldn't find this information in the cluster university srinagar knowledge base"
    marker = " ".join(marker.replace("'", "").split())
    return marker in normalized


def _llm_parroted_prompt(text: str) -> bool:
    """True when the model echoed the retrieval prompt instead of answering.

    Two fingerprints of a poisoned generation:
      - the context template's instruction line ("Answer using ONLY the
        excerpts...") appears in the output, and/or
      - a context-block source label ("[Source 1: ...]" with a number) is
        reproduced — the system prompt tells the model to cite as
        "[Source: Title, Page]" without numeric labels, so a numbered
        label can only be a copy of the context block.
    """
    normalized = " ".join(text.lower().replace("'", "").split())
    if "answer using only the excerpts" in normalized:
        return True
    return re.search(r"\[source\s*\d+\s*:", normalized) is not None


def _confession_cut(text: str) -> int:
    """End position of the first 'not in knowledge base' confession sentence.

    Trailing noise after a confession (leaked context fragments, the model
    repeating itself) is cut off so the persisted/streamed answer stays
    clean. Returns -1 when no confession is present.
    """
    m = re.search(
        r"couldn['’]?t\s+find\s+this\s+information\s+in\s+the\s+cluster\s+university\s+srinagar\s+knowledge\s+base",
        text,
        re.I | re.S,
    )
    return m.end() if m else -1


def _llm_fallback_events(message: str) -> list[dict[str, Any]]:
    """Enrichment events (authority card + next steps) after the LLM itself
    reports it cannot answer — the existing text is kept verbatim."""
    fb = build_fallback_response(message)
    events: list[dict[str, Any]] = []
    if fb.get("card"):
        events.append(fb["card"])
    if fb.get("options"):
        events.append(fb["options"])
    return events


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

    # Cache retrieval by query hash AND context so that programme/semester/
    # scheme-filtered results never leak to an identical query from another
    # context (the `where` filter depends on `retrieval_context`).
    cache = get_cache()
    ctx_key = json.dumps(retrieval_context, sort_keys=True, default=str)
    query_key = hashlib.md5((message + "|" + ctx_key).encode()).hexdigest()
    cached_chunks = await cache.get("rag", query_key)
    if cached_chunks is not None:
        chunks = cached_chunks
    else:
        try:
            # Retrieval chains Ollama embedding (HTTP, up to 120s), a Chroma
            # query and a BM25 refresh that can load tens of thousands of
            # chunks — never run that on the event loop or a single chat
            # stalls every user.
            chunks = _relevant(
                await asyncio.to_thread(retrieve, message, context=retrieval_context)
            )
            await cache.set("rag", query_key, chunks, ttl=60.0)
        except Exception as exc:
            # Retrieval/embedding failures must never propagate: the user gets
            # a friendly, traceable message instead of a broken SSE stream.
            log.error("Retrieval failed chat=%s query=%s: %s", chat_id, message[:120], exc)
            yield {
                "type": "error",
                "message": "The knowledge service is temporarily unavailable. Please try again in a moment.",
                "ref": hashlib.sha1(str(chat_id or "").encode()).hexdigest()[:8],
            }
            yield {"type": "done", "chat_id": str(conv.id), "cited_chunks": []}
            return

    # Scope chunks to the conversation's programme so a "BCA" answer never
    # carries MCA facts. Comparisons (programmes list) keep all targets.
    chunks = _scope_chunks(chunks, retrieval_context)

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
        events, assistant_text = _fallback_events(message)
        for event in events:
            yield event
    else:
        # Generate fully BEFORE streaming anything to the client: with a
        # small local model, a generation can be poisoned (it echoes the
        # prompt/context, or hallucinates after confessing it cannot
        # answer). Only validated text may reach the user; poisoned or
        # confessed-unknown output is replaced with the clean fallback.
        context_block = format_context(chunks)
        scope_note = _scope_note(retrieval_context)
        if scope_note:
            context_block = f"{context_block}\n\n{scope_note}"
        try:
            async for token in stream_answer_async(message, context_block):
                assistant_text += token
        except GenerationError as exc:
            log.error("Generation failed: %s", exc)
            events, assistant_text = _fallback_events(message)
            for event in events:
                yield event
        except Exception as exc:
            log.error("Generation error chat=%s: %s", chat_id, exc)
            events, assistant_text = _fallback_events(message)
            for event in events:
                yield event
        if _llm_parroted_prompt(assistant_text):
            log.warning("Generation parroted the prompt; falling back cleanly")
            events, assistant_text = _fallback_events(message)
            for event in events:
                yield event
        elif _llm_confessed_unknown(assistant_text):
            cut = _confession_cut(assistant_text)
            if cut > 0:
                assistant_text = assistant_text[:cut].rstrip()
            yield {"type": "token", "text": assistant_text}
            for event in _llm_fallback_events(message):
                yield event
        else:
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
