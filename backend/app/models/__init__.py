"""
backend/app/models/__init__.py

Re-exports ORM models for convenient imports and ensures they are registered
on the declarative metadata.
"""

# Analytics models — imported to register on Base.metadata
from app.analytics.models import (
    AggregatedMetric,
    AnalyticsSession,
    InteractionEvent,
    KnowledgeGap,
    PerformanceSample,
)

# Authority model
from app.authority.models import Authority
from app.models.db_models import (
    AuditLog,
    Conversation,
    Document,
    DocumentChunk,
    Message,
    RefreshToken,
    Student,
    StudentSession,
    User,
)

# Demo models — imported to register on Base.metadata
from app.models.demo_models import (
    BacklogStatus,
    CourseRegistration,
    FeeReceipt,
    HelpdeskTicket,
    MigrationCertificate,
    Revaluation,
    StudentAdmitCard,
    StudentAttendance,
    StudentExamForm,
    StudentResult,
    StudentTranscript,
    XeroxRequest,
)
from app.models.sync_source import SyncSource

__all__ = [
    "AggregatedMetric",
    "AnalyticsSession",
    "AuditLog",
    # Authority
    "Authority",
    # Demo
    "BacklogStatus",
    "Conversation",
    "CourseRegistration",
    "Document",
    "DocumentChunk",
    "FeeReceipt",
    "HelpdeskTicket",
    # Analytics
    "InteractionEvent",
    "KnowledgeGap",
    "Message",
    "MigrationCertificate",
    "PerformanceSample",
    "RefreshToken",
    "Revaluation",
    "Student",
    "StudentAdmitCard",
    "StudentAttendance",
    "StudentExamForm",
    "StudentResult",
    "StudentSession",
    "StudentTranscript",
    "SyncSource",
    "User",
    "XeroxRequest",
]
