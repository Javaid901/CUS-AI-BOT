"""
backend/app/authority/__init__.py

University Authority Management System.

Provides centralized administration of all university offices, departments,
contacts, and escalation routing for the chatbot.
"""

from app.authority.matcher import find_authority, format_contact_card
from app.authority.models import Authority
from app.authority.schemas import (
    AuthorityCard,
    AuthorityCreate,
    AuthorityMatchResult,
    AuthorityResponse,
    AuthorityUpdate,
)
from app.authority.service import AuthorityService, authority_service

__all__ = [
    "Authority",
    "AuthorityCard",
    "AuthorityCreate",
    "AuthorityMatchResult",
    "AuthorityResponse",
    "AuthorityService",
    "AuthorityUpdate",
    "authority_service",
    "find_authority",
    "format_contact_card",
]
