"""
backend/app/analytics/__init__.py

AI Insights & Analytics Module for Administrators.

This module collects anonymous interaction data, computes aggregations,
generates reports, and provides enterprise-grade analytics dashboards.

Architecture:
  collector.py  ──>  models.py  ──>  aggregator.py  ──>  reports.py
       │                                                       │
       │  (async, non-blocking)                                 │
       │                                                       ▼
  scheduler.py  ──>  background jobs                   insights.py
                                                             │
                                                             ▼
                                                      routes.py / export.py

All collection is fire-and-forget — chatbot response times are never affected.
"""

from app.analytics.models import (
    AggregatedMetric,
    AnalyticsSession,
    InteractionEvent,
    KnowledgeGap,
    PerformanceSample,
)

__all__ = [
    "AggregatedMetric",
    "AnalyticsSession",
    "InteractionEvent",
    "KnowledgeGap",
    "PerformanceSample",
]
