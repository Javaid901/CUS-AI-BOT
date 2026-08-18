"""
backend/app/authority_admin/portal.py

Authority Admin self-service portal business logic (PHASE 6).

Scope rule (applies to EVERY function in this module):
  * The effective authority is ALWAYS derived from the authenticated user's
    users.authority_id — never from any client-supplied value (query params,
    path ids, request bodies and forged headers are all ignored for scope).
  * A grievance is only reachable when its authority_id matches the admin's
    authority. Anything else returns None, which routes surface as 404 — the
    existence of another authority's grievance is never leaked.

Status mutations go through app.grievance.service.record_status_change — the
only sanctioned mutation path — so every change lands in the immutable
grievance_status_history chain with actor / role / timestamp. No route in this
portal ever writes grievance.status directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.grievance.models import GRIEVANCE_STATUSES, Grievance
from app.grievance.notifications import notify_status_change, record_response_delivery
from app.grievance.service import list_history, record_status_change
from app.models import User
from app.utils.email import send_grievance_response
from app.utils.logging import audit

# Workflow statuses an Authority Admin may set (a subset of the existing
# GRIEVANCE_STATUSES vocabulary — "draft" is never admin-visible/settable).
PORTAL_STATUSES = [
    s for s in GRIEVANCE_STATUSES if s not in ("draft",)
]

DEFAULT_PAGE_SIZE = 15
MAX_PAGE_SIZE = 50

_STATUS_LABELS = {
    "submitted": "Submitted",
    "acknowledged": "Acknowledged",
    "in_progress": "In Progress",
    "resolved": "Resolved",
    "closed": "Closed",
    "rejected": "Rejected",
}


def scope_authority_id(user: User) -> str:
    """The single authoritative scope for an Authority Admin account."""
    if not user.authority_id:
        raise ValueError("No authority is assigned to this account")
    return str(user.authority_id)


def actor_label(user: User) -> str:
    return (user.full_name or "").strip() or user.username


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _grievance_view(g: Grievance, history: bool = False) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": str(g.id),
        "reference": g.reference,
        "student_name": g.student_name,
        "roll_number": g.roll_number,
        "college": g.college,
        "semester": g.semester,
        "category": g.category,
        "status": g.status,
        "status_label": _STATUS_LABELS.get(g.status, g.status.title()),
        "is_read": bool(g.is_read),
        "read_at": _iso(g.read_at),
        "read_by": g.read_by,
        "created_at": _iso(g.created_at),
        "updated_at": _iso(g.updated_at),
        "submitted_at": _iso(g.submitted_at),
        "summary": (g.final_grievance_text or "")[:160],
    }
    if history:
        view.update({
            "student_id": str(g.student_id) if g.student_id else None,
            "student_email": g.student_email,
            "programme": g.programme,
            "phone": g.phone,
            "priority": g.priority,
            "original_student_input": g.original_student_input,
            "final_grievance_text": g.final_grievance_text,
            "authority_id": g.authority_id,
            "email_status": g.email_status,
            "authority_email_status": g.authority_email_status,
            "authority_response": g.authority_response,
            "authority_response_at": _iso(g.authority_response_at),
            "response_email_status": g.response_email_status,
            "resolved_at": _iso(g.resolved_at),
            "closed_at": _iso(g.closed_at),
        })
    return view


def _history_view(h: Any) -> dict[str, Any]:
    return {
        "previous_status": h.previous_status,
        "new_status": h.new_status,
        "changed_by": h.changed_by,
        "changed_by_role": h.changed_by_role,
        "comment": h.comment,
        "is_internal": bool(h.is_internal),
        "created_at": _iso(h.created_at),
    }


# ---------------------------------------------------------------------------
# Read / unread (WhatsApp-style, independent of workflow status)
# ---------------------------------------------------------------------------


def mark_read(db: Session, grievance: Grievance, user: User, ip: str | None = None) -> dict[str, Any]:
    """Mark as read. Idempotent: re-marks never duplicate history or events."""
    if not grievance.is_read:
        grievance.is_read = True
        grievance.read_at = datetime.now(timezone.utc)
        grievance.read_by = actor_label(user)
        db.commit()
        db.refresh(grievance)
        audit(
            db, "grievance.mark_read", actor_id=str(user.id), actor_role=user.role,
            target=grievance.reference, detail=f"Marked read by {actor_label(user)}", ip=ip,
        )
    return {"id": str(grievance.id), "reference": grievance.reference, "is_read": True}


def mark_unread(db: Session, grievance: Grievance, user: User, ip: str | None = None) -> dict[str, Any]:
    """Mark as unread. Idempotent."""
    if grievance.is_read:
        grievance.is_read = False
        db.commit()
        db.refresh(grievance)
        audit(
            db, "grievance.mark_unread", actor_id=str(user.id), actor_role=user.role,
            target=grievance.reference, detail=f"Marked unread by {actor_label(user)}", ip=ip,
        )
    return {"id": str(grievance.id), "reference": grievance.reference, "is_read": False}


# ---------------------------------------------------------------------------
# Workflow status (immutable history via the existing service)
# ---------------------------------------------------------------------------


def change_status(
    db: Session,
    grievance: Grievance,
    new_status: str,
    user: User,
    note: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    """Advance workflow status through record_status_change.

    Raises ValueError -> route maps to 409 for no-op / invalid vocabulary.
    """
    new_status = (new_status or "").strip().lower()
    if new_status not in PORTAL_STATUSES:
        raise ValueError(
            f"Invalid status: {new_status!r} (allowed: {', '.join(PORTAL_STATUSES)})"
        )
    if new_status == grievance.status:
        raise ValueError(f"Grievance is already {grievance.status}")

    record_status_change(
        db,
        grievance,
        new_status=new_status,
        changed_by=actor_label(user),
        changed_by_role="authority_admin",
        comment=(note or "").strip() or None,
        is_internal=True,
    )

    db.refresh(grievance)
    now = datetime.now(timezone.utc)
    if new_status == "resolved" and not grievance.resolved_at:
        grievance.resolved_at = now
    if new_status == "closed" and not grievance.closed_at:
        grievance.closed_at = now
    db.commit()
    db.refresh(grievance)
    audit(
        db, "grievance.status_changed", actor_id=str(user.id), actor_role=user.role,
        target=grievance.reference, detail=f"{new_status} (was {grievance.status})", ip=ip,
    )

    # Automatic notification (status is already committed — the immutable
    # history chain is the source of truth; email is best-effort and logged).
    # Only acknowledged/resolved carry a student email; everything else is a
    # no-op that returns the response shape unchanged.
    notification = notify_status_change(db, grievance, new_status)
    db.commit()
    db.refresh(grievance)
    return {
        "id": str(grievance.id),
        "reference": grievance.reference,
        "status": new_status,
        "notification": notification,
    }


# ---------------------------------------------------------------------------
# Official response + student email
# ---------------------------------------------------------------------------


def add_response(
    db: Session,
    grievance: Grievance,
    response: str,
    user: User,
    authority_name: str,
    ip: str | None = None,
) -> dict[str, Any]:
    """Record the authority's official response and deliver it to the student.

    The response is appended to the immutable history chain (student-visible,
    is_internal=False) and the delivery outcome is recorded honestly in
    `response_email_status`. SMTP failure NEVER drops the response — it is
    stored first, emailed best-effort, and the status reflects delivery.
    """
    response = (response or "").strip()
    if not response or len(response) < 2:
        raise ValueError("A response text is required")
    if len(response) > 5000:
        raise ValueError("Response is too long (max 5000 characters)")
    if grievance.authority_response:
        raise ValueError("A response has already been recorded for this grievance")

    now = datetime.now(timezone.utc)
    grievance.authority_response = response
    grievance.authority_response_at = now
    db.commit()
    db.refresh(grievance)

    # Immutable history entry for the response (previous == new status).
    record_status_change(
        db,
        grievance,
        new_status=grievance.status,
        changed_by=actor_label(user),
        changed_by_role="authority_admin",
        comment=response,
        is_internal=False,
    )
    db.refresh(grievance)

    audit(
        db, "grievance.response_created", actor_id=str(user.id), actor_role=user.role,
        target=grievance.reference, detail="Official response recorded", ip=ip,
    )

    student_email = (grievance.student_email or "").strip()
    if not student_email:
        grievance.response_email_status = "unavailable"
        db.commit()
        audit(
            db, "grievance.email_failed", actor_id=str(user.id), actor_role=user.role,
            target=grievance.reference, detail="Response email skipped: no student email", ip=ip,
        )
        record_response_delivery(
            db, grievance, student_email=None, status="skipped",
            error_message="no student email on record",
        )
        db.commit()
        db.refresh(grievance)
        return {"id": str(grievance.id), "reference": grievance.reference, "response_email_status": "unavailable"}

    sent = send_grievance_response(
        student_email,
        grievance.reference,
        grievance.status,
        authority_name,
        response,
        grievance.authority_response_at,
    )
    grievance.response_email_status = "sent" if sent else "failed"
    db.commit()
    db.refresh(grievance)
    audit(
        db, "grievance.email_sent" if sent else "grievance.email_failed",
        actor_id=str(user.id), actor_role=user.role,
        target=grievance.reference, detail="Response email to student", ip=ip,
    )
    record_response_delivery(
        db, grievance, student_email=student_email,
        status=grievance.response_email_status,
        error_message=None if sent else "response email delivery failed",
    )
    db.commit()
    return {"id": str(grievance.id), "reference": grievance.reference, "response_email_status": grievance.response_email_status}


# ---------------------------------------------------------------------------
# Queries (authority-scoped, backend-enforced)
# ---------------------------------------------------------------------------


def get_grievance(db: Session, authority_id: str, grievance_id: str) -> Grievance | None:
    """Fetch a grievance ONLY when it belongs to the admin's authority."""
    gid = uuid_as_uuid(grievance_id)
    if gid is None:
        return None
    return (
        db.query(Grievance)
        .filter(Grievance.id == gid, Grievance.authority_id == authority_id)
        .first()
    )


def uuid_as_uuid(value: str):
    import uuid
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def open_grievance(
    db: Session,
    grievance: Grievance,
    user: User,
    ip: str | None = None,
) -> dict[str, Any]:
    """Detail view. Opening an unread grievance marks it READ (once)."""
    if not grievance.is_read:
        grievance.is_read = True
        grievance.read_at = datetime.now(timezone.utc)
        grievance.read_by = actor_label(user)
        db.commit()
        db.refresh(grievance)
        audit(
            db, "grievance.opened", actor_id=str(user.id), actor_role=user.role,
            target=grievance.reference, detail="Opened (auto-marked read) by " + actor_label(user), ip=ip,
        )
    view = _grievance_view(grievance, history=True)
    view["history"] = [_history_view(h) for h in list_history(db, grievance)]
    return view


def list_grievances(
    db: Session,
    authority_id: str,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    q: str | None = None,
    status: str | None = None,
    read_state: str | None = None,  # "read" | "unread" | None
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    """Paginated, searchable, filterable grievance list for ONE authority.

    Filtering is enforced by the query (never by the frontend).
    """
    query = db.query(Grievance).filter(Grievance.authority_id == authority_id)

    if q:
        pattern = f"%{(q or '').strip()}%"
        query = query.filter(
            or_(
                Grievance.reference.ilike(pattern),
                Grievance.student_name.ilike(pattern),
                Grievance.roll_number.ilike(pattern),
                Grievance.student_email.ilike(pattern),
                Grievance.category.ilike(pattern),
            )
        )
    if status:
        query = query.filter(Grievance.status == status)
    if read_state == "read":
        query = query.filter(Grievance.is_read.is_(True))
    elif read_state == "unread":
        query = query.filter(Grievance.is_read.is_(False))
    if date_from is not None:
        query = query.filter(Grievance.created_at >= date_from)
    if date_to is not None:
        query = query.filter(Grievance.created_at <= date_to)

    total = query.count()
    unread_total = (
        db.query(Grievance)
        .filter(Grievance.authority_id == authority_id, Grievance.is_read.is_(False))
        .count()
    )
    rows = (
        query.order_by(Grievance.created_at.desc(), Grievance.reference)
        .offset(max(0, page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_grievance_view(g) for g in rows],
        "total": total,
        "unread_total": unread_total,
        "page": page,
        "page_size": page_size,
        "filter": {
            "q": q or "",
            "status": status or "all",
            "read": read_state or "all",
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
        },
    }


def dashboard_stats(db: Session, authority_id: str) -> dict[str, Any]:
    """Counter cards + recent grievances for the dashboard."""
    from sqlalchemy import func

    base = db.query(Grievance).filter(Grievance.authority_id == authority_id)
    total = base.count()
    unread = base.filter(Grievance.is_read.is_(False)).count()
    by_status = {
        row[0]: row[1]
        for row in db.query(Grievance.status, func.count(Grievance.id))
        .filter(Grievance.authority_id == authority_id)
        .group_by(Grievance.status)
        .all()
    }
    recent = (
        db.query(Grievance)
        .filter(Grievance.authority_id == authority_id)
        .order_by(Grievance.created_at.desc())
        .limit(8)
        .all()
    )
    return {
        "total": total,
        "unread": unread,
        "in_progress": by_status.get("in_progress", 0),
        "resolved": by_status.get("resolved", 0),
        "closed": by_status.get("closed", 0),
        "recent": [_grievance_view(g) for g in recent],
    }


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "PORTAL_STATUSES",
    "actor_label",
    "add_response",
    "change_status",
    "dashboard_stats",
    "get_grievance",
    "list_grievances",
    "mark_read",
    "mark_unread",
    "open_grievance",
    "scope_authority_id",
]