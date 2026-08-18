"""
backend/app/services/registry.py

Service connector registry — maps service names to connector instances.

Supports both demo connectors (for development/testing) and university
connectors (for production mode with real portal integration).

Mode is determined by ENV: STUDENT_SERVICES_MODE=real|demo (default: demo).
"""

from __future__ import annotations

import os
from typing import Any

# ---------------------------------------------------------------------------
# Mode configuration
# ---------------------------------------------------------------------------

_MODE: str | None = None


def _get_mode() -> str:
    """Return the current student services mode."""
    global _MODE
    if _MODE is None:
        _MODE = os.getenv("STUDENT_SERVICES_MODE", "demo").lower()
    if _MODE not in ("demo", "real"):
        raise ValueError(f"Invalid STUDENT_SERVICES_MODE: {_MODE!r}. Use 'demo' or 'real'.")
    return _MODE


# ---------------------------------------------------------------------------
# Human-readable service name set — used for intent detection
# (imported lazily per mode so the set is identical in both modes)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Registry — maps service name → connector instance
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Any] = {}


def _register(connector: Any) -> None:
    """Register a connector instance keyed by its .name."""
    global _REGISTRY
    _REGISTRY[connector.name] = connector


def _clear_registry() -> None:
    global _REGISTRY
    _REGISTRY = {}


def get_connector(name: str) -> Any | None:
    """Get a connector by its machine-readable name.

    The connector class loaded depends on the current MODE.
    """
    # Ensure registry is populated for the current mode
    _populate_registry()
    return _REGISTRY.get(name)


def get_connector_by_display(display: str) -> Any | None:
    """Get a connector by its display name (case-insensitive)."""
    _populate_registry()
    for conn in _REGISTRY.values():
        if conn.display_name.lower() == display.lower():
            return conn
    return None


def list_services() -> list[dict[str, Any]]:
    """List all registered services (for admin / info display)."""
    _populate_registry()
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


def _populate_registry() -> None:
    """Populate _REGISTRY with the appropriate connector set for the current mode."""
    global _REGISTRY
    mode = _get_mode()
    _clear_registry()

    if mode == "demo":
        from app.services.demo_connectors import (  # noqa: F401, E501, F811
            AdmitCardConnector,
            AttendanceConnector,
            BacklogConnector,
            DegreeConnector,
            ExamFormConnector,
            FeeConnector,
            MigrationConnector,
            ProfileConnector,
            ReEvaluationConnector,
            ResultsConnector,
            RegistrationConnector,
            SemesterAdmissionConnector,
            TranscriptConnector,
            XeroxCopyConnector,
            HelpdeskConnector,
        )
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
    elif mode == "real":
        from app.services.university_connectors import (  # noqa: F401, E501, F811
            AdmitCardConnector as UniAdmitCardConnector,
            AttendanceConnector as UniAttendanceConnector,
            BacklogConnector as UniBacklogConnector,
            DegreeConnector as UniDegreeConnector,
            ExamFormConnector as UniExamFormConnector,
            FeeConnector as UniFeeConnector,
            MigrationConnector as UniMigrationConnector,
            ProfileConnector as UniProfileConnector,
            ReEvaluationConnector as UniReEvaluationConnector,
            ResultsConnector as UniResultsConnector,
            RegistrationConnector as UniRegistrationConnector,
            SemesterAdmissionConnector as UniSemesterAdmissionConnector,
            TranscriptConnector as UniTranscriptConnector,
            XeroxCopyConnector as UniXeroxCopyConnector,
            HelpdeskConnector as UniHelpdeskConnector,
        )
        _register(UniResultsConnector())
        _register(UniAdmitCardConnector())
        _register(UniExamFormConnector())
        _register(UniAttendanceConnector())
        _register(UniFeeConnector())
        _register(UniRegistrationConnector())
        _register(UniMigrationConnector())
        _register(UniProfileConnector())
        _register(UniReEvaluationConnector())
        _register(UniResultsConnector())
        _register(UniSemesterAdmissionConnector())
        _register(UniTranscriptConnector())
        _register(UniXeroxCopyConnector())
        _register(UniHelpdeskConnector())