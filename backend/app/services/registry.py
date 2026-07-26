"""
backend/app/services/registry.py

Service connector registry — maps service names to connector instances.

Uses demo/data-backed connectors (from demo_connectors.py) instead of
the placeholder stubs, so all student services return real synthetic data.

To add a new service:
   1. Create a connector class extending ServiceConnector in a new file
   2. Import and register it below

The orchestrator discovers services through this registry.
"""

from __future__ import annotations

from typing import Any

from app.services.base import ServiceConnector
from app.services.demo_connectors import (
    AdmitCardConnector,
    AttendanceConnector,
    BacklogConnector,
    DegreeConnector,
    ExamFormConnector,
    FeeConnector,
    HelpdeskConnector,
    MigrationConnector,
    ProfileConnector,
    ReEvaluationConnector,
    RegistrationConnector,
    ResultsConnector,
    SemesterAdmissionConnector,
    TranscriptConnector,
    XeroxCopyConnector,
)

# ---------------------------------------------------------------------------
# Registry — maps service name → connector instance
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ServiceConnector] = {}

# Human-readable service name set — used for intent detection
SERVICE_NAMES: dict[str, str] = {
    "results": "Results",
    "admit_card": "Admit Card",
    "exam_form": "Exam Form",
    "attendance": "Attendance / Internal Marks",
    "fee": "Fee Receipt",
    "registration": "Course Registration",
    "migration": "Migration Certificate",
    "transcript": "Transcript",
    "degree": "Degree Status",
    "backlog": "Backlog Status",
    "profile": "Student Profile",
    "re_evaluation": "Re-evaluation / Rechecking",
    "xerox_copy": "Xerox / Photocopy",
    "semester_admission": "Semester Admission",
    "helpdesk": "Helpdesk / Support",
}


def _register(connector: ServiceConnector) -> None:
    _REGISTRY[connector.name] = connector


def get_connector(name: str) -> ServiceConnector | None:
    """Get a connector by its machine-readable name."""
    return _REGISTRY.get(name)


def get_connector_by_display(display: str) -> ServiceConnector | None:
    """Get a connector by its display name (case-insensitive)."""
    for conn in _REGISTRY.values():
        if conn.display_name.lower() == display.lower():
            return conn
    return None


def list_services() -> list[dict[str, Any]]:
    """List all registered services (for admin / info display)."""
    return [
        {
            "name": conn.name,
            "display_name": conn.display_name,
            "description": conn.description,
            "requires_auth": conn.requires_auth,
        }
        for conn in _REGISTRY.values()
    ]


def get_service_options() -> list[dict[str, str]]:
    """Return all services as options for the frontend."""
    return [
        {"id": name, "label": display}
        for name, display in SERVICE_NAMES.items()
    ]


# ---------------------------------------------------------------------------
# Register all connectors
# ---------------------------------------------------------------------------

_register(ResultsConnector())
_register(AdmitCardConnector())
_register(ExamFormConnector())
_register(AttendanceConnector())
_register(FeeConnector())
_register(RegistrationConnector())
_register(MigrationConnector())
_register(TranscriptConnector())
_register(DegreeConnector())
_register(BacklogConnector())
_register(ProfileConnector())
_register(ReEvaluationConnector())
_register(XeroxCopyConnector())
_register(SemesterAdmissionConnector())
_register(HelpdeskConnector())
