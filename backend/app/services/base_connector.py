"""
backend/app/services/base_connector.py

Common base class for all university service connectors.

Ensures consistent interface across demo and university connectors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.services.base import ServiceResult, ServiceConnector


@dataclass
class BaseServiceConnector(ServiceConnector):
    """Concrete base class providing default implementations of optional methods."""

    def get_options(self) -> list[dict[str, str]]:
        """Return available sub-options for this service.

        Returns a list of {"id": "...", "label": "..."} dicts,
        or empty list if the service has no sub-options.
        """
        return []

    @property
    def requires_auth(self) -> bool:
        """Whether this service requires student portal authentication."""
        return True

    def validate_credentials(self, reg_no: str, password: str) -> str | None:
        """Validate credentials format before authentication.

        Override in subclasses for format-specific validation.
        Returns error message string if invalid, None if valid.
        """
        if not reg_no or not password:
            return "Registration number and password are required."
        return None