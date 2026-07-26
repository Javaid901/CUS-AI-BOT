"""
backend/app/analytics/service.py

Comprehensive Analytics Service — data backfill, multi-metric computation,
and no-null JSON responses.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.analytics.models import (
    AnalyticsSession,
    InteractionEvent,
    KnowledgeGap,
)
from app.database import SessionLocal
from app.models.db_models import Message
from app.utils.logging import log


def _db() -> Session:
    return SessionLocal()


# ---------------------------------------------------------------------------
# Backfill: populate analytics from existing Conversation / Message tables
# ---------------------------------------------------------------------------


def backfill_from_conversations() -> int:
    """Create InteractionEvent rows from existing Message records.

    Scans messages that do not yet have a matching InteractionEvent
    (matched by conversation_id).  Skips system messages.
    Returns the number of events created.
    """
    db = _db()
    try:
        existing_conv_ids = {
            r[0] for r in db.query(InteractionEvent.conversation_id).distinct().all()
            if r[0]
        }

        messages = db.query(Message).filter(
            Message.role.in_(["user", "assistant"]),
        ).order_by(Message.created_at.asc()).all()

        created = 0
        batch: list[InteractionEvent] = []

        # Group messages by conversation
        conv_msgs: dict[str, list[Message]] = defaultdict(list)
        for m in messages:
            cid = str(m.conversation_id)
            if cid not in existing_conv_ids:
                conv_msgs[cid].append(m)

        for conv_id, msgs in conv_msgs.items():
            anon = hashlib.sha1(conv_id.encode()).hexdigest()[:16]
            for m in msgs:
                if m.content and len(m.content) > 500:
                    continue  # skip very long content (likely KB data)
                ev = InteractionEvent(
                    id=uuid.uuid4(),
                    timestamp=m.created_at,
                    anon_session_id=anon,
                    conversation_id=conv_id,
                    response_source="rag" if m.role == "assistant" else None,
                    response_time_ms=m.latency_ms,
                )
                batch.append(ev)
                created += 1
                if len(batch) >= 200:
                    db.bulk_save_objects(batch)
                    db.commit()
                    batch = []

        if batch:
            db.bulk_save_objects(batch)
            db.commit()

        if created:
            log.info("Analytics backfill: created %d InteractionEvent rows", created)
        return created
    except Exception as exc:
        db.rollback()
        log.warning("Analytics backfill failed: %s", exc)
        return 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Auto-backfill on first access if analytics tables appear empty
# ---------------------------------------------------------------------------


def ensure_analytics_data() -> int:
    """If interaction_events is empty, attempt backfill from conversations."""
    db = _db()
    try:
        count = db.query(func.count(InteractionEvent.id)).scalar() or 0
        if count == 0:
            return backfill_from_conversations()
        return 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Charts endpoint — multi-metric JSON for frontend charts
# ---------------------------------------------------------------------------


def get_charts_data(period: str = "month") -> dict[str, Any]:
    """Return a rich JSON payload for all frontend charts.

    Never returns None / undefined.  Returns empty lists / zeroes when
    no data exists.
    """
    ensure_analytics_data()
    start, end = _date_range(period)
    db = _db()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
        ).order_by(InteractionEvent.timestamp.asc()).all()

        # ---- Daily time series ----
        daily: dict[str, dict[str, float]] = defaultdict(
            lambda: {"messages": 0, "avg_time": 0.0, "total_time": 0}
        )
        # ---- Hourly distribution ----
        hourly: Counter = Counter()
        # ---- Source distribution ----
        sources: Counter = Counter()
        # ---- Topics / programmes / colleges / services ----
        topics: Counter = Counter()
        programmes: Counter = Counter()
        colleges: Counter = Counter()
        services: Counter = Counter()
        queries_raw: Counter = Counter()
        # ---- Intent distribution ----
        intents: Counter = Counter()
        # ---- Latency tracking ----
        latencies: list[int] = []

        for e in events:
            if e.timestamp:
                key = e.timestamp.strftime("%Y-%m-%d")
                hour_key = e.timestamp.hour
                daily[key]["messages"] += 1
                if e.response_time_ms:
                    daily[key]["total_time"] += e.response_time_ms
                    latencies.append(e.response_time_ms)
                hourly[hour_key] += 1

            src = e.response_source or "unknown"
            sources[src] += 1

            if e.detected_topic:
                topics[e.detected_topic] += 1
            if e.detected_programme:
                programmes[e.detected_programme] += 1
            if e.detected_college:
                colleges[e.detected_college] += 1
            if e.service_requested:
                services[e.service_requested] += 1
            if e.query_original and e.response_source not in ("welcome", "navigation"):
                queries_raw[e.query_original.lower().strip()] += 1
            if e.detected_intent:
                intents[e.detected_intent] += 1

        # Build daily time-series with computed averages
        daily_series = []
        for date_key in sorted(daily):
            d = daily[date_key]
            daily_series.append({
                "date": date_key,
                "messages": d["messages"],
                "avg_response_time_ms": round(d["total_time"] / d["messages"], 1) if d["messages"] else 0,
            })

        # Hourly activity
        hourly_activity = [
            {"hour": h, "count": hourly.get(h, 0)}
            for h in range(24)
        ]

        # Percentiles
        latencies.sort()
        n_lat = len(latencies)
        p50 = latencies[max(0, int(n_lat * 0.50) - 1)] if n_lat else 0
        p90 = latencies[max(0, int(n_lat * 0.90) - 1)] if n_lat else 0
        p99 = latencies[max(0, int(n_lat * 0.99) - 1)] if n_lat else 0

        return {
            "daily_conversations": daily_series,
            "hourly_activity": hourly_activity,
            "top_topics": [{"term": t, "count": c} for t, c in topics.most_common(20)],
            "top_programmes": [{"term": p, "count": c} for p, c in programmes.most_common(20)],
            "top_colleges": [{"term": c, "count": cnt} for c, cnt in colleges.most_common(20)],
            "top_services": [{"term": s, "count": c} for s, c in services.most_common(20)],
            "query_distribution": [{"source": src, "count": cnt} for src, cnt in sources.most_common(10)],
            "intent_distribution": [{"intent": i, "count": c} for i, c in intents.most_common(10)],
            "performance": {
                "avg_response_time_ms": round(sum(latencies) / n_lat, 1) if n_lat else 0,
                "p50": p50,
                "p90": p90,
                "p99": p99,
                "total_samples": n_lat,
            },
            "raw_queries": [{"term": q, "count": c} for q, c in queries_raw.most_common(20)],
            "total_events": len(events),
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# FAQ-style endpoints
# ---------------------------------------------------------------------------


def get_frequent_questions(period: str = "month", limit: int = 20) -> dict[str, Any]:
    """Most frequently asked raw questions."""
    ensure_analytics_data()
    start, end = _date_range(period)
    db = _db()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
            InteractionEvent.response_source.notin_(["welcome", "navigation"]),
        ).all()
        counter: Counter = Counter()
        for e in events:
            if e.query_original:
                counter[e.query_original] += 1
        return {
            "frequent_questions": [{"question": q, "count": c} for q, c in counter.most_common(limit)],
            "total": len(counter),
        }
    finally:
        db.close()


def get_unanswered_questions(period: str = "month", limit: int = 20) -> dict[str, Any]:
    """Questions that led to clarifications or low confidence."""
    ensure_analytics_data()
    start, end = _date_range(period)
    db = _db()
    try:
        gaps = db.query(KnowledgeGap).filter(
            KnowledgeGap.detected_at >= start,
            KnowledgeGap.detected_at < end,
            KnowledgeGap.resolved == False,
        ).order_by(KnowledgeGap.frequency.desc()).limit(limit).all()
        return {
            "unanswered_questions": [
                {
                    "question": g.query_text,
                    "frequency": g.frequency,
                    "type": g.gap_type,
                    "suggestion": g.suggestion,
                }
                for g in gaps
            ],
            "total": len(gaps),
        }
    finally:
        db.close()


def get_knowledge_base_stats() -> dict[str, Any]:
    """Stats about knowledge base growth."""
    db = _db()
    try:
        from app.models import Document, DocumentChunk
        doc_count = db.query(func.count(Document.id)).scalar() or 0
        chunk_count = db.query(func.count(DocumentChunk.id)).scalar() or 0
        return {
            "documents": doc_count,
            "chunks": chunk_count,
            "vectors": chunk_count,  # approximate: 1 embedding per chunk
        }
    finally:
        db.close()


def get_user_metrics(period: str = "month") -> dict[str, Any]:
    """New vs returning users, total sessions."""
    ensure_analytics_data()
    start, end = _date_range(period)
    db = _db()
    try:
        sessions = db.query(AnalyticsSession).filter(
            AnalyticsSession.last_activity >= start,
            AnalyticsSession.last_activity < end,
        ).all()
        total_sessions = len(sessions)
        new_sessions = sum(1 for s in sessions if s.message_count <= 1)
        returning = total_sessions - new_sessions
        return {
            "total_sessions": total_sessions,
            "new_users": new_sessions,
            "returning_users": returning,
            "avg_messages_per_session": round(
                sum(s.message_count or 0 for s in sessions) / total_sessions, 1
            ) if total_sessions else 0,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _date_range(period: str) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return today_start, now
    elif period == "week":
        return today_start - timedelta(days=today_start.weekday()), now
    elif period == "month":
        return today_start.replace(day=1), now
    elif period == "year":
        return today_start.replace(month=1, day=1), now
    return datetime(2020, 1, 1, tzinfo=timezone.utc), now
