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
    detect_academic_scheme,
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
    "sgpa": "results",
    "cgpa": "results",
    "grades": "results",
    "marks": "results",
    "marksheet": "results",
    "score": "results",
    "scored": "results",
    "admit card": "admit_card",
    "admitcard": "admit_card",
    "hall ticket": "admit_card",
    "exam form": "exam_form",
    "examform": "exam_form",
    "examination form": "exam_form",
    "attendance": "attendance",
    "presence": "attendance",
    "attendance percentage": "attendance",
    "internal marks": "attendance",
    "fee receipt": "fee",
    "my fee receipt": "fee",
    "download fee receipt": "fee",
    "course registration": "registration",
    "course reg": "registration",
    "registration": "registration",
    "register": "registration",
    "enroll": "registration",
    "enrol": "registration",
    "sign up": "registration",
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
    "help": "helpdesk",
    "support": "helpdesk",
    "semester form": "semester_admission",
    "admission form": "semester_admission",
}

SERVICE_KEYWORDS = _SERVICE_KEYWORDS
_SERVICE_PATTERNS = sorted(_SERVICE_KEYWORDS.keys(), key=len, reverse=True)
SERVICE_PATTERNS = _SERVICE_PATTERNS

# ---------------------------------------------------------------------------
# Informational phrasing detection (shared by engine + planner)
# ---------------------------------------------------------------------------

# Question phrasing that asks about a service AS A TOPIC ("what is course
# registration?") instead of requesting private data or an action.
_QUESTION_FRAMES = (
    "what is", "what are", "what's", "whats", "when", "where", "how does",
    "how do", "how to", "how can", "what about", "tell me", "explain",
    "is there", "are there",
)

# Action/possession words that make a service mention an actual request
# ("show my attendance", "i need my exam form", "how to check my result").
_ACTION_VERBS = (
    "apply", "fill", "submit", "register", "enroll", "enrol", "file",
    "download", "view", "print", "get", "show", "check", "need", "want",
    "request", "obtain", "see", "open", "fetch",
)


def is_informational_question(text: str) -> bool:
    """True when a service-noun message is phrased as a general knowledge
    question rather than a private-data or action request.

    "What is course registration?"  -> informational (topic question)
    "What is my CGPA?"              -> private (possessive) -> service
    "Show my attendance"            -> action verb -> service
    "When will results be announced?" -> informational (announcement news)
    """
    t = " ".join((text or "").strip().lower().split())
    if not t:
        return False
    if re.search(r"\bmy\b", t):
        return False
    if any(v in t for v in _ACTION_VERBS):
        return False
    return any(t.startswith(f) for f in _QUESTION_FRAMES)

# ---------------------------------------------------------------------------
# Semester detection
# ---------------------------------------------------------------------------

# Word-number maps for ordinal semester references ("fourth semester").
_SEM_WORD_NUM: dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8,
}

_SEM_CURRENT_WORDS = ("current sem", "this sem", "current semester", "this semester", "current", "this")
_SEM_NEXT_WORDS = ("next sem", "next semester", "next")
_SEM_PREV_WORDS = ("previous sem", "previous semester", "prev sem", "prev semester", "previous", "prev")

# "sem 4" / "sem4" / "semester 4" / "4th semester" / "fourth semester"
_SEMESTER_DIGIT_PATTERN = re.compile(
    r"\b(?:sem(?:ester)?[ -]?(\d{1,2})|(\d{1,2})(?:st|nd|rd|th)\s+sem)",
    re.IGNORECASE,
)


def _extract_semester(text: str) -> tuple[int | None, str | None]:
    """Extract a numeric semester reference and/or a relative word.

    Returns (semester, word) where:
      - semester: explicit number (e.g. "4th semester" -> 4)
      - word: "current" | "next" | "previous" for relative references
    """
    lowered = text.lower()
    # Bare relative words ("next", "previous", "current") are only treated as a
    # semester reference when they are the ENTIRE message — inside prose they
    # are far too ambiguous ("next subject", "what's next", ...).
    bare = lowered.strip().strip("?.,!;:")
    if bare in ("next", "previous", "current", "this"):
        return None, "current" if bare == "this" else bare
    # Relative references
    for w in _SEM_CURRENT_WORDS:
        if w in ("next", "previous", "current", "prev", "this"):
            continue
        if lowered.startswith(w) or f" {w} " in f" {lowered} ":
            return None, "current"
    for w in _SEM_NEXT_WORDS:
        if w == "next":
            continue  # "next" alone is too ambiguous inside prose
        if lowered.startswith(w) or f" {w} " in f" {lowered} ":
            return None, "next"
    for w in _SEM_PREV_WORDS:
        if w == "previous":
            continue
        if lowered.startswith(w) or f" {w} " in f" {lowered} ":
            return None, "previous"

    # Explicit numeric
    for phrase, num in sorted(_SEM_WORD_NUM.items(), key=lambda kv: len(kv[0]), reverse=True):
        if f"{phrase} sem" in lowered or f"{phrase} semester" in lowered:
            return num, None

    m = _SEMESTER_DIGIT_PATTERN.search(lowered)
    if m:
        for group in m.groups():
            if group and group.isdigit() and int(group) in range(1, 13):
                return int(group), None
    return None, None


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
        "confidence",
        "domain",
        "is_back",
        "is_question",
        "is_reset",
        "level",
        "programme",
        "programmes",
        "raw_text",
        "scheme",
        "semester",
        "semester_word",
        "service",
        "topic",
        "word_count",
    )

    def __init__(self, message: str):
        self.raw_text = message
        self.clean_text = message.strip().lower().rstrip("?.,!;:")
        self.word_count = len(self.clean_text.split()) if self.clean_text else 0
        self.programme: str | None = None
        self.programmes: list[str] = []
        self.level: str | None = None
        self.topic: str | None = None
        self.domain: str | None = None
        self.service: str | None = None
        self.scheme: str | None = None
        self.semester: int | None = None
        self.semester_word: str | None = None
        self.confidence: float = 0.0
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

    # Programme(s) — all mentions, so comparisons keep both targets
    programmes = _extract_programmes(message)
    if programmes:
        entities.programmes = programmes
        entities.programme = programmes[0]

    # Level
    level = _extract_level(text)
    if level:
        entities.level = level

    # Academic scheme (NEP / CBCS)
    scheme = detect_academic_scheme(text)
    if scheme:
        entities.scheme = scheme

    # Semester
    sem, sem_word = _extract_semester(text)
    if sem is not None:
        entities.semester = sem
    if sem_word is not None:
        entities.semester_word = sem_word

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

    entities.confidence = _compute_confidence(entities)
    return entities


def _extract_programmes(message: str) -> list[str]:
    """Extract ALL distinct programme IDs mentioned in a message.

    Order follows first appearance so the first mention is the primary
    programme ("BBA vs BCA" -> [bba, bca]).
    """
    text = (message or "").strip().lower()
    if not text:
        return []
    seen: list[str] = []
    for match in PROGRAMME_PATTERN.finditer(text):
        pid = PROGRAMME_ALIASES.get(match.group(0).lower())
        if pid and pid not in seen:
            seen.append(pid)
    return seen


def _compute_confidence(e: ExtractedEntities) -> float:
    """Heuristic confidence in the extraction.

    Strong signals: programme mention, topic, service, explicit level,
    catalogue-relevant scheme/semester.
    """
    score = 0.2
    if e.programme:
        score += 0.35
    if e.topic:
        score += 0.25
    if e.service:
        score += 0.3
    if e.level:
        score += 0.1
    if e.scheme:
        score += 0.1
    if e.semester is not None:
        score += 0.1
    if e.is_question:
        score += 0.05
    return min(score, 0.95)


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
