"""
backend/app/orchestrator/lookup.py

Centralized structured data access for university information.

Provides fast dictionary lookups for:
  - Programme details (fee, eligibility, duration, etc.)
  - College details
  - Broad category responses (admissions, fee, etc.)
  - Topic navigation options

This module wraps intent_router data to provide a single query interface
for the planner and engine, avoiding repeated imports and redundant lookups.
"""

from __future__ import annotations

from typing import Any

from app.chat.intent_router import (
    _COLLEGE_DETAILS,
    _PROGRAMME_DETAILS,
    _PROGRAMMES,
    _TOPICS,
    WELCOME_OPTIONS,
    get_broad_response,
)

# ---------------------------------------------------------------------------
# Pre-built index: programme_id -> field map for fast topic lookups
# ---------------------------------------------------------------------------

_PROGRAMME_FIELD_INDEX: dict[str, dict[str, str]] = {}


def _build_field_index() -> None:
    """Build a topic->value index for each programme."""
    for pid, detail in _PROGRAMME_DETAILS.items():
        index: dict[str, str] = {}
        for field in detail.get("fields", []):
            label = field.get("label", "").lower().strip()
            value = field.get("value", "")
            # Map common field labels to topic keys
            if "fee" in label:
                index["fee"] = value
            if "eligibility" in label:
                index["eligibility"] = value
            if "duration" in label:
                index["duration"] = value
            if "admission" in label or "mode" in label:
                index["admission_mode"] = value
            if "document" in label:
                index["documents"] = value
            if "special" in label or "stream" in label:
                index["specializations"] = value
            if "seat" in label or "intake" in label:
                index["seats"] = value
        _PROGRAMME_FIELD_INDEX[pid] = index


_build_field_index()


# ---------------------------------------------------------------------------
# Public lookup API
# ---------------------------------------------------------------------------


def lookup_programme(programme_id: str) -> dict[str, Any] | None:
    """Get full programme detail dict, or None if unknown."""
    return _PROGRAMME_DETAILS.get(programme_id)


def lookup_field(programme_id: str, topic: str) -> str | None:
    """Look up a specific field value for a programme.

    Example:
        lookup_field("bca", "fee") -> "Approx. Rs 10,500 per year"
    """
    index = _PROGRAMME_FIELD_INDEX.get(programme_id)
    if index:
        return index.get(topic)
    # Fallback: search the full details
    detail = _PROGRAMME_DETAILS.get(programme_id)
    if detail:
        for field in detail.get("fields", []):
            label = field.get("label", "").lower().strip()
            if topic in label:
                return field.get("value")
    return None


def lookup_college(college_id: str) -> dict[str, Any] | None:
    """Get college detail dict."""
    return _COLLEGE_DETAILS.get(college_id)


def lookup_topic(topic_id: str) -> dict[str, Any] | None:
    """Get topic navigation options dict."""
    return _TOPICS.get(topic_id)


def lookup_programme_list(level: str) -> list[dict[str, str]] | None:
    """Get the list of programmes for a given level (ug/pg/integrated)."""
    return _PROGRAMMES.get(level)


def get_programme_names() -> list[str]:
    """Get all known programme IDs."""
    return list(_PROGRAMME_DETAILS.keys())


def get_welcome_options() -> dict[str, Any]:
    """Get the welcome/start options."""
    return WELCOME_OPTIONS


def get_broad_response_safe(category: str) -> dict[str, Any] | None:
    """Get a broad category response, or None if not found."""
    response = get_broad_response(category)
    if response.get("type") == "rag":
        return None
    return response


def has_structured_answer(programme: str | None, topic: str | None) -> bool:
    """Check if we can answer with structured data alone."""
    if not programme or not topic:
        return False
    return lookup_field(programme, topic) is not None


def get_programme_actions(programme_id: str) -> list[dict[str, str]]:
    """Get action buttons for a programme from its detail."""
    detail = _PROGRAMME_DETAILS.get(programme_id)
    if detail:
        return detail.get("actions", [])
    return [
        {"id": "fee", "label": "Fee Structure"},
        {"id": "eligibility", "label": "Eligibility"},
        {"id": "duration", "label": "Duration"},
        {"id": "dates", "label": "Important Dates"},
    ]
