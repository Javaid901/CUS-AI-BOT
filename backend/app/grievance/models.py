"""
backend/app/grievance/models.py

SQLAlchemy models for the Student Grievance System (PHASE 1 — database foundation).

Tables:
  grievances              - a student grievance record (pre-login capable).
  grievance_status_history - audit trail of every status transition.
  grievance_attachments    - minimal attachment relationship (upload flow is a
                             later phase; only the storage shape exists now).

Design notes:
  * `student_id` (students) / `user_id` (users) are optional; a grievance may
    exist before it is claimed by a verified account (pre-login submissions).
  * `authority_id` reuses the existing `authorities` table — no duplicate
    authority concept is created.
  * Status lifecycle: draft -> submitted -> acknowledged -> in_progress ->
    resolved -> closed | rejected (transitions enforced in later phases).
  * The public reference number (`reference`) is generated in a later phase;
    the column exists now with a unique index and is nullable until then.
  * Pre-login submissions store only self-reported identification (name, roll
    number, email, phone, programme) plus a ONE-TIME tracking token digest so
    students can check status without any account; the plaintext token is
    returned only at submission time and never persisted.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base, _UUID, utcnow

# Status lifecycle (see phase plan). Transitions are NOT enforced at DB level yet.
GRIEVANCE_STATUSES = [
    "draft",
    "submitted",
    "acknowledged",
    "in_progress",
    "resolved",
    "closed",
    "rejected",
]

# Priority levels used by the grievance system.
GRIEVANCE_PRIORITIES = ["low", "normal", "high", "urgent"]

# Role labels used in status history / messages.
GRIEVANCE_CHANGED_BY_ROLES = ["student", "super_admin", "authority_admin", "system"]

DEFAULT_STATUS = "draft"
DEFAULT_PRIORITY = "normal"


class Grievance(Base):
    __tablename__ = "grievances"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Public reference number, e.g. CUS-GRV-2026-000001. Assigned by a later
    # phase; NULL until the reference-generator runs. Unique once assigned.
    reference = Column(String(36), unique=True, index=True, nullable=True)

    # Routing target (existing authorities table; no duplicate concept).
    authority_id = Column(
        String(36),
        ForeignKey("authorities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ----- Student / account identity (all optional: pre-login support) -----
    student_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("students.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ----- Student self-reported details (filled by the submission flow) -----
    student_name = Column(String(200), nullable=True)
    roll_number = Column(String(50), nullable=True, index=True)
    semester = Column(String(20), nullable=True)
    college = Column(String(200), nullable=True)
    student_email = Column(String(200), nullable=True, index=True)
    programme = Column(String(50), nullable=True)
    phone = Column(String(30), nullable=True)

    # ----- Submission metadata -----
    # Where the grievance entered the system: "pre_login" (public intake form,
    # never authenticated) or "student" (claimed through a verified account).
    source_kind = Column(String(20), default="pre_login", nullable=False)
    # BEST-EFFORT email delivery result: None | "sent" | "failed". Never
    # stores secrets; failures must never block the submission itself.
    email_status = Column(String(20), nullable=True)
    # BEST-EFFORT delivery result for the email sent to the SELECTED AUTHORITY:
    # None | "sent" | "failed" | "unavailable" (authority has no usable email).
    authority_email_status = Column(String(20), nullable=True)
    # SHA-256 of the per-grievance tracking token. Only the digest is stored —
    # the plaintext token is returned exactly once at submission time.
    tracking_token_hash = Column(String(128), unique=True, nullable=True, index=True)
    # Idempotency key supplied by the client (double-click / browser / network
    # retries). Re-submitting with the same key returns the original receipt
    # instead of creating a second grievance. Unique across submissions.
    client_request_id = Column(String(64), unique=True, nullable=True, index=True)

    # ----- Grievance content -----
    category = Column(String(100), nullable=True)  # subject / category e.g. academics, hostel, fee
    original_student_input = Column(Text, nullable=True)  # raw text as the student wrote it
    generated_formal_grievance = Column(Text, nullable=True)  # AI-normalized draft (reviewed by student)
    final_grievance_text = Column(Text, nullable=True)  # reviewed text the student approved

    # ----- Lifecycle / SLA timestamp fields -----
    status = Column(String(20), default=DEFAULT_STATUS, nullable=False, index=True)
    priority = Column(String(20), default=DEFAULT_PRIORITY, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    # ----- Read / unread state (WhatsApp-style, INDEPENDENT of workflow status)
    # One read-state per grievance at the authority level: every admin bound to
    # the grievance's authority shares that read state (per-authority semantics,
    # consistent with the authority-scoped portal).
    is_read = Column(Boolean, default=False, nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)
    read_by = Column(String(200), nullable=True)  # display name of first reader

    # ----- Authority official response (single response per grievance) -----
    authority_response = Column(Text, nullable=True)
    authority_response_at = Column(DateTime(timezone=True), nullable=True)
    # BEST-EFFORT delivery of the response to the student: None|"sent"|"failed"|"unavailable"
    response_email_status = Column(String(20), nullable=True)

    # Relationships
    student = relationship("Student")
    user = relationship("User")
    authority = relationship("Authority")
    status_history = relationship(
        "GrievanceStatusHistory",
        back_populates="grievance",
        cascade="all, delete-orphan",
        order_by="GrievanceStatusHistory.created_at",
    )
    attachments = relationship(
        "GrievanceAttachment",
        back_populates="grievance",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Grievance {self.reference or self.id} status={self.status}>"


class GrievanceStatusHistory(Base):
    """Audit trail for grievance status transitions.

    Authority-internal notes are tracked separately from the student-visible
    grievance content: `comment` here is guidance/notes for staff, and
    `is_internal` marks whether the note must never be shown to students.
    """

    __tablename__ = "grievance_status_history"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grievance_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("grievances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    previous_status = Column(String(20), nullable=True)
    new_status = Column(String(20), nullable=False)
    changed_by = Column(String(200), nullable=True)  # display name or system identifier
    changed_by_role = Column(String(30), nullable=True)  # student | super_admin | authority_admin | system
    comment = Column(Text, nullable=True)  # internal staff note / reason
    is_internal = Column(Boolean, default=True, nullable=False)  # True => never student-visible
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    grievance = relationship("Grievance", back_populates="status_history")

    def __repr__(self) -> str:
        return f"<GrievanceStatusHistory {self.previous_status}->{self.new_status}>"


class GrievanceAttachment(Base):
    """Minimal attachment shell for grievances (file upload is a later phase)."""

    __tablename__ = "grievance_attachments"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grievance_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("grievances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename = Column(String(255), nullable=True)  # original filename as the student provided
    stored_path = Column(String(500), nullable=True)  # relative path under uploads dir
    file_type = Column(String(20), nullable=True)  # e.g. pdf, jpg
    file_size = Column(Integer, nullable=True)  # bytes
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    grievance = relationship("Grievance", back_populates="attachments")

    def __repr__(self) -> str:
        return f"<GrievanceAttachment {self.filename}>"


# Notification event types recorded in `grievance_notifications`.
NOTIFICATION_EVENT_TYPES = [
    "grievance_submitted",      # student confirmation  + authority alert
    "grievance_acknowledged",   # student (status -> acknowledged)
    "grievance_response",       # student (official response)
    "grievance_resolved",       # student (status -> resolved)
]

# Recipient roles recorded in `grievance_notifications`.
NOTIFICATION_RECIPIENT_ROLES = ["student", "authority"]

# Notification delivery outcomes (mirrors the existing best-effort vocabulary).
NOTIFICATION_STATUSES = ["sent", "failed", "skipped"]


class GrievanceNotification(Base):
    """Delivery log for automatic grievance notifications.

    One row per (grievance, event_type, recipient_role) — the unique key makes
    notification events idempotent: a retried acknowledgement or resolution
    operation can never produce a duplicate student email. Rows are appended
    AFTER the grievance state is committed, so a delivery failure can never
    roll the grievance back. Stores no secrets: recipient email, delivery
    outcome, timestamps and failure text only.
    """

    __tablename__ = "grievance_notifications"
    __table_args__ = (
        UniqueConstraint(
            "grievance_id", "event_type", "recipient_role",
            name="uq_grievance_notification_event",
        ),
    )

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grievance_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("grievances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # event_type: NOTIFICATION_EVENT_TYPES; recipient_role: NOTIFICATION_RECIPIENT_ROLES.
    event_type = Column(String(40), nullable=False)
    recipient_role = Column(String(20), nullable=False)
    recipient_email = Column(String(200), nullable=True)  # DB authority email / grievance student email
    # Delivery outcome (does NOT reflect the grievance operation itself):
    # sent | failed | skipped.
    status = Column(String(20), nullable=False)
    retry_count = Column(Integer, default=0, nullable=False)  # total attempts so far
    attempted_at = Column(DateTime(timezone=True), nullable=True)  # last attempt
    sent_at = Column(DateTime(timezone=True), nullable=True)  # accepted by SMTP
    # Provider/message id when the provider exposes one (SMTP smtplib does
    # not) — reserved for provider-API integrations. Never contains secrets.
    provider_message_id = Column(String(200), nullable=True)
    error_message = Column(Text, nullable=True)  # failure detail (never secrets)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    grievance = relationship("Grievance")

    def __repr__(self) -> str:
        return (
            f"<GrievanceNotification {self.event_type} "
            f"{self.recipient_role}={self.status} attempts={self.retry_count}>"
        )


__all__ = [
    "DEFAULT_PRIORITY",
    "DEFAULT_STATUS",
    "GRIEVANCE_CHANGED_BY_ROLES",
    "GRIEVANCE_PRIORITIES",
    "GRIEVANCE_STATUSES",
    "NOTIFICATION_EVENT_TYPES",
    "NOTIFICATION_RECIPIENT_ROLES",
    "NOTIFICATION_STATUSES",
    "Grievance",
    "GrievanceAttachment",
    "GrievanceNotification",
    "GrievanceStatusHistory",
]

# Register the string-referenced relationship targets (Student/User) so this
# module can also be imported standalone (e.g. by one-off maintenance scripts).
from app.models.db_models import Student as _Student  # noqa: E402, F401
from app.models.db_models import User as _User  # noqa: E402, F401