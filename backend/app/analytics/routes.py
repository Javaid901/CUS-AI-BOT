"""
backend/app/analytics/routes.py

API routes for the AI Insights & Analytics dashboard.

All routes are admin-protected via require_admin dependency.

Prefix: /api/admin/analytics
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth.security import require_admin
from app.config import settings
from app.database import get_db
from app.models import User

router = APIRouter(prefix=f"{settings.API_PREFIX}/admin/analytics", tags=["analytics"])
_protected = Depends(require_admin)


# ---------------------------------------------------------------------------
# Dashboard overview
# ---------------------------------------------------------------------------


@router.get("/overview")
def analytics_overview(
    period: str = Query("today", pattern="^(today|week|month|year|all)$"),
    current: User = _protected,
):
    from app.analytics.reports import get_multi_period_overview, get_overview
    if period == "all":
        return get_multi_period_overview()
    return get_overview(period)


# ---------------------------------------------------------------------------
# Trending searches
# ---------------------------------------------------------------------------


@router.get("/trending")
def analytics_trending(
    period: str = Query("today", pattern="^(today|week|month|year)$"),
    limit: int = Query(20, ge=1, le=100),
    current: User = _protected,
):
    from app.analytics.reports import get_trending_searches
    return get_trending_searches(period, limit)


# ---------------------------------------------------------------------------
# Course analytics
# ---------------------------------------------------------------------------


@router.get("/courses")
def analytics_courses(
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    current: User = _protected,
):
    from app.analytics.reports import get_course_analytics
    return get_course_analytics(period)


# ---------------------------------------------------------------------------
# College analytics
# ---------------------------------------------------------------------------


@router.get("/colleges")
def analytics_colleges(
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    current: User = _protected,
):
    from app.analytics.reports import get_college_analytics
    return get_college_analytics(period)


# ---------------------------------------------------------------------------
# Student services analytics
# ---------------------------------------------------------------------------


@router.get("/services")
def analytics_services(
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    current: User = _protected,
):
    from app.analytics.reports import get_service_analytics
    return get_service_analytics(period)


@router.get("/authorities")
def analytics_authorities(
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    current: User = _protected,
):
    from app.analytics.reports import get_authority_analytics
    return get_authority_analytics(period)


# ---------------------------------------------------------------------------
# Knowledge insights
# ---------------------------------------------------------------------------


@router.get("/knowledge")
def analytics_knowledge(
    current: User = _protected,
):
    from app.analytics.reports import get_knowledge_insights
    return get_knowledge_insights()


# ---------------------------------------------------------------------------
# Knowledge gaps
# ---------------------------------------------------------------------------


@router.get("/knowledge-gaps")
def analytics_knowledge_gaps(
    limit: int = Query(50, ge=1, le=200),
    include_resolved: bool = Query(False),
    current: User = _protected,
):
    from app.analytics.reports import get_knowledge_gaps
    return get_knowledge_gaps(limit, include_resolved)


@router.post("/knowledge-gaps/{gap_id}/resolve")
def resolve_knowledge_gap(
    gap_id: str,
    current: User = _protected,
    db: Session = Depends(get_db),
):
    from app.analytics.models import KnowledgeGap
    try:
        uid = uuid.UUID(gap_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid gap ID")
    gap = db.get(KnowledgeGap, uid)
    if not gap:
        raise HTTPException(status_code=404, detail="Knowledge gap not found")
    gap.resolved = True
    gap.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "resolved"}


# ---------------------------------------------------------------------------
# Conversation analytics
# ---------------------------------------------------------------------------


@router.get("/conversations")
def analytics_conversations(
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    current: User = _protected,
):
    from app.analytics.reports import get_conversation_analytics
    return get_conversation_analytics(period)


# ---------------------------------------------------------------------------
# Query understanding
# ---------------------------------------------------------------------------


@router.get("/queries")
def analytics_queries(
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    limit: int = Query(50, ge=1, le=200),
    current: User = _protected,
):
    from app.analytics.reports import get_query_analytics
    return get_query_analytics(period, limit)


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


@router.get("/performance")
def analytics_performance(
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    current: User = _protected,
):
    from app.analytics.reports import get_performance_analytics
    return get_performance_analytics(period)


# ---------------------------------------------------------------------------
# Trends (time-series data for charts)
# ---------------------------------------------------------------------------


@router.get("/trends")
def analytics_trends(
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    granularity: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    current: User = _protected,
):
    from app.analytics.reports import get_trend_data
    return get_trend_data(period, granularity)


# ---------------------------------------------------------------------------
# Smart AI Insights
# ---------------------------------------------------------------------------


@router.get("/insights")
def analytics_insights(
    current: User = _protected,
):
    from app.analytics.insights import generate_insights
    return generate_insights()


# ---------------------------------------------------------------------------
# Charts — multi-metric JSON for frontend chart rendering
# ---------------------------------------------------------------------------


@router.get("/charts")
def analytics_charts(
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    current: User = _protected,
):
    from app.analytics.service import get_charts_data
    return get_charts_data(period)


# ---------------------------------------------------------------------------
# Frequently asked questions
# ---------------------------------------------------------------------------


@router.get("/frequent-questions")
def analytics_frequent_questions(
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    limit: int = Query(20, ge=1, le=100),
    current: User = _protected,
):
    from app.analytics.service import get_frequent_questions
    return get_frequent_questions(period, limit)


# ---------------------------------------------------------------------------
# Unanswered questions
# ---------------------------------------------------------------------------


@router.get("/unanswered-questions")
def analytics_unanswered_questions(
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    limit: int = Query(20, ge=1, le=100),
    current: User = _protected,
):
    from app.analytics.service import get_unanswered_questions
    return get_unanswered_questions(period, limit)


# ---------------------------------------------------------------------------
# Knowledge base stats
# ---------------------------------------------------------------------------


@router.get("/kb-stats")
def analytics_kb_stats(
    current: User = _protected,
):
    from app.analytics.service import get_knowledge_base_stats
    return get_knowledge_base_stats()


# ---------------------------------------------------------------------------
# User metrics
# ---------------------------------------------------------------------------


@router.get("/user-metrics")
def analytics_user_metrics(
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    current: User = _protected,
):
    from app.analytics.service import get_user_metrics
    return get_user_metrics(period)


# ---------------------------------------------------------------------------
# Backfill (admin trigger)
# ---------------------------------------------------------------------------


@router.post("/backfill")
def analytics_backfill(
    current: User = _protected,
):
    from app.analytics.service import backfill_from_conversations
    count = backfill_from_conversations()
    return {"status": "ok", "events_created": count}


# ---------------------------------------------------------------------------
# Search inside analytics
# ---------------------------------------------------------------------------


@router.get("/search")
def analytics_search(
    q: str = Query(..., min_length=1, max_length=200),
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    limit: int = Query(50, ge=1, le=100),
    current: User = _protected,
):
    from app.analytics.reports import search_analytics
    return search_analytics(q, period, limit)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@router.get("/export/{format_type}")
def analytics_export(
    format_type: str,
    report: str = Query("overview", description="Report type to export"),
    period: str = Query("month", pattern="^(today|week|month|year)$"),
    current: User = _protected,
):
    """Export analytics data in the specified format.

    Supported formats: csv, json, xlsx, pdf
    Supported reports: overview, trending, courses, colleges, services,
                       conversations, queries, performance, insights
    """
    from app.analytics.export import export_csv, export_excel, export_json, export_pdf
    from app.analytics.insights import generate_insights
    from app.analytics.reports import (
        get_college_analytics,
        get_conversation_analytics,
        get_course_analytics,
        get_overview,
        get_performance_analytics,
        get_query_analytics,
        get_service_analytics,
        get_trending_searches,
    )

    # Fetch the report data
    report_data: Any = {}
    if report == "overview":
        report_data = get_overview(period)
    elif report == "trending":
        report_data = get_trending_searches(period)
    elif report == "courses":
        report_data = get_course_analytics(period)
    elif report == "colleges":
        report_data = get_college_analytics(period)
    elif report == "services":
        report_data = get_service_analytics(period)
    elif report == "conversations":
        report_data = get_conversation_analytics(period)
    elif report == "queries":
        report_data = get_query_analytics(period)
    elif report == "performance":
        report_data = get_performance_analytics(period)
    elif report == "insights":
        report_data = generate_insights()
    else:
        raise HTTPException(status_code=400, detail=f"Unknown report: {report}")

    # Convert to flat list for tabular formats
    flat_data = _flatten_for_export(report_data)

    content_type = "application/json"
    filename = f"analytics_{report}_{period}"
    body: Any = ""

    if format_type == "csv":
        body = export_csv(flat_data)
        content_type = "text/csv"
        filename += ".csv"
    elif format_type == "json":
        body = export_json(report_data)
        content_type = "application/json"
        filename += ".json"
    elif format_type == "xlsx":
        body = export_excel(flat_data, f"{report}_{period}")
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename += ".xlsx"
    elif format_type == "pdf":
        sections = [
            {"title": report.replace("_", " ").title(), "data": flat_data},
        ]
        body = export_pdf(f"Analytics: {report}", sections)
        content_type = "application/pdf"
        filename += ".pdf"
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format_type}")

    return Response(
        content=body if isinstance(body, bytes) else body.encode("utf-8"),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _flatten_for_export(data: Any) -> list[dict[str, Any]]:
    """Convert nested report data into a flat list of dicts for tabular export."""
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return data
        return [{"value": str(v)} for v in data]
    if isinstance(data, dict):
        # Check if it has a list-valued key we can expand
        for val in data.values():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val
        # Flatten key-value pairs
        result = []
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            result.append({"metric": k, "value": str(v)})
        return result
    return [{"value": str(data)}]


# ---------------------------------------------------------------------------
# Aggregation control
# ---------------------------------------------------------------------------


@router.post("/aggregate")
def trigger_aggregation(
    period: str | None = Query(None, pattern="^(daily|weekly|monthly|yearly)$"),
    current: User = _protected,
):
    """Manually trigger analytics aggregation."""
    from app.analytics.aggregator import run_aggregation
    result = run_aggregation(period)
    return result


@router.post("/cleanup")
def trigger_cleanup(
    retention_days: int = Query(365, ge=30, le=1825),
    current: User = _protected,
):
    """Manually trigger cleanup of old analytics data."""
    from app.analytics.aggregator import run_cleanup
    result = run_cleanup(retention_days)
    return result


# ---------------------------------------------------------------------------
# Raw event count (health check)
# ---------------------------------------------------------------------------


@router.get("/health")
def analytics_health(
    current: User = _protected,
    db: Session = Depends(get_db),
):
    """Analytics module health check."""
    from app.analytics.models import (
        AggregatedMetric,
        InteractionEvent,
        KnowledgeGap,
        PerformanceSample,
    )
    return {
        "status": "ok",
        "events": db.query(InteractionEvent).count(),
        "aggregations": db.query(AggregatedMetric).count(),
        "performance_samples": db.query(PerformanceSample).count(),
        "knowledge_gaps": db.query(KnowledgeGap).count(),
        "scheduler_active": True,
    }
