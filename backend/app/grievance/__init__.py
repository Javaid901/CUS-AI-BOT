"""
backend/app/grievance/__init__.py

Student Grievance System — PHASE 1 (database foundation).

Re-exports ORM models for convenient imports and ensures they are registered
on the declarative metadata.
"""

from app.grievance.models import (
    GRIEVANCE_CHANGED_BY_ROLES,
    GRIEVANCE_PRIORITIES,
    GRIEVANCE_STATUSES,
    Grievance,
    GrievanceAttachment,
    GrievanceStatusHistory,
)

__all__ = [
    "GRIEVANCE_CHANGED_BY_ROLES",
    "GRIEVANCE_PRIORITIES",
    "GRIEVANCE_STATUSES",
    "Grievance",
    "GrievanceAttachment",
    "GrievanceStatusHistory",
]