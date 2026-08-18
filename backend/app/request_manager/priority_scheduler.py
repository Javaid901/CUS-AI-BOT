"""
backend/app/request_manager/priority_scheduler.py

Classify incoming chat messages by request type and assign priority + cost.

Used by the Admission Controller to decide whether to execute immediately,
queue, or reject.
"""

from __future__ import annotations

import re
from typing import Any

from app.request_manager.models import Classification, Priority, RequestCost

# Patterns that indicate structured (Priority 1) requests
_STRUCTURED_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(?:fee|fees|fee\s*structure|fee\s*details)\b",
        r"\b(?:eligibility|eligibility\s*criteria|am\s*i\s*eligible)\b",
        r"\b(?:duration|course\s*duration|programme\s*duration|how\s*long)\b",
        r"\b(?:admission|admissions|admission\s*process|how\s*to\s*apply)\b",
        r"\b(?:department|departments|department\s*details)\b",
        r"\b(?:college\s+info|college\s+details|about\s+college)\b",
        r"\b(?:course\s+info|programme\s+info|programme\s+details)\b",
        r"\b(?:academic\s*calendar|important\s*dates|exam\s*schedule)\b",
        r"\b(?:seats|intake|seat\s*capacity)\b",
        r"\b(?:specializations?|specialisation|branches?)\b",
        r"\b(?:syllabus|curriculum|subjects)\b",
        r"\b(?:documents?\s*required|required\s*documents?|documents?\s*needed)\b",
        r"\b(?:placement|placement\s*details|placement\s*record)\b",
        r"\b(?:career|career\s*options|career\s*opportunities)\b",
        r"\b(?:scholarship|scholarships|financial\s*aid)\b",
    ]
]

# Patterns that indicate navigation (Priority 2)
_NAVIGATION_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(?:menu|options|what\s+can\s+you\s+do|help|show\s+menu)\b",
        r"\b(?:results?|exam\s*results?|marks?)\b",
        r"\b(?:course\s*selection|choose\s*course|select\s*programme)\b",
        r"\b(?:datesheet|date\s*sheet|time\s*table|timetable)\b",
        r"\b(?:notification|notifications|notices?|announcement)\b",
        r"\b(?:back|go\s+back|previous|return)\b",
    ]
]

# Patterns that indicate student service (Priority 3) - authenticated
_STUDENT_SERVICE_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(?:profile|my\s*profile|student\s*profile)\b",
        r"\b(?:attendance|my\s*attendance|attendance\s*record)\b",
        r"\b(?:fee\s*receipt|payment\s*receipt|fee\s*paid)\b",
        r"\b(?:admit\s*card|hall\s*ticket|exam\s*admit)\b",
        r"\b(?:exam\s*form|examination\s*form|form\s*fill)\b",
        r"\b(?:registration|enrollment|admission\s*status)\b",
        r"\b(?:transcript|academic\s*transcript|marksheet)\b",
        r"\b(?:migration|migration\s*certificate|transfer)\b",
    ]
]

# Patterns that indicate RAG / KB search (Priority 4)
_RAG_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(?:tell\s+me\s+about|what\s+is|explain|describe|define)\b",
        r"\b(?:search|find|look\s+up|retrieve)\b",
        r"\b(?:document|documentation|knowledge|information\s+about)\b",
    ]
]


def _has_pattern(text: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


def _is_simple_programme_query(text: str) -> bool:
    """Detect bare programme names without additional context."""
    known = {"ba", "bsc", "bcom", "bba", "bca", "btech", "bed",
             "ma", "msc", "mcom", "mba", "mca", "med", "phd",
             "ba.", "b.sc", "b.com", "b.tech", "m.sc", "m.com"}
    words = text.strip().lower().rstrip("?.,!;:").split()
    if len(words) == 1 and words[0].rstrip(".") in {x.rstrip(".") for x in known}:
        return True
    return False


def _is_greeting_or_reset(text: str) -> bool:
    """Detect greetings, welcome, reset requests."""
    clean = text.strip().lower().rstrip("?.,!;:")
    return clean in {"hi", "hello", "hey", "start", "reset", "welcome", "restart", "clear"}


def classify_request(
    message: str,
    planner_action: str | None = None,
    entities: Any = None,
) -> Classification:
    """Classify a chat message and return priority, cost, and cache hint.

    Args:
        message: The raw user message.
        planner_action: The planner's action decision (if already computed).
        entities: Extracted entities (if already computed).

    Returns:
        A Classification dataclass.
    """
    text = message.strip().lower()

    # Planner action override (most accurate when available)
    if planner_action:
        return _from_planner_action(planner_action)

    # Greeting / reset → priority 1 (fast path, negligible cost)
    if _is_greeting_or_reset(text):
        return Classification(
            priority=Priority.STRUCTURED,
            cost=RequestCost.STRUCTURED,
            action="welcome",
            cacheable=False,
        )

    # Bare programme name → structured
    if _is_simple_programme_query(text):
        return Classification(
            priority=Priority.STRUCTURED,
            cost=RequestCost.STRUCTURED,
            action="structured",
            cacheable=True,
            cache_ttl=600,
        )

    # Structured lookups
    if _has_pattern(text, _STRUCTURED_PATTERNS):
        return Classification(
            priority=Priority.STRUCTURED,
            cost=RequestCost.STRUCTURED,
            action="structured",
            cacheable=True,
            cache_ttl=300,
        )

    # Navigation
    if _has_pattern(text, _NAVIGATION_PATTERNS):
        return Classification(
            priority=Priority.NAVIGATION,
            cost=RequestCost.NAVIGATION,
            action="navigation",
            cacheable=False,
        )

    # Student services
    if _has_pattern(text, _STUDENT_SERVICE_PATTERNS):
        return Classification(
            priority=Priority.STUDENT_SERVICE,
            cost=RequestCost.STUDENT_SERVICE,
            action="connector",
            cacheable=False,
        )

    # RAG / KB
    if _has_pattern(text, _RAG_PATTERNS):
        return Classification(
            priority=Priority.RAG,
            cost=RequestCost.RAG,
            action="rag",
            cacheable=False,
        )

    # Default: LLM generation (lowest priority, highest cost)
    return Classification(
        priority=Priority.LLM,
        cost=RequestCost.LLM,
        action="llm",
        cacheable=False,
    )


def _from_planner_action(action: str) -> Classification:
    """Map a planner action to a priority/cost classification."""
    mapping = {
        "welcome": (Priority.STRUCTURED, RequestCost.STRUCTURED, True, 300),
        "structured": (Priority.STRUCTURED, RequestCost.STRUCTURED, True, 300),
        "catalogue": (Priority.STRUCTURED, RequestCost.STRUCTURED, True, 300),
        "navigation": (Priority.NAVIGATION, RequestCost.NAVIGATION, False, 0),
        "connector": (Priority.STUDENT_SERVICE, RequestCost.STUDENT_SERVICE, False, 0),
        "rag": (Priority.RAG, RequestCost.RAG, False, 0),
        "clarify": (Priority.STRUCTURED, RequestCost.STRUCTURED, False, 0),
        "llm": (Priority.LLM, RequestCost.LLM, False, 0),
    }
    p, c, cacheable, ttl = mapping.get(action, (Priority.LLM, RequestCost.LLM, False, 0))
    return Classification(
        priority=p,
        cost=c,
        action=action,
        cacheable=cacheable,
        cache_ttl=ttl,
    )
