"""
backend/app/grievance/notifications.py

Centralized automatic-email notification service for the grievance lifecycle.

Events recorded in the `grievance_notifications` delivery log:

    grievance_submitted    -> student confirmation  (existing sender, intake)
                           -> authority alert       (existing sender, intake)
    grievance_acknowledged -> student               (NEW, triggered on status change)
    grievance_response     -> student               (existing sender, portal)
    grievance_resolved     -> student               (NEW, triggered on status change)

Design contract (additive — the existing email senders, columns, audit events
and API contracts are untouched):
  * Recording NEVER changes the outcome of the underlying operation: the
    grievance state is always committed BEFORE notification work begins, so a
    delivery failure can never roll a grievance, acknowledgement, response or
    resolution back (rule: email is best-effort, state is source of truth).
  * Events are idempotent: the unique (grievance_id, event_type,
    recipient_role) key means a retried operation can never deliver a second
    copy of an already-sent notification. Failed/skipped rows are reused for
    retry attempts (retry_count increments) instead of inserting duplicates.
  * Recipients are never client-supplied: the student address always comes
    from the persisted grievance record, and the authority address always
    comes from the database authority record.
  * No secrets are logged: recipient address, delivery status, timestamps and
    failure text only.
  * No background worker is introduced: delivery is synchronous best-effort
    (the existing design); the delivery log is the recovery record for any
    future retry job.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.grievance.models import Grievance, GrievanceNotification, GrievanceStatusHistory
from app.utils.email import (
    send_grievance_acknowledged,
    send_grievance_resolved,
)

_log = logging.getLogger("cus_ai")

# Event type vocabulary (matches NOTIFICATION_EVENT_TYPES in models).
EVENT_SUBMITTED = "grievance_submitted"
EVENT_ACKNOWLEDGED = "grievance_acknowledged"
EVENT_RESPONSE = "grievance_response"
EVENT_RESOLVED = "grievance_resolved"

# Recipient role vocabulary.
ROLE_STUDENT = "student"
ROLE_AUTHORITY = "authority"

# Status vocabulary (matches NOTIFICATION_STATUSES in models).
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# Status changes that carry an automatic student notification. The existing
# grievance_status_history chain remains the source of truth for the change
# itself; these events only mirror it for email purposes.
_STATUS_NOTIFY_EVENTS = {
    "acknowledged": EVENT_ACKNOWLEDGED,
    "resolved": EVENT_RESOLVED,
}

_NOTIFY_SENDERS = {
    EVENT_ACKNOWLEDGED: send_grievance_acknowledged,
    EVENT_RESOLVED: send_grievance_resolved,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _authority_email(grievance: Grievance, db: Session | None = None) -> str:
    """Authority address ALWAYS from the database authority record (never a
    client-supplied value)."""
    if not grievance.authority_id:
        return ""
    from app.grievance.intake import authority_summary  # deferred: no import cycle

    auth = authority_summary(grievance.authority_id, db=db) or {}
    return (auth.get("email") or "").strip()


def _authority_name(grievance: Grievance, db: Session | None = None) -> str | None:
    if not grievance.authority_id:
        return None
    from app.grievance.intake import authority_summary  # deferred: no import cycle

    auth = authority_summary(grievance.authority_id, db=db) or {}
    return (auth.get("authority_name") or "").strip() or None


def _student_first_name(grievance: Grievance) -> str | None:
    name = (grievance.student_name or "").strip()
    return (name.split(" ")[0] if name else "").strip() or None


# ---------------------------------------------------------------------------
# Ledger primitives
# ---------------------------------------------------------------------------


def find_notification(
    db: Session,
    grievance: Grievance,
    event_type: str,
    recipient_role: str,
) -> GrievanceNotification | None:
    return (
        db.query(GrievanceNotification)
        .filter(
            GrievanceNotification.grievance_id == grievance.id,
            GrievanceNotification.event_type == event_type,
            GrievanceNotification.recipient_role == recipient_role,
        )
        .first()
    )


def log_delivery(
    db: Session,
    grievance: Grievance,
    *,
    event_type: str,
    recipient_role: str,
    recipient_email: str | None,
    status: str,
    attempted: bool = True,
    error_message: str | None = None,
) -> GrievanceNotification:
    """Record one delivery outcome in the notification log.

    Upserts on (grievance_id, event_type, recipient_role): a retry reuses the
    existing row (bumping retry_count) instead of inserting a duplicate — the
    event is idempotent at the storage layer. Never raises; the caller's
    transaction outcome is never affected.
    """
    row = find_notification(db, grievance, event_type, recipient_role)
    if row is None:
        row = GrievanceNotification(
            grievance_id=grievance.id,
            event_type=event_type,
            recipient_role=recipient_role,
            recipient_email=(recipient_email or "").strip() or None,
            status=status,
        )
        db.add(row)
    row.status = status
    row.retry_count = (row.retry_count or 0) + 1
    row.attempted_at = _now() if attempted else None
    row.sent_at = _now() if status == STATUS_SENT else None
    row.error_message = (error_message or "").strip()[:2000] or None
    if status != STATUS_SENT:
        row.sent_at = None
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Event triggers
# ---------------------------------------------------------------------------


def _attempt_delivery(
    db: Session,
    grievance: Grievance,
    *,
    event_type: str,
    recipient_role: str,
    recipient_email: str,
    sender: Callable[[], bool],
    svc_label: str,
) -> str:
    """Idempotent, best-effort send + ledger row. Returns delivery status.

    Never raises and never touches grievance state. Already-sent events short-
    circuit (no duplicate email); failed/skipped rows are re-attempted.
    """
    if not recipient_email:
        log_delivery(
            db, grievance,
            event_type=event_type, recipient_role=recipient_role,
            recipient_email=None, status=STATUS_SKIPPED,
            attempted=False, error_message="no recipient email on record",
        )
        return STATUS_SKIPPED

    try:
        delivered = bool(sender())
    except Exception as exc:  # noqa: BLE001 — best-effort by design
        _log.warning("notification %s failed for %s: %s", event_type, recipient_email, exc)
        delivered = False

    status = STATUS_SENT if delivered else STATUS_FAILED
    log_delivery(
        db, grievance,
        event_type=event_type, recipient_role=recipient_role,
        recipient_email=recipient_email, status=status,
        attempted=True,
        error_message=None if delivered else f"{svc_label} delivery failed",
    )
    return status


def record_submission_deliveries(
    db: Session,
    grievance: Grievance,
    *,
    student_email: str | None,
    student_status: str | None,
    authority_email: str | None,
    authority_status: str | None,
) -> None:
    """Ledger entries for the EXISTING submission emails (sent by intake).

    This recorder is intentionally passive: it captures the outcome the
    existing submission flow already produced (grievance.email_status /
    authority_email_status) into the notification log. No email is sent here
    and no existing behavior changes.
    """
    if student_email and student_status:
        log_delivery(
            db, grievance,
            event_type=EVENT_SUBMITTED, recipient_role=ROLE_STUDENT,
            recipient_email=student_email, status=student_status,
            error_message=None if student_status == STATUS_SENT
            else "student submission acknowledgement delivery failed",
        )
    if authority_email and authority_status and authority_status != STATUS_SKIPPED:
        log_delivery(
            db, grievance,
            event_type=EVENT_SUBMITTED, recipient_role=ROLE_AUTHORITY,
            recipient_email=authority_email, status=authority_status,
            error_message=None if authority_status == STATUS_SENT
            else "authority new-grievance alert delivery failed",
        )


def record_response_delivery(
    db: Session,
    grievance: Grievance,
    *,
    student_email: str | None,
    status: str,
    error_message: str | None = None,
) -> None:
    """Ledger entry for the EXISTING response email (sent by portal.add_response).

    Passive recorder — the existing sender and response_email_status column
    are untouched.
    """
    if student_email:
        log_delivery(
            db, grievance,
            event_type=EVENT_RESPONSE, recipient_role=ROLE_STUDENT,
            recipient_email=student_email, status=status,
            error_message=error_message,
        )


def notify_status_change(
    db: Session,
    grievance: Grievance,
    new_status: str,
) -> dict[str, Any] | None:
    """Automatic student notification for acknowledged/resolved transitions.

    Called AFTER the status change has been committed (the existing
    record_status_change chain is the source of truth). Adds the missing
    acknowledgement / resolution emails and records them in the delivery log.
    Returns {"event_type": ..., "recipient_role": ..., "recipient_email": ...,
    "status": ...} or None when the transition carries no notification.
    Idempotent: a re-invocation for an already-sent event sends nothing.
    """
    event_type = _STATUS_NOTIFY_EVENTS.get((new_status or "").strip().lower())
    if event_type is None:
        return None

    existing = find_notification(db, grievance, event_type, ROLE_STUDENT)
    if existing is not None and existing.status == STATUS_SENT:
        return {
            "event_type": event_type,
            "recipient_role": ROLE_STUDENT,
            "recipient_email": existing.recipient_email,
            "status": STATUS_SENT,
            "deduplicated": True,
        }

    # Status-change timestamps come from the immutable status-history chain
    # (the sanctioned source of truth), never from the client.
    event_at: Any = None
    hist = (
        db.query(GrievanceStatusHistory)
        .filter(
            GrievanceStatusHistory.grievance_id == grievance.id,
            GrievanceStatusHistory.new_status == (new_status or "").strip().lower(),
        )
        .order_by(GrievanceStatusHistory.created_at.desc())
        .first()
    )
    if hist is not None:
        event_at = hist.created_at

    student_email = (grievance.student_email or "").strip()
    sender = _NOTIFY_SENDERS[event_type]
    if student_email:
        first_name = _student_first_name(grievance)

        def _send() -> bool:
            return sender(
                student_email,
                grievance.reference,
                _authority_name(grievance, db=db),
                first_name,
                event_at,
                (grievance.authority_response or "").strip() or None
                if event_type == EVENT_RESOLVED else None,
            )

        status = _attempt_delivery(
            db, grievance,
            event_type=event_type, recipient_role=ROLE_STUDENT,
            recipient_email=student_email, sender=_send,
            svc_label=event_type,
        )
    else:
        status = STATUS_SKIPPED
        log_delivery(
            db, grievance,
            event_type=event_type, recipient_role=ROLE_STUDENT,
            recipient_email=None, status=STATUS_SKIPPED,
            attempted=False, error_message="no student email on record",
        )

    return {
        "event_type": event_type,
        "recipient_role": ROLE_STUDENT,
        "recipient_email": student_email or None,
        "status": status,
        "deduplicated": False,
    }


__all__ = [
    "EVENT_ACKNOWLEDGED",
    "EVENT_RESPONSE",
    "EVENT_RESOLVED",
    "EVENT_SUBMITTED",
    "ROLE_AUTHORITY",
    "ROLE_STUDENT",
    "STATUS_FAILED",
    "STATUS_SENT",
    "STATUS_SKIPPED",
    "find_notification",
    "log_delivery",
    "notify_status_change",
    "record_response_delivery",
    "record_submission_deliveries",
]