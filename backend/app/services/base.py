"""
backend/app/services/base.py

Abstract base class for all university service connectors.

All student-facing services (Results, Admit Card, Exam Form, Attendance, etc.)
MUST extend ServiceConnector and implement authenticate() and fetch().

This ensures a clean, replaceable interface. When the university provides
official APIs, swap the connector implementation — no chatbot code changes needed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ServiceResult:
    """Standard result type returned by all connector methods."""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    message: str | None = None


class ServiceConnector(ABC):
    """
    Abstract connector for a student-facing university service.

    Every connector must provide:
      - name:       unique machine-readable identifier (e.g. "results")
      - display_name: human-readable name (e.g. "Results")
      - description: what this service does

    Lifecycle:
      1. authenticate(reg_no, password) → ServiceResult
         - Validates credentials against the university system
         - Returns a session token on success
         - The orchestrator stores the token; it is NEVER persisted to disk/DB

      2. fetch(session_token, params) → ServiceResult
         - Retrieves the actual service data
         - Called only after authentication succeeds
         - Must handle expired sessions and return appropriate errors

    Security rules (MUST follow):
      - NEVER log, store, or cache registration numbers or passwords
      - NEVER write credentials to localStorage, sessionStorage, SQLite, or ChromaDB
      - Session tokens may be stored in memory only (orchestrator state)
      - Destroy session tokens on logout or timeout
    """

    name: str = ""
    display_name: str = ""
    description: str = ""

    @abstractmethod
    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        """
        Authenticate a student against the university portal.

        Args:
            reg_no:  Student registration number
            password: Student password / PIN

        Returns:
            ServiceResult with:
              - success=True: data includes {"session_token": "...", "expiry": timestamp}
              - success=False: error message (MUST NOT expose internal details)

        Security:
            - Credentials exist ONLY in this method's local scope
            - Return ONLY a temporary session token, NOT the credentials
        """
        ...

    @abstractmethod
    async def fetch(
        self,
        session_token: str | None,
        params: dict[str, Any],
    ) -> ServiceResult:
        """
        Fetch data from the service using an active session.

        Args:
            session_token: Opaque token from a successful authenticate() call
            params:        Query parameters (e.g. {"semester": "4", "exam": "regular"})

        Returns:
            ServiceResult with:
              - success=True: data includes service-specific fields
              - success=False: error message

        The data dict should contain keys that map to the frontend detail card:
          {
            "title": "...",
            "message": "...",
            "fields": [{"label": "...", "value": "..."}, ...],
            "actions": [{"id": "...", "label": "..."}, ...],
          }
        """
        ...

    def get_options(self) -> list[dict[str, str]]:
        """
        Return available sub-options for this service (e.g. semester selection).

        Returns a list of {"id": "...", "label": "..."} dicts,
        or empty list if the service has no sub-options.
        """
        return []

    @property
    def requires_auth(self) -> bool:
        """Whether this service requires student portal authentication."""
        return True
