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
from app.authority.models import Authority, GrievanceCategory

# Grievance models — re-exported for convenience and registered on metadata
from app.grievance.models import (
    Grievance,
    GrievanceAttachment,
    GrievanceNotification,
    GrievanceStatusHistory,
)

# Academic catalogue models — imported to register on Base.metadata
from app.catalogue.models import (
    AcademicScheme,
    CurriculumDocument,
    LearningOutcome,
    MinorDiscipline,
    Programme,
    ProgrammeCategory,
    ProgrammeSubject,
)

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
from app.models.website_sync import CrawlRun, WebsitePage, WebsitePageVersion

__all__ = [
    "AcademicScheme",
    "AggregatedMetric",
    "AnalyticsSession",
    "AuditLog",
    # Authority
    "Authority",
    # Demo
    "BacklogStatus",
    "Conversation",
    "CourseRegistration",
    "CurriculumDocument",
    "Document",
    "DocumentChunk",
    "FeeReceipt",
    # Grievances
    "Grievance",
    "GrievanceAttachment",
    "GrievanceCategory",
    "GrievanceNotification",
    "GrievanceStatusHistory",
    "HelpdeskTicket",
    # Analytics
    "InteractionEvent",
    "KnowledgeGap",
    "LearningOutcome",
    "Message",
    "MigrationCertificate",
    "MinorDiscipline",
    "PerformanceSample",
    "Programme",
    "ProgrammeCategory",
    "ProgrammeSubject",
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
    "WebsitePage",
    "WebsitePageVersion",
    "CrawlRun",
    "XeroxRequest",
]
