"""
backend/app/orchestrator/extractor.py

Rule-based entity extraction for university queries.

Extracts from raw user messages:
  - programme mentions (BCA, MBA, etc.)
  - level mentions (UG, PG, PhD, Integrated)
  - topic mentions (fee, eligibility, documents, etc.)
  - domain mentions (admissions, results, etc.)
  - service mentions (results, admit card, exam form, etc.)
  - clarification signals (start over, new conversation, etc.)

NO LLM calls — pure regex + dictionary lookup for speed.
"""

from __future__ import annotations

import re

from app.orchestrator.context import (
    CONTEXT_TOPICS,
    DOMAIN_KEYWORDS,
    PROGRAMME_ALIASES,
    PROGRAMME_PATTERN,
    QUESTION_STARTERS,
)

# ---------------------------------------------------------------------------
# Level keywords
# ---------------------------------------------------------------------------

_LEVEL_KEYWORDS: dict[str, str] = {
    "ug": "ug",
    "undergraduate": "ug",
    "under graduate": "ug",
    "pg": "pg",
    "postgraduate": "pg",
    "post graduate": "pg",
    "phd": "phd",
    "ph.d": "phd",
    "doctorate": "phd",
    "integrated": "integrated",
    "dyd": "dyd",
    "design your degree": "dyd",
}

_LEVEL_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_LEVEL_KEYWORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Service keywords (copied from engine.py to avoid circular deps)
# ---------------------------------------------------------------------------

_SERVICE_KEYWORDS: dict[str, str] = {
    "result": "results",
    "results": "results",
    "admit card": "admit_card",
    "admitcard": "admit_card",
    "hall ticket": "admit_card",
    "exam form": "exam_form",
    "examform": "exam_form",
    "examination form": "exam_form",
    "attendance": "attendance",
    "internal marks": "attendance",
    "fee receipt": "fee",
    "my fee receipt": "fee",
    "download fee receipt": "fee",
    "course registration": "registration",
    "course reg": "registration",
    "registration": "registration",
    "migration certificate": "migration",
    "migration": "migration",
    "transcript": "transcript",
    "degree status": "degree",
    "degree": "degree",
    "backlog": "backlog",
    "backlog status": "backlog",
    "student profile": "profile",
    "profile": "profile",
    "my profile": "profile",
    "re evaluation": "re_evaluation",
    "reevaluation": "re_evaluation",
    "re-evaluation": "re_evaluation",
    "xerox": "xerox_copy",
    "xerox copy": "xerox_copy",
    "photocopy": "xerox_copy",
    "semester admission": "semester_admission",
    "semester admission form": "semester_admission",
    "sem registration": "semester_admission",
    "helpdesk": "helpdesk",
    "help": "helpdesk",
    "support": "helpdesk",
    "semester form": "semester_admission",
    "admission form": "semester_admission",
}

SERVICE_KEYWORDS = _SERVICE_KEYWORDS
_SERVICE_PATTERNS = sorted(_SERVICE_KEYWORDS.keys(), key=len, reverse=True)
SERVICE_PATTERNS = _SERVICE_PATTERNS

# ---------------------------------------------------------------------------
# Reset / clarification signals
# ---------------------------------------------------------------------------

_RESET_SIGNALS = re.compile(
    r"\b(start over|new conversation|reset|clear|start fresh|restart)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Extracted entities dataclass
# ---------------------------------------------------------------------------


class ExtractedEntities:
    """Structured entities extracted from a user message."""

    __slots__ = (
        "clean_text",
        "domain",
        "is_back",
        "is_question",
        "is_reset",
        "level",
        "programme",
        "raw_text",
        "service",
        "topic",
        "word_count",
    )

    def __init__(self, message: str):
        self.raw_text = message
        self.clean_text = message.strip().lower().rstrip("?.,!;:")
        self.word_count = len(self.clean_text.split()) if self.clean_text else 0
        self.programme: str | None = None
        self.level: str | None = None
        self.topic: str | None = None
        self.domain: str | None = None
        self.service: str | None = None
        self.is_reset: bool = False
        self.is_back: bool = self.clean_text == "back"
        self.is_question: bool = self._is_question()


    def _is_question(self) -> bool:
        first = self.clean_text.split()[0] if self.clean_text.split() else ""
        return first in QUESTION_STARTERS


# ---------------------------------------------------------------------------
# Extraction logic
# ---------------------------------------------------------------------------


def extract_entities(message: str) -> ExtractedEntities:
    """Extract all entities from a user message.

    Runs all extractors in parallel (pure CPU, no I/O) and returns
    a populated ExtractedEntities instance.
    """
    entities = ExtractedEntities(message)
    text = entities.clean_text

    # Reset signal
    if _RESET_SIGNALS.search(text):
        entities.is_reset = True

    # Programme
    prog = _extract_programme(message)
    if prog:
        entities.programme = prog

    # Level
    level = _extract_level(text)
    if level:
        entities.level = level

    # Topic (must check before domain since topics can overlap with domains)
    topic = _extract_topic(text)
    if topic:
        entities.topic = topic

    # Domain
    domain = _extract_domain(text, entities)
    if domain:
        entities.domain = domain

    # Service
    service = _extract_service(text)
    if service:
        entities.service = service

    return entities


def _extract_programme(message: str) -> str | None:
    """Extract a programme ID from the message."""
    match = PROGRAMME_PATTERN.search(message.strip().lower())
    if match:
        return PROGRAMME_ALIASES.get(match.group(0).lower())
    return None


def _extract_level(text: str) -> str | None:
    match = _LEVEL_PATTERN.search(text)
    if match:
        return _LEVEL_KEYWORDS.get(match.group(0).lower())
    return None


def _extract_topic(text: str) -> str | None:
    for phrase in sorted(CONTEXT_TOPICS, key=len, reverse=True):
        if phrase in text:
            return CONTEXT_TOPICS[phrase]
    return None


def _extract_domain(text: str, entities: ExtractedEntities) -> str | None:
    """Domain is only set if no topic/programme/level is already active."""
    if entities.topic or entities.programme or entities.level:
        return None
    if text in DOMAIN_KEYWORDS:
        domain_map = {
            "admissions": "admissions", "admission": "admissions",
            "courses": "courses", "course": "courses",
            "fee": "fee", "fees": "fee",
            "results": "results", "result": "results",
            "datesheet": "datesheet",
            "syllabus": "syllabus",
            "scholarship": "scholarships", "scholarships": "scholarships",
            "notices": "notices", "notice": "notices",
            "downloads": "downloads", "download": "downloads",
            "hostel": "hostel",
            "examination": "examination", "exam": "examination",
            "departments": "departments", "department": "departments",
            "colleges": "colleges", "college": "colleges",
            "contact": "contact",
        }
        return domain_map.get(text)
    return None


def _extract_service(text: str) -> str | None:
    for phrase in _SERVICE_PATTERNS:
        if phrase in text:
            return _SERVICE_KEYWORDS[phrase]
    return None
