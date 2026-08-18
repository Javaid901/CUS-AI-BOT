"""
backend/app/grievance/service.py

PHASE 3 — Grievance status-history foundation.

This module is the ONLY way the backend mutates a grievance's status. History
rows are append-only (immutable): a transition writes a NEW row and updates the
grievance's `status`; previous rows are never modified or deleted. There is no
update/delete path for history anywhere in the codebase, and no public endpoint
exposes one yet (dashboards arrive in later phases).

Rendering/transition policy (which transitions are allowed, SLA timestamps,
notifications) belongs to later phases — this phase only guarantees the
immutable record-keeping contract:

  GRIEVANCE (current_status) ──┬── status_history[0]  (oldest)
                               ├── status_history[1]
                               └── ... every change preserved, with actor
"""

from __future__ import annotations

from app.grievance.models import (
    GRIEVANCE_CHANGED_BY_ROLES,
    GRIEVANCE_STATUSES,
    Grievance,
    GrievanceStatusHistory,
)
from sqlalchemy.orm import Session


def record_status_change(
    db: Session,
    grievance: Grievance,
    new_status: str,
    changed_by: str,
    changed_by_role: str,
    comment: str | None = None,
    is_internal: bool = True,
) -> GrievanceStatusHistory:
    """Append an immutable status-history entry and advance the grievance status.

    Raises ValueError for statuses/roles outside the Phase-1 enums. The previous
    state is captured from the grievance itself, so replays always produce a
    correct chain even if records were created before this service existed.
    """
    if new_status not in GRIEVANCE_STATUSES:
        raise ValueError(f"Invalid status: {new_status!r} (allowed: {', '.join(GRIEVANCE_STATUSES)})")
    if changed_by_role not in GRIEVANCE_CHANGED_BY_ROLES:
        raise ValueError(f"Invalid changed_by_role: {changed_by_role!r} (allowed: {', '.join(GRIEVANCE_CHANGED_BY_ROLES)})")

    previous_status = grievance.status

    entry = GrievanceStatusHistory(
        grievance_id=grievance.id,
        previous_status=previous_status,
        new_status=new_status,
        changed_by=changed_by,
        changed_by_role=changed_by_role,
        comment=comment,
        is_internal=is_internal,
    )
    db.add(entry)
    grievance.status = new_status
    db.commit()
    db.refresh(entry)
    return entry


def list_history(db: Session, grievance: Grievance) -> list[GrievanceStatusHistory]:
    """Return the immutable history chain, oldest first."""
    return (
        db.query(GrievanceStatusHistory)
        .filter(GrievanceStatusHistory.grievance_id == grievance.id)
        .order_by(GrievanceStatusHistory.created_at, GrievanceStatusHistory.id)
        .all()
    )


__all__ = ["list_history", "record_status_change"]
