"""
backend/app/analytics/models.py

SQLAlchemy ORM models for AI Insights & Analytics.

Tables:
  interaction_events    — raw anonymized interaction log (append-only)
  aggregated_metrics    — pre-computed daily/weekly/monthly rollups
  knowledge_gaps        — automatically detected knowledge gaps
  performance_samples   — raw latency samples for percentile computation
  analytics_sessions    — anonymous session tracking
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)

from app.database import Base, utcnow
from app.models.db_models import _UUID


class InteractionEvent(Base):
    """Anonymized interaction event — one row per user message processed."""

    __tablename__ = "interaction_events"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    anon_session_id = Column(String(64), nullable=True, index=True)
    conversation_id = Column(String(64), nullable=True, index=True)

    # Planner / intent
    planner_action = Column(String(40), nullable=True)
    detected_intent = Column(String(40), nullable=True)
    confidence_score = Column(Float, nullable=True)

    # Entities detected
    detected_programme = Column(String(40), nullable=True)
    detected_college = Column(String(40), nullable=True)
    detected_topic = Column(String(60), nullable=True)
    detected_level = Column(String(20), nullable=True)
    detected_service = Column(String(40), nullable=True)

    # Routing
    response_source = Column(String(40), nullable=True)  # rag | structured | llm | connector | clarification | welcome | navigation
    route_chosen = Column(String(40), nullable=True)     # same as planner action
    cache_hit = Column(Boolean, default=False)

    # Latency
    response_time_ms = Column(Integer, nullable=True)
    planner_latency_ms = Column(Integer, nullable=True)
    rag_latency_ms = Column(Integer, nullable=True)
    llm_latency_ms = Column(Integer, nullable=True)

    # Conversation flow
    clarification_count = Column(Integer, default=0)
    conversation_completed = Column(Boolean, default=False)
    conversation_abandoned = Column(Boolean, default=False)

    # Feature usage
    service_requested = Column(String(40), nullable=True)
    knowledge_sync_used = Column(Boolean, default=False)
    rag_used = Column(Boolean, default=False)
    structured_lookup_used = Column(Boolean, default=False)
    llm_used = Column(Boolean, default=False)

    # Query understanding
    query_corrected = Column(Boolean, default=False)
    query_original = Column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_interaction_ts_programme", "timestamp", "detected_programme"),
        Index("ix_interaction_ts_service", "timestamp", "detected_service"),
        Index("ix_interaction_ts_topic", "timestamp", "detected_topic"),
        Index("ix_interaction_source", "response_source"),
    )


class AnalyticsSession(Base):
    """An anonymous session — tracks conversation boundaries."""

    __tablename__ = "analytics_sessions"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    anon_session_id = Column(String(64), unique=True, nullable=False, index=True)
    conversation_ids = Column(JSON, default=list)
    message_count = Column(Integer, default=0)
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_activity = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed = Column(Boolean, default=False)
    abandoned = Column(Boolean, default=False)


class AggregatedMetric(Base):
    """Pre-computed aggregation — avoids expensive real-time queries."""

    __tablename__ = "aggregated_metrics"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    period = Column(String(10), nullable=False, index=True)  # daily | weekly | monthly | yearly
    period_start = Column(DateTime(timezone=True), nullable=False, index=True)
    period_end = Column(DateTime(timezone=True), nullable=False)

    # Overview
    total_conversations = Column(Integer, default=0)
    total_messages = Column(Integer, default=0)
    unique_sessions = Column(Integer, default=0)
    avg_response_time_ms = Column(Float, default=0)
    avg_conversation_length = Column(Float, default=0)
    avg_planner_latency_ms = Column(Float, default=0)
    avg_rag_latency_ms = Column(Float, default=0)
    avg_llm_latency_ms = Column(Float, default=0)
    cache_hit_ratio = Column(Float, default=0)
    completion_rate = Column(Float, default=0)
    clarification_rate = Column(Float, default=0)

    # Service usage counts (JSON blobs for flexibility)
    service_usage = Column(JSON, default=dict)
    programme_mentions = Column(JSON, default=dict)
    college_mentions = Column(JSON, default=dict)
    topic_mentions = Column(JSON, default=dict)
    response_source_counts = Column(JSON, default=dict)
    query_correction_count = Column(Integer, default=0)

    # Performance percentiles (computed from performance_samples)
    p50_response_time_ms = Column(Float, nullable=True)
    p90_response_time_ms = Column(Float, nullable=True)
    p99_response_time_ms = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_agg_period_start", "period", "period_start"),
    )


class PerformanceSample(Base):
    """Raw latency sample for percentile calculations."""

    __tablename__ = "performance_samples"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    stage = Column(String(40), nullable=False, index=True)
    latency_ms = Column(Integer, nullable=False)
    anon_session_id = Column(String(64), nullable=True)


class KnowledgeGap(Base):
    """Automatically detected knowledge gap."""

    __tablename__ = "knowledge_gaps"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    detected_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    gap_type = Column(String(40), nullable=False, index=True)
    # Types: unanswered_question | low_confidence | repeated_clarification | missing_document
    query_text = Column(String(500), nullable=True)
    confidence_score = Column(Float, nullable=True)
    frequency = Column(Integer, default=1)
    suggestion = Column(Text, nullable=True)
    resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_kg_type_freq", "gap_type", "frequency"),
    )
