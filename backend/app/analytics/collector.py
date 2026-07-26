"""
backend/app/analytics/collector.py

Async, fire-and-forget analytics collector.

Design:
  - All collection methods are async and return immediately.
  - DB writes happen via asyncio.to_thread() to avoid blocking the event loop.
  - The collector uses a background queue (asyncio.Queue) for batch writes,
    ensuring chat response times are NEVER affected by analytics.

Usage in orchestrator:

    from app.analytics.collector import collect

    # Fire-and-forget:  the coroutine schedules the write and returns immediately
    asyncio.ensure_future(collect(db_session_factory, event_data))
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.analytics.models import (
    AnalyticsSession,
    InteractionEvent,
    KnowledgeGap,
    PerformanceSample,
)
from app.database import SessionLocal

# ---------------------------------------------------------------------------
# Background batch queue
# ---------------------------------------------------------------------------

_BATCH: list[dict[str, Any]] = []
_BATCH_LOCK = asyncio.Lock()
_BATCH_MAX_SIZE = 50
_FLUSH_INTERVAL = 5.0  # seconds
_last_flush = time.monotonic()


async def _flush_batch() -> None:
    """Flush buffered events to DB in a background thread."""
    global _last_flush
    async with _BATCH_LOCK:
        if not _BATCH:
            return
        batch = _BATCH[:]
        _BATCH.clear()
    _last_flush = time.monotonic()

    def _write(events: list[dict]) -> None:
        db: Session = SessionLocal()
        try:
            for ev in events:
                ev_type = ev.pop("_type", "event")
                if ev_type == "event":
                    db.add(InteractionEvent(**ev))
                elif ev_type == "perf":
                    db.add(PerformanceSample(**ev))
                elif ev_type == "gap":
                    existing = db.query(KnowledgeGap).filter(
                        KnowledgeGap.query_text == ev.get("query_text"),
                        KnowledgeGap.gap_type == ev.get("gap_type"),
                        KnowledgeGap.resolved == False,
                    ).first()
                    if existing:
                        existing.frequency = (existing.frequency or 1) + 1
                    else:
                        db.add(KnowledgeGap(**ev))
                elif ev_type == "session":
                    existing = db.query(AnalyticsSession).filter(
                        AnalyticsSession.anon_session_id == ev.get("anon_session_id")
                    ).first()
                    if existing:
                        for k, v in ev.items():
                            if k != "anon_session_id":
                                setattr(existing, k, v)
                    else:
                        db.add(AnalyticsSession(**ev))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    await asyncio.to_thread(_write, batch)


async def _maybe_flush() -> None:
    """Flush if batch is full or enough time has passed."""
    async with _BATCH_LOCK:
        should = len(_BATCH) >= _BATCH_MAX_SIZE or (time.monotonic() - _last_flush >= _FLUSH_INTERVAL and _BATCH)
    if should:
        await _flush_batch()


async def collect_event(
    anon_session_id: str | None = None,
    conversation_id: str | None = None,
    planner_action: str | None = None,
    detected_intent: str | None = None,
    confidence_score: float | None = None,
    detected_programme: str | None = None,
    detected_college: str | None = None,
    detected_topic: str | None = None,
    detected_level: str | None = None,
    detected_service: str | None = None,
    response_source: str | None = None,
    route_chosen: str | None = None,
    cache_hit: bool = False,
    response_time_ms: int | None = None,
    planner_latency_ms: int | None = None,
    rag_latency_ms: int | None = None,
    llm_latency_ms: int | None = None,
    clarification_count: int = 0,
    conversation_completed: bool = False,
    conversation_abandoned: bool = False,
    service_requested: str | None = None,
    knowledge_sync_used: bool = False,
    rag_used: bool = False,
    structured_lookup_used: bool = False,
    llm_used: bool = False,
    query_corrected: bool = False,
    query_original: str | None = None,
) -> None:
    """Collect an interaction event asynchronously (fire-and-forget)."""
    event = {
        "_type": "event",
        "timestamp": datetime.now(timezone.utc),
        "anon_session_id": anon_session_id,
        "conversation_id": conversation_id,
        "planner_action": planner_action,
        "detected_intent": detected_intent,
        "confidence_score": confidence_score,
        "detected_programme": detected_programme,
        "detected_college": detected_college,
        "detected_topic": detected_topic,
        "detected_level": detected_level,
        "detected_service": detected_service,
        "response_source": response_source,
        "route_chosen": route_chosen,
        "cache_hit": cache_hit,
        "response_time_ms": response_time_ms,
        "planner_latency_ms": planner_latency_ms,
        "rag_latency_ms": rag_latency_ms,
        "llm_latency_ms": llm_latency_ms,
        "clarification_count": clarification_count,
        "conversation_completed": conversation_completed,
        "conversation_abandoned": conversation_abandoned,
        "service_requested": service_requested,
        "knowledge_sync_used": knowledge_sync_used,
        "rag_used": rag_used,
        "structured_lookup_used": structured_lookup_used,
        "llm_used": llm_used,
        "query_corrected": query_corrected,
        "query_original": query_original,
    }
    # Remove None values to allow DB defaults
    event = {k: v for k, v in event.items() if v is not None}

    async with _BATCH_LOCK:
        _BATCH.append(event)
    await _maybe_flush()


async def collect_performance(
    stage: str,
    latency_ms: int,
    anon_session_id: str | None = None,
) -> None:
    """Collect a raw performance sample."""
    event = {
        "_type": "perf",
        "timestamp": datetime.now(timezone.utc),
        "stage": stage,
        "latency_ms": latency_ms,
        "anon_session_id": anon_session_id,
    }
    async with _BATCH_LOCK:
        _BATCH.append(event)
    await _maybe_flush()


async def collect_knowledge_gap(
    gap_type: str,
    query_text: str | None = None,
    confidence_score: float | None = None,
    suggestion: str | None = None,
) -> None:
    """Collect a detected knowledge gap."""
    event = {
        "_type": "gap",
        "detected_at": datetime.now(timezone.utc),
        "gap_type": gap_type,
        "query_text": query_text,
        "confidence_score": confidence_score,
        "suggestion": suggestion,
    }
    event = {k: v for k, v in event.items() if v is not None}
    async with _BATCH_LOCK:
        _BATCH.append(event)
    await _maybe_flush()


async def collect_session(
    anon_session_id: str,
    conversation_id: str,
    message_count: int = 0,
    completed: bool = False,
    abandoned: bool = False,
) -> None:
    """Track an anonymous session."""
    event = {
        "_type": "session",
        "anon_session_id": anon_session_id,
        "conversation_ids": [conversation_id],
        "message_count": message_count,
        "last_activity": datetime.now(timezone.utc),
        "completed": completed,
        "abandoned": abandoned,
    }
    async with _BATCH_LOCK:
        _BATCH.append(event)
    await _maybe_flush()


# ---------------------------------------------------------------------------
# Startup / shutdown helpers
# ---------------------------------------------------------------------------


async def flush_all() -> None:
    """Force-flush all buffered events (call on shutdown)."""
    await _flush_batch()


async def start_background_flusher() -> asyncio.Task:
    """Start a background task that periodically flushes the batch."""

    async def _loop():
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL)
            try:
                await _flush_batch()
            except Exception:
                pass

    return asyncio.create_task(_loop())
