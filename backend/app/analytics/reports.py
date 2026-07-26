"""
backend/app/analytics/reports.py

Report generators for the AI Insights dashboard.

All methods accept query parameters (period, programme, college, etc.)
and return pre-formatted dicts ready for JSON serialization.

Expensive computations use pre-computed AggregatedMetric rows when possible.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.analytics.models import (
    AggregatedMetric,
    InteractionEvent,
    KnowledgeGap,
)
from app.database import SessionLocal


def _db() -> Session:
    return SessionLocal()


def _date_range(period: str) -> tuple[datetime, datetime]:
    """Get (start, end) for a preset period label."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return today_start, now
    elif period == "week":
        week_start = today_start - timedelta(days=today_start.weekday())
        return week_start, now
    elif period == "month":
        month_start = today_start.replace(day=1)
        return month_start, now
    elif period == "year":
        year_start = today_start.replace(month=1, day=1)
        return year_start, now
    else:
        # all time
        return datetime(2020, 1, 1, tzinfo=timezone.utc), now


# ---------------------------------------------------------------------------
# Dashboard overview
# ---------------------------------------------------------------------------


def get_overview(period: str = "today") -> dict[str, Any]:
    """Return overview KPIs for a given period."""
    start, end = _date_range(period)
    db = _db()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
        ).all()

        n = len(events)
        session_ids = {e.anon_session_id for e in events if e.anon_session_id}
        conv_ids = {e.conversation_id for e in events if e.conversation_id}
        total_time = sum(e.response_time_ms or 0 for e in events)
        planner_time = sum(e.planner_latency_ms or 0 for e in events)
        rag_time = sum(e.rag_latency_ms or 0 for e in events)
        llm_time = sum(e.llm_latency_ms or 0 for e in events)
        cache_hits = sum(1 for e in events if e.cache_hit)
        completed = sum(1 for e in events if e.conversation_completed)
        abandoned = sum(1 for e in events if e.conversation_abandoned)
        clarifications = sum(e.clarification_count or 0 for e in events)
        corrections = sum(1 for e in events if e.query_corrected)
        services = sum(1 for e in events if e.service_requested)
        rag_count = sum(1 for e in events if e.rag_used)
        structured = sum(1 for e in events if e.structured_lookup_used)
        llm_count = sum(1 for e in events if e.llm_used)

        return {
            "total_messages": n,
            "unique_sessions": len(session_ids),
            "unique_conversations": len(conv_ids),
            "avg_response_time_ms": round(total_time / n, 1) if n else 0,
            "avg_conversation_length": round(n / len(conv_ids), 1) if conv_ids else 0,
            "avg_planner_latency_ms": round(planner_time / n, 1) if n else 0,
            "avg_rag_latency_ms": round(rag_time / n, 1) if n else 0,
            "avg_llm_latency_ms": round(llm_time / n, 1) if n else 0,
            "cache_hit_ratio": round(cache_hits / n, 3) if n else 0,
            "completion_rate": round(completed / (completed + abandoned), 3) if (completed + abandoned) else 0,
            "clarification_rate": round(clarifications / n, 3) if n else 0,
            "query_corrections": corrections,
            "service_requests": services,
            "rag_requests": rag_count,
            "structured_requests": structured,
            "llm_requests": llm_count,
        }
    finally:
        db.close()


def get_multi_period_overview() -> dict[str, Any]:
    """Return overview for today, week, month, and year."""
    return {
        "today": get_overview("today"),
        "week": get_overview("week"),
        "month": get_overview("month"),
        "year": get_overview("year"),
    }


# ---------------------------------------------------------------------------
# Trending searches
# ---------------------------------------------------------------------------


def get_trending_searches(period: str = "today", limit: int = 20) -> dict[str, Any]:
    """Return top searched topics/programmes/colleges."""
    start, end = _date_range(period)
    db = _db()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
        ).all()

        # Aggregate mentions by type
        topics: Counter = Counter()
        programmes: Counter = Counter()
        colleges: Counter = Counter()
        services: Counter = Counter()
        raw_queries: Counter = Counter()

        for e in events:
            if e.detected_topic:
                topics[e.detected_topic] += 1
            if e.detected_programme:
                programmes[e.detected_programme] += 1
            if e.detected_college:
                colleges[e.detected_college] += 1
            if e.service_requested:
                services[e.service_requested] += 1
            if e.query_original and e.response_source not in ("welcome", "navigation"):
                raw_queries[e.query_original.lower().strip()] += 1

        return {
            "topics": [{"term": t, "count": c} for t, c in topics.most_common(limit)],
            "programmes": [{"term": p, "count": c} for p, c in programmes.most_common(limit)],
            "colleges": [{"term": c, "count": cnt} for c, cnt in colleges.most_common(limit)],
            "services": [{"term": s, "count": c} for s, c in services.most_common(limit)],
            "queries": [{"term": q, "count": c} for q, c in raw_queries.most_common(limit)],
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Course / Programme analytics
# ---------------------------------------------------------------------------


def get_course_analytics(period: str = "month") -> dict[str, Any]:
    """Return course-related analytics."""
    start, end = _date_range(period)
    db = _db()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
        ).all()

        programmes: Counter = Counter()
        topics: Counter = Counter()
        colleges: Counter = Counter()

        for e in events:
            if e.detected_programme:
                programmes[e.detected_programme] += 1
            if e.detected_topic:
                topics[e.detected_topic] += 1
            if e.detected_college:
                colleges[e.detected_college] += 1

        return {
            "most_searched_programmes": [{"id": p, "count": c} for p, c in programmes.most_common(20)],
            "topic_breakdown": [{"topic": t, "count": c} for t, c in topics.most_common(20)],
            "college_mentions": [{"college": c, "count": cnt} for c, cnt in colleges.most_common(20)],
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# College analytics
# ---------------------------------------------------------------------------


def get_college_analytics(period: str = "month") -> dict[str, Any]:
    """Return college-specific analytics."""
    start, end = _date_range(period)
    db = _db()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
        ).all()

        college_mentions: Counter = Counter()
        college_topics: dict[str, Counter] = {}

        for e in events:
            coll = e.detected_college
            if coll:
                college_mentions[coll] += 1
                if coll not in college_topics:
                    college_topics[coll] = Counter()
                if e.detected_topic:
                    college_topics[coll][e.detected_topic] += 1

        result = []
        for coll, cnt in college_mentions.most_common(30):
            entry = {"college": coll, "queries": cnt}
            if coll in college_topics:
                entry["top_topics"] = [{"topic": t, "count": c} for t, c in college_topics[coll].most_common(5)]
            result.append(entry)

        return {"colleges": result}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Student services analytics
# ---------------------------------------------------------------------------


def get_service_analytics(period: str = "month") -> dict[str, Any]:
    """Return student service usage analytics."""
    start, end = _date_range(period)
    db = _db()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
        ).all()

        service_counts: Counter = Counter()
        service_success: Counter = Counter()
        service_total_time: dict[str, int] = {}

        for e in events:
            svc = e.service_requested
            if svc:
                service_counts[svc] += 1
                if e.response_time_ms:
                    service_total_time[svc] = service_total_time.get(svc, 0) + e.response_time_ms
                if e.response_source == "connector" and e.conversation_completed:
                    service_success[svc] += 1

        result = []
        for svc, cnt in service_counts.most_common(20):
            entry = {
                "service": svc,
                "count": cnt,
                "success_rate": round(service_success.get(svc, 0) / cnt, 3) if cnt else 0,
                "avg_response_time_ms": round(service_total_time.get(svc, 0) / cnt, 1) if cnt else 0,
            }
            result.append(entry)
        return {"services": result}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Authority / escalation analytics
# ---------------------------------------------------------------------------


def get_authority_analytics(period: str = "month") -> dict[str, Any]:
    """Return authority office referral analytics."""
    start, end = _date_range(period)
    db = _db()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
        ).all()

        dept_counts: Counter = Counter()
        dept_total_time: dict[str, int] = {}
        total_authority = 0
        total_events = len(events)

        for e in events:
            if e.response_source == "authority":
                total_authority += 1
                svc = e.service_requested
                if svc:
                    dept_counts[svc] += 1
                    if e.response_time_ms:
                        dept_total_time[svc] = dept_total_time.get(svc, 0) + e.response_time_ms

        # Unanswered / escalated queries: knowledge_gaps linked to departments
        from app.analytics.models import KnowledgeGap
        gaps = db.query(KnowledgeGap).filter(
            KnowledgeGap.detected_at >= start,
            KnowledgeGap.detected_at < end,
        ).all()
        gap_topics = Counter()
        for g in gaps:
            if g.suggestion:
                gap_topics[g.suggestion] += g.frequency or 1

        departments = []
        for dept, cnt in dept_counts.most_common(20):
            departments.append({
                "department": dept,
                "referrals": cnt,
                "avg_response_time_ms": round(dept_total_time.get(dept, 0) / cnt, 1) if cnt else 0,
                "share_pct": round(cnt / total_authority * 100, 1) if total_authority else 0,
            })

        return {
            "total_authority_referrals": total_authority,
            "escalation_rate": round(total_authority / total_events, 3) if total_events else 0,
            "departments": departments,
            "knowledge_gaps": [{"topic": t, "frequency": f} for t, f in gap_topics.most_common(10)],
            "trend": _get_authority_trend(db, start),
        }
    finally:
        db.close()


def _get_authority_trend(db, start) -> list[dict]:
    """Daily authority referral counts for the given period."""
    from collections import defaultdict
    events = db.query(InteractionEvent).filter(
        InteractionEvent.timestamp >= start,
        InteractionEvent.response_source == "authority",
    ).order_by(InteractionEvent.timestamp.asc()).all()

    buckets: dict[str, int] = defaultdict(int)
    for e in events:
        key = e.timestamp.strftime("%Y-%m-%d")
        buckets[key] += 1

    return [{"date": k, "referrals": v} for k, v in sorted(buckets.items())]


# ---------------------------------------------------------------------------
# Knowledge sync insights
# ---------------------------------------------------------------------------


def get_knowledge_insights() -> dict[str, Any]:
    """Return Knowledge Sync related insights from interaction data."""
    db = _db()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.response_source == "rag",
        ).all()

        total_synced = db.query(InteractionEvent).filter(
            InteractionEvent.knowledge_sync_used == True,
        ).count()

        return {
            "total_rag_uses": len(events),
            "knowledge_sync_references": total_synced,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Knowledge gap detection
# ---------------------------------------------------------------------------


def get_knowledge_gaps(limit: int = 50, include_resolved: bool = False) -> list[dict[str, Any]]:
    """Return detected knowledge gaps."""
    db = _db()
    try:
        q = db.query(KnowledgeGap)
        if not include_resolved:
            q = q.filter(KnowledgeGap.resolved == False)
        gaps = q.order_by(KnowledgeGap.frequency.desc()).limit(limit).all()

        return [
            {
                "id": str(g.id),
                "gap_type": g.gap_type,
                "query_text": g.query_text,
                "confidence_score": g.confidence_score,
                "frequency": g.frequency,
                "suggestion": g.suggestion,
                "resolved": g.resolved,
                "detected_at": g.detected_at.isoformat() if g.detected_at else None,
            }
            for g in gaps
        ]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Conversation analytics
# ---------------------------------------------------------------------------


def get_conversation_analytics(period: str = "month") -> dict[str, Any]:
    """Return conversation flow analytics."""
    start, end = _date_range(period)
    db = _db()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
        ).all()

        Counter()
        total_conv = len({e.conversation_id for e in events if e.conversation_id})
        total_clarifications = sum(e.clarification_count or 0 for e in events)
        completed = sum(1 for e in events if e.conversation_completed)
        abandoned = sum(1 for e in events if e.conversation_abandoned)
        restart_count = sum(1 for e in events if e.detected_intent == "navigation" and e.response_source == "welcome")

        # Track paths per conversation
        conv_actions: dict[str, list[str]] = {}
        for e in events:
            cid = e.conversation_id
            if cid:
                if cid not in conv_actions:
                    conv_actions[cid] = []
                if e.planner_action:
                    conv_actions[cid].append(e.planner_action)

        depths = [len(actions) for actions in conv_actions.values()]
        avg_depth = sum(depths) / len(depths) if depths else 0

        return {
            "total_conversations": total_conv,
            "avg_depth": round(avg_depth, 1),
            "total_clarifications": total_clarifications,
            "completed": completed,
            "abandoned": abandoned,
            "restarts": restart_count,
            "completion_rate": round(completed / total_conv, 3) if total_conv else 0,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Query understanding analytics
# ---------------------------------------------------------------------------


def get_query_analytics(period: str = "month", limit: int = 50) -> dict[str, Any]:
    """Return query understanding analytics."""
    start, end = _date_range(period)
    db = _db()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
            InteractionEvent.query_corrected == True,
        ).all()

        corrections: Counter = Counter()
        for e in events:
            if e.query_original:
                corrections[e.query_original] += 1

        total = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
        ).count()
        corrected_count = len(events)

        return {
            "total_queries": total,
            "corrected_queries": corrected_count,
            "correction_rate": round(corrected_count / total, 3) if total else 0,
            "common_corrections": [{"original": q, "count": c} for q, c in corrections.most_common(limit)],
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Performance analytics
# ---------------------------------------------------------------------------


def get_performance_analytics(period: str = "month") -> dict[str, Any]:
    """Return performance percentiles from aggregated metrics."""
    start, end = _date_range(period)
    db = _db()
    try:
        # Try pre-computed metric first
        metric = db.query(AggregatedMetric).filter(
            AggregatedMetric.period == _period_key(period),
            AggregatedMetric.period_start >= start,
        ).order_by(AggregatedMetric.period_start.desc()).first()

        if metric and metric.p50_response_time_ms is not None:
            return {
                "avg_response_time_ms": round(metric.avg_response_time_ms, 1),
                "p50": round(metric.p50_response_time_ms, 1),
                "p90": round(metric.p90_response_time_ms, 1),
                "p99": round(metric.p99_response_time_ms, 1),
                "avg_planner_latency_ms": round(metric.avg_planner_latency_ms, 1),
                "avg_rag_latency_ms": round(metric.avg_rag_latency_ms, 1),
                "avg_llm_latency_ms": round(metric.avg_llm_latency_ms, 1),
                "cache_hit_ratio": round(metric.cache_hit_ratio, 3),
            }

        # Fallback: compute from raw events
        from app.analytics.aggregator import _compute_percentiles

        pcts = _compute_percentiles(None, start, end)

        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
        ).all()
        n = len(events)
        total_time = sum(e.response_time_ms or 0 for e in events)
        planner_time = sum(e.planner_latency_ms or 0 for e in events)
        rag_time = sum(e.rag_latency_ms or 0 for e in events)
        llm_time = sum(e.llm_latency_ms or 0 for e in events)
        cache_hits = sum(1 for e in events if e.cache_hit)

        return {
            "avg_response_time_ms": round(total_time / n, 1) if n else 0,
            "p50": round(pcts.get("p50", 0), 1),
            "p90": round(pcts.get("p90", 0), 1),
            "p99": round(pcts.get("p99", 0), 1),
            "avg_planner_latency_ms": round(planner_time / n, 1) if n else 0,
            "avg_rag_latency_ms": round(rag_time / n, 1) if n else 0,
            "avg_llm_latency_ms": round(llm_time / n, 1) if n else 0,
            "cache_hit_ratio": round(cache_hits / n, 3) if n else 0,
        }
    finally:
        db.close()


def _period_key(period: str) -> str:
    mapping = {
        "today": "daily",
        "week": "weekly",
        "month": "monthly",
        "year": "yearly",
    }
    return mapping.get(period, "daily")


# ---------------------------------------------------------------------------
# Search inside analytics
# ---------------------------------------------------------------------------


def search_analytics(query: str, period: str = "month", limit: int = 50) -> dict[str, Any]:
    """Search across all analytics data for a query term."""
    start, end = _date_range(period)
    db = _db()
    try:
        q = query.lower().strip()
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
        ).all()

        matches = []
        for e in events:
            score = 0
            if e.detected_programme and q in e.detected_programme.lower():
                score += 3
            if e.detected_college and q in e.detected_college.lower():
                score += 3
            if e.detected_topic and q in e.detected_topic.lower():
                score += 2
            if e.service_requested and q in e.service_requested.lower():
                score += 2
            if e.query_original and q in e.query_original.lower():
                score += 1
            if score:
                matches.append({
                    "score": score,
                    "programme": e.detected_programme,
                    "college": e.detected_college,
                    "topic": e.detected_topic,
                    "service": e.service_requested,
                    "query": e.query_original,
                    "count": 1,
                })

        # Aggregate by unique combination
        grouped: dict[str, dict] = {}
        for m in matches:
            key = f"{m['programme']}|{m['college']}|{m['topic']}|{m['service']}"
            if key in grouped:
                grouped[key]["count"] += 1
                grouped[key]["score"] = max(grouped[key]["score"], m["score"])
            else:
                grouped[key] = dict(m)

        result = sorted(grouped.values(), key=lambda x: -x["score"])[:limit]
        return {
            "query": query,
            "total_matches": len(matches),
            "results": result,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Trend data (for charts over time)
# ---------------------------------------------------------------------------


def get_trend_data(period: str = "month", granularity: str = "daily") -> list[dict[str, Any]]:
    """Return time-series data for trend charts."""
    start, end = _date_range(period)
    db = _db()
    try:
        events = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= start,
            InteractionEvent.timestamp < end,
        ).order_by(InteractionEvent.timestamp.asc()).all()

        buckets: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_time": 0, "cache_hits": 0, "services": 0, "rag": 0})

        for e in events:
            ts = e.timestamp
            if granularity == "daily":
                key = ts.strftime("%Y-%m-%d")
            elif granularity == "weekly":
                iso = ts.isocalendar()
                key = f"{iso[0]}-W{iso[1]:02d}"
            elif granularity == "monthly":
                key = ts.strftime("%Y-%m")
            else:
                key = ts.strftime("%Y-%m-%d")

            buckets[key]["count"] += 1
            if e.response_time_ms:
                buckets[key]["total_time"] += e.response_time_ms
            if e.cache_hit:
                buckets[key]["cache_hits"] += 1
            if e.service_requested:
                buckets[key]["services"] += 1
            if e.rag_used:
                buckets[key]["rag"] += 1

        return [
            {
                "date": k,
                "messages": v["count"],
                "avg_response_time_ms": round(v["total_time"] / v["count"], 1) if v["count"] else 0,
                "cache_hits": v["cache_hits"],
                "service_requests": v["services"],
                "rag_requests": v["rag"],
            }
            for k, v in sorted(buckets.items())
        ]
    finally:
        db.close()
