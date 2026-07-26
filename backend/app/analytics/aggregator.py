"""
backend/app/analytics/aggregator.py

Periodic aggregation engine — transforms raw interaction events into
pre-computed daily / weekly / monthly / yearly metrics.

Design:
  - Runs as a background scheduled task (see scheduler.py)
  - Computes aggregations for the latest incomplete period
  - Upserts into aggregated_metrics table
  - Also computes performance percentiles from performance_samples
  - Auto-cleans expired data per retention policy
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.analytics.models import (
    AggregatedMetric,
    InteractionEvent,
    PerformanceSample,
)
from app.database import SessionLocal


def _period_range(period: str, dt: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) for the period containing dt."""
    if period == "daily":
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif period == "weekly":
        start = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(weeks=1)
    elif period == "monthly":
        start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    elif period == "yearly":
        start = dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
    else:
        raise ValueError(f"Unknown period: {period}")
    return (start, end)


def _compute_percentiles(stage: str | None, period_start: datetime, period_end: datetime) -> dict[str, float]:
    """Compute P50, P90, P99 for a given stage within a time range."""
    db: Session = SessionLocal()
    try:
        q = db.query(PerformanceSample.latency_ms).filter(
            PerformanceSample.timestamp >= period_start,
            PerformanceSample.timestamp < period_end,
        )
        if stage:
            q = q.filter(PerformanceSample.stage == stage)

        values = sorted([r[0] for r in q.all()])
        if not values:
            return {}
        n = len(values)
        return {
            "p50": float(values[max(0, int(n * 0.50) - 1)]),
            "p90": float(values[max(0, int(n * 0.90) - 1)]),
            "p99": float(values[max(0, int(n * 0.99) - 1)]),
        }
    finally:
        db.close()


def _aggregate_period(period: str, period_start: datetime, period_end: datetime) -> AggregatedMetric | None:
    """Compute and return an AggregatedMetric for the given period."""
    db: Session = SessionLocal()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= period_start,
            InteractionEvent.timestamp < period_end,
        ).all()

        if not events:
            return None

        n = len(events)
        total_time = sum(e.response_time_ms or 0 for e in events)
        planner_time = sum(e.planner_latency_ms or 0 for e in events)
        rag_time = sum(e.rag_latency_ms or 0 for e in events)
        llm_time = sum(e.llm_latency_ms or 0 for e in events)
        cache_hits = sum(1 for e in events if e.cache_hit)
        completed = sum(1 for e in events if e.conversation_completed)
        abandoned = sum(1 for e in events if e.conversation_abandoned)
        clarifications = sum(e.clarification_count or 0 for e in events)
        corrected = sum(1 for e in events if e.query_corrected)

        # Count unique sessions
        session_ids = {e.anon_session_id for e in events if e.anon_session_id}

        # Count unique conversations
        conv_ids = {e.conversation_id for e in events if e.conversation_id}

        # Service usage
        service_usage: dict[str, int] = {}
        for e in events:
            if e.service_requested:
                service_usage[e.service_requested] = service_usage.get(e.service_requested, 0) + 1

        # Programme mentions
        prog_mentions: dict[str, int] = {}
        for e in events:
            if e.detected_programme:
                prog_mentions[e.detected_programme] = prog_mentions.get(e.detected_programme, 0) + 1

        # College mentions
        coll_mentions: dict[str, int] = {}
        for e in events:
            if e.detected_college:
                coll_mentions[e.detected_college] = coll_mentions.get(e.detected_college, 0) + 1

        # Topic mentions
        topic_mentions: dict[str, int] = {}
        for e in events:
            if e.detected_topic:
                topic_mentions[e.detected_topic] = topic_mentions.get(e.detected_topic, 0) + 1

        # Response source counts
        source_counts: dict[str, int] = {}
        for e in events:
            src = e.response_source or "unknown"
            source_counts[src] = source_counts.get(src, 0) + 1

        # Percentiles for total response time
        pcts = _compute_percentiles(None, period_start, period_end)

        metric = AggregatedMetric(
            period=period,
            period_start=period_start,
            period_end=period_end,
            total_conversations=len(conv_ids),
            total_messages=n,
            unique_sessions=len(session_ids),
            avg_response_time_ms=total_time / n if n else 0,
            avg_conversation_length=n / len(conv_ids) if conv_ids else 0,
            avg_planner_latency_ms=planner_time / n if n else 0,
            avg_rag_latency_ms=rag_time / n if n else 0,
            avg_llm_latency_ms=llm_time / n if n else 0,
            cache_hit_ratio=cache_hits / n if n else 0,
            completion_rate=completed / (completed + abandoned) if (completed + abandoned) else 0,
            clarification_rate=clarifications / n if n else 0,
            service_usage=service_usage,
            programme_mentions=prog_mentions,
            college_mentions=coll_mentions,
            topic_mentions=topic_mentions,
            response_source_counts=source_counts,
            query_correction_count=corrected,
            p50_response_time_ms=pcts.get("p50"),
            p90_response_time_ms=pcts.get("p90"),
            p99_response_time_ms=pcts.get("p99"),
        )
        return metric
    finally:
        db.close()


def run_aggregation(period: str | None = None) -> dict[str, Any]:
    """Run aggregation for the specified period(s).

    If period is None, aggregates all incomplete periods.
    Returns counts of metrics created.
    """
    now = datetime.now(timezone.utc)
    periods = [period] if period else ["daily", "weekly", "monthly", "yearly"]
    results: dict[str, int] = {}

    db: Session = SessionLocal()
    try:
        for p in periods:
            start, end = _period_range(p, now)
            # Check if we already have a metric for this period
            existing = db.query(AggregatedMetric).filter(
                AggregatedMetric.period == p,
                AggregatedMetric.period_start == start,
            ).first()

            metric = _aggregate_period(p, start, end)
            if metric is None:
                results[p] = 0
                continue

            if existing:
                # Update in-place
                for col in AggregatedMetric.__table__.columns:
                    if col.name not in ("id", "period", "period_start", "period_end", "created_at"):
                        setattr(existing, col.name, getattr(metric, col.name))
                db.add(existing)
            else:
                db.add(metric)
            db.commit()
            results[p] = 1

        return {"aggregated": results}
    finally:
        db.close()


def run_cleanup(retention_days: int = 365) -> dict[str, int]:
    """Delete events older than retention_days.

    Returns counts of deleted rows.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    counts: dict[str, int] = {}
    db: Session = SessionLocal()
    try:
        for model, name in [
            (InteractionEvent, "events"),
            (PerformanceSample, "samples"),
        ]:
            deleted = db.query(model).filter(model.timestamp < cutoff).delete()
            db.commit()
            counts[name] = deleted
        return counts
    finally:
        db.close()
