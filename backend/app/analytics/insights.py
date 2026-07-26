"""
backend/app/analytics/insights.py

Smart AI Insights engine.

Generates automated recommendations and observations from collected
analytics data without requiring an LLM for every request.

Insights are computed by comparing current period metrics against
previous periods, detecting trends, and identifying patterns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.analytics.models import InteractionEvent, KnowledgeGap
from app.database import SessionLocal


def _db() -> Session:
    return SessionLocal()


def _percentage_change(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round((current - previous) / previous * 100, 1)


def generate_insights() -> list[dict[str, Any]]:
    """Generate automated insights by comparing periods.

    Returns a list of insight dicts with:
      - type: trend | anomaly | suggestion | observation | warning
      - severity: info | important | critical
      - message: human-readable insight text
      - metric: what was measured
      - change_pct: percentage change (if applicable)
    Always returns at least one insight (even if just "collecting data").
    """
    insights: list[dict[str, Any]] = []

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    today_start - timedelta(days=7)
    month_start = today_start.replace(day=1)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)

    db = _db()
    try:
        # Total event count (all time)
        total_all = db.query(func.count(InteractionEvent.id)).scalar() or 0

        # ---- Compare this month vs last month ----
        curr = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= month_start,
            InteractionEvent.timestamp < now,
        ).all()
        prev = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= prev_month_start,
            InteractionEvent.timestamp < month_start,
        ).all()

        curr_n = len(curr)
        prev_n = len(prev)

        # Always report total queries
        if total_all > 0:
            insights.append({
                "type": "observation",
                "severity": "info",
                "message": f"Total analytics events recorded: {total_all}. This month: {curr_n}.",
                "metric": "total_events",
            })

        if curr_n and prev_n:
            change = _percentage_change(curr_n, prev_n)
            insights.append({
                "type": "trend" if abs(change) >= 20 else "observation",
                "severity": "important" if abs(change) >= 50 else "info",
                "message": f"Total queries {'increased' if change > 0 else 'decreased'} by {abs(change)}% compared to last month.",
                "metric": "total_queries",
                "change_pct": change,
            })
        elif curr_n and not prev_n:
            insights.append({
                "type": "observation",
                "severity": "info",
                "message": f"{curr_n} queries recorded this month — the first full month of data collection.",
                "metric": "total_queries_first_month",
            })

        # ---- Service usage trends ----
        curr_services: dict[str, int] = {}
        prev_services: dict[str, int] = {}
        for e in curr:
            if e.service_requested:
                curr_services[e.service_requested] = curr_services.get(e.service_requested, 0) + 1
        for e in prev:
            if e.service_requested:
                prev_services[e.service_requested] = prev_services.get(e.service_requested, 0) + 1

        if curr_services:
            top_svc = max(curr_services, key=curr_services.get)
            insights.append({
                "type": "observation",
                "severity": "info",
                "message": f"Most requested service: '{top_svc.replace('_', ' ').title()}' ({curr_services[top_svc]} requests).",
                "metric": "top_service",
            })
            for svc, cnt in curr_services.items():
                prev_cnt = prev_services.get(svc, 0)
                chg = _percentage_change(cnt, prev_cnt)
                if abs(chg) >= 30 and cnt >= 3:
                    insights.append({
                        "type": "trend",
                        "severity": "info",
                        "message": f"'{svc.replace('_', ' ').title()}' requests {'increased' if chg > 0 else 'decreased'} by {abs(chg)}%.",
                        "metric": f"service_{svc}",
                        "change_pct": chg,
                    })

        # ---- Programme popularity ----
        curr_progs: dict[str, int] = {}
        prev_progs: dict[str, int] = {}
        for e in curr:
            if e.detected_programme:
                curr_progs[e.detected_programme] = curr_progs.get(e.detected_programme, 0) + 1
        for e in prev:
            if e.detected_programme:
                prev_progs[e.detected_programme] = prev_progs.get(e.detected_programme, 0) + 1

        if curr_progs:
            top_prog = max(curr_progs, key=curr_progs.get)
            insights.append({
                "type": "observation",
                "severity": "info",
                "message": f"Most searched programme: '{top_prog.upper()}' ({curr_progs[top_prog]} queries).",
                "metric": "top_programme",
            })
            for prog, cnt in sorted(curr_progs.items(), key=lambda x: -x[1]):
                prev_cnt = prev_progs.get(prog, 0)
                chg = _percentage_change(cnt, prev_cnt)
                if abs(chg) >= 40 and cnt >= 2:
                    insights.append({
                        "type": "trend",
                        "severity": "info",
                        "message": f"'{prog.upper()}' searches {'increased' if chg > 0 else 'decreased'} by {abs(chg)}%.",
                        "metric": f"programme_{prog}",
                        "change_pct": chg,
                    })

        # ---- College popularity ----
        curr_colls: dict[str, int] = {}
        for e in curr:
            if e.detected_college:
                curr_colls[e.detected_college] = curr_colls.get(e.detected_college, 0) + 1
        if curr_colls:
            top_coll = max(curr_colls, key=curr_colls.get)
            insights.append({
                "type": "observation",
                "severity": "info",
                "message": f"'{top_coll.replace('_', ' ').title()}' is currently the most searched college ({curr_colls[top_coll]} queries).",
                "metric": "top_college",
            })

        # ---- Topic trends ----
        curr_topics: dict[str, int] = {}
        prev_topics: dict[str, int] = {}
        for e in curr:
            if e.detected_topic:
                curr_topics[e.detected_topic] = curr_topics.get(e.detected_topic, 0) + 1
        for e in prev:
            if e.detected_topic:
                prev_topics[e.detected_topic] = prev_topics.get(e.detected_topic, 0) + 1

        if curr_topics:
            top_topic = max(curr_topics, key=curr_topics.get)
            insights.append({
                "type": "observation",
                "severity": "info",
                "message": f"Most searched topic this month: '{top_topic.replace('_', ' ').title()}' ({curr_topics[top_topic]} queries).",
                "metric": "top_topic",
            })
            for topic, cnt in curr_topics.items():
                prev_cnt = prev_topics.get(topic, 0)
                chg = _percentage_change(cnt, prev_cnt)
                if abs(chg) >= 40 and cnt >= 2:
                    insights.append({
                        "type": "trend",
                        "severity": "info",
                        "message": f"'{topic.replace('_', ' ').title()}' queries {'increased' if chg > 0 else 'decreased'} by {abs(chg)}%.",
                        "metric": f"topic_{topic}",
                        "change_pct": chg,
                    })

        # ---- Knowledge gaps ----
        gaps = db.query(KnowledgeGap).filter(
            KnowledgeGap.resolved == False,
        ).order_by(KnowledgeGap.frequency.desc()).limit(5).all()
        for g in gaps:
            if g.frequency >= 2:
                insights.append({
                    "type": "warning",
                    "severity": "important" if g.frequency >= 10 else "info",
                    "message": f"Common unanswered query: '{g.query_text}' (asked {g.frequency} times). {g.suggestion or 'Consider adding relevant documentation.'}",
                    "metric": f"knowledge_gap_{g.gap_type}",
                })

        # ---- Performance ----
        if curr_n:
            avg_time = sum(e.response_time_ms or 0 for e in curr) / curr_n
            insights.append({
                "type": "observation",
                "severity": "info",
                "message": f"Average response time this month: {avg_time:.0f}ms.",
                "metric": "avg_response_time",
            })

            curr_cache = sum(1 for e in curr if e.cache_hit)
            cache_rate = curr_cache / curr_n
            if curr_n >= 5:
                insights.append({
                    "type": "observation",
                    "severity": "info",
                    "message": f"Cache hit rate: {cache_rate:.0%} ({curr_cache}/{curr_n}).",
                    "metric": "cache_hit_rate",
                })

            # Compare with previous month
            prev_n_use = prev_n
            if prev_n_use:
                prev_avg = sum(e.response_time_ms or 0 for e in prev) / prev_n_use
                perf_change = _percentage_change(avg_time, prev_avg)
                if abs(perf_change) >= 10:
                    insights.append({
                        "type": "trend",
                        "severity": "important" if perf_change > 0 else "info",
                        "message": f"Average response time {'increased' if perf_change > 0 else 'decreased'} by {abs(perf_change)}% (now {avg_time:.0f}ms).",
                        "metric": "response_time_trend",
                        "change_pct": perf_change,
                    })

        # ---- Completion vs abandonment ----
        completed = sum(1 for e in curr if e.conversation_completed)
        abandoned = sum(1 for e in curr if e.conversation_abandoned)
        conv_total = completed + abandoned
        if conv_total > 0:
            aband_rate = abandoned / conv_total
            insights.append({
                "type": "observation",
                "severity": "important" if aband_rate > 0.3 else "info",
                "message": f"Conversation completion rate: {completed}/{conv_total} ({completed/conv_total*100:.0f}%).",
                "metric": "completion_rate",
            })
            if aband_rate > 0.3:
                insights.append({
                    "type": "warning",
                    "severity": "important",
                    "message": f"Conversation abandonment rate is {aband_rate:.0%}. Students may not be finding what they need.",
                    "metric": "abandonment_rate",
                })

        # ---- Traffic spikes ----
        today_events = [e for e in curr if e.timestamp >= today_start]
        yesterday_count = db.query(InteractionEvent).filter(
            InteractionEvent.timestamp >= yesterday_start,
            InteractionEvent.timestamp < today_start,
        ).count()
        today_count = len(today_events)
        if yesterday_count > 0 and today_count > 0:
            spike = _percentage_change(today_count, yesterday_count)
            if abs(spike) >= 30:
                insights.append({
                    "type": "anomaly",
                    "severity": "critical" if abs(spike) >= 100 else "important",
                    "message": f"Traffic {'spike' if spike > 0 else 'drop'} of {abs(spike)}% compared to yesterday.",
                    "metric": "daily_traffic",
                    "change_pct": spike,
                })

        # ---- Total all-time stats ----
        if total_all > 0 and not insights:
            insights.append({
                "type": "observation",
                "severity": "info",
                "message": f"Analytics data collection active — {total_all} events recorded to date.",
                "metric": "data_collection",
            })

        # Always return at least one insight
        if not insights:
            insights.append({
                "type": "observation",
                "severity": "info",
                "message": "Analytics engine is active. Insights will appear once chatbot conversations generate sufficient data.",
                "metric": "awaiting_data",
            })

        return insights
    finally:
        db.close()
