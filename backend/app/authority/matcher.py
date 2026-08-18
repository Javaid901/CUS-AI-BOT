"""
backend/app/authority/matcher.py

Intelligent authority matcher — matches user messages to university offices
using keyword overlap scoring.

No LLM calls. Pure dict lookups against the in-memory cache.
Typically < 1ms.
"""

from __future__ import annotations

import re
from typing import Any

from app.authority.service import authority_service

# Phrases that signal an escalation / human assistance request
ESCALATION_PATTERNS: list[tuple[str, float]] = [
    (r"\bspeak\s+to\s+(a\s+)?(human|person|someone|representative|officer|authority)\b", 0.9),
    (r"\btalk\s+to\s+(a\s+)?(human|person|someone|representative|officer)\b", 0.9),
    (r"\bcontact\s+(a\s+)?(human|person|someone|office|department)\b", 0.8),
    (r"\bhuman\s+assistance\b", 0.9),
    (r"\b(real\s+)?(person|human)\s+(help|support|assist)\b", 0.9),
    (r"\b(connect|transfer|redirect)\s+me\s+to\b", 0.85),
    (r"\b(complain|complaint|grievance)\b", 0.7),
    (r"\b(escalate|escalation)\b", 0.85),
    (r"\bI\s+want\s+to\s+(meet|see|talk)\b", 0.7),
    (r"\b(where\s+is|who\s+is)\s+(the\s+)?(\w+\s+)?office\b", 0.7),
    (r"\b(helpline|help\s+line|support\s+line)\b", 0.8),
    (r"\b(customer\s+care|customer\s+support)\b", 0.8),
    (r"\b(concerned|relevant|appropriate)\s+(authority|office|department|cell|wing)\b", 0.8),
    (r"\b(contact|reach)\s+(the\s+)?(concerned|relevant|appropriate|right)\s+(authority|office|department|person)\b", 0.85),
    (r"\bI\s+don'?t\s+understand\b", 0.4),
    (r"\bcan'?t\s+find\b", 0.4),
    (r"\bnot\s+helpful\b", 0.5),
    (r"\bthis\s+is\s+not\s+(what|the)\b", 0.4),
]

# Known department/service names mapped to their canonical department_name
DEPARTMENT_ALIASES: dict[str, str] = {
    "admission": "Admissions",
    "admissions": "Admissions",
    "exam": "Controller of Examinations",
    "exams": "Controller of Examinations",
    "examination": "Controller of Examinations",
    "examinations": "Controller of Examinations",
    "controller of examination": "Controller of Examinations",
    "controller of examinations": "Controller of Examinations",
    "coe": "Controller of Examinations",
    "datesheet": "Controller of Examinations",
    "results": "Controller of Examinations",
    "academic": "Academic Section",
    "academics": "Academic Section",
    "registrar": "Registrar Office",
    "vice chancellor": "Vice Chancellor Office",
    "vc": "Vice Chancellor Office",
    "finance": "Finance",
    "accounts": "Accounts",
    "scholarship": "Scholarship Cell",
    "research": "Research Directorate",
    "it cell": "IT Cell",
    "it": "IT Cell",
    "help desk": "Student Help Desk",
    "helpdesk": "Student Help Desk",
    "library": "Library",
    "affiliated college": "Affiliated Colleges",
    "affiliated colleges": "Affiliated Colleges",
    "anti ragging": "Anti-Ragging Cell",
    "anti-ragging": "Anti-Ragging Cell",
    "grievance": "Grievance Cell",
    "placement": "Placement Cell",
    "training": "Training & Career Cell",
    "international": "International Cell",
    "hostel": "Hostel Office",
    "student welfare": "Student Welfare",
    "student service": "Student Welfare",
    "migration": "Academic Section",
    "transcript": "Controller of Examinations",
    "degree": "Controller of Examinations",
    "certificate": "Academic Section",
}

# Service categories → department routing hints
SERVICE_ROUTES: dict[str, str] = {
    "ug admission": "Admissions",
    "pg admission": "Admissions",
    "b.tech admission": "Admissions",
    "btech admission": "Admissions",
    "phd admission": "Admissions",
    "fee issue": "Finance",
    "fee": "Finance",
    "result": "Controller of Examinations",
    "re-evaluation": "Controller of Examinations",
    "reevaluation": "Controller of Examinations",
    "migration certificate": "Academic Section",
    "migration cert": "Academic Section",
    "transcript": "Controller of Examinations",
    "degree certificate": "Controller of Examinations",
    "degree cert": "Controller of Examinations",
    "scholarship": "Scholarship Cell",
    "attendance": "Academic Section",
    "student login": "IT Cell",
    "portal login": "IT Cell",
    "login problem": "IT Cell",
    "technical problem": "IT Cell",
    "hostel": "Hostel Office",
    "hostel issue": "Hostel Office",
    "library": "Library",
    "library card": "Library",
    "certificate": "Academic Section",
    "eligibility": "Admissions",
    "affiliated college": "Affiliated Colleges",
    "general enquiry": "Student Help Desk",
    "general inquiry": "Student Help Desk",
}


def _tokenize(text: str) -> set[str]:
    """Lowercase, split on non-alpha, return set of significant tokens."""
    text = text.lower()
    tokens = re.findall(r"[a-z]+", text)
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
        "into", "through", "during", "before", "after", "above", "below",
        "between", "out", "off", "over", "under", "again", "further",
        "then", "once", "here", "there", "when", "where", "why", "how",
        "all", "each", "every", "both", "few", "more", "most", "other",
        "some", "such", "no", "nor", "not", "only", "own", "same", "so",
        "than", "too", "very", "just", "because", "but", "and", "or",
        "if", "while", "that", "this", "these", "those", "it", "its",
        "i", "me", "my", "we", "our", "you", "your", "he", "him", "his",
        "she", "her", "they", "them", "their", "what", "which", "who",
        "whom", "about", "up", "down", "please", "tell", "know", "want",
        "like", "get", "give", "take", "let", "say", "see",
        "also", "well", "back", "any", "one", "two", "new", "now",
        "kindly",
    }
    return {t for t in tokens if len(t) > 1 and t not in stopwords}


def detect_escalation_intent(message: str) -> float:
    """Check if the message is requesting human assistance. Returns confidence [0,1]."""
    for pattern, score in ESCALATION_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return score
    return 0.0


def _department_from_aliases(message: str) -> str | None:
    """Try to route to a known department via alias/service maps."""
    lower = message.lower()
    matches: list[tuple[str, float]] = []
    for alias, dept in DEPARTMENT_ALIASES.items():
        if alias in lower:
            matches.append((dept, len(alias)))
    if not matches:
        for svc, dept in SERVICE_ROUTES.items():
            if svc in lower:
                matches.append((dept, len(svc)))
    if not matches:
        return None
    matches.sort(key=lambda x: -x[1])
    return matches[0][0]


def _score_authority(msg_tokens: set[str], authority: dict[str, Any]) -> float:
    """Compute overlap score between message tokens and authority keywords/services."""
    score = 0.0
    name_tokens = _tokenize(authority.get("authority_name", ""))
    dept_tokens = _tokenize(authority.get("department_name", ""))
    desc_tokens = _tokenize(authority.get("description", "") or "")

    kw_pool: set[str] = set()
    for kw in authority.get("keywords", []):
        kw_pool.update(_tokenize(kw))
    for svc in authority.get("services_offered", []):
        kw_pool.update(_tokenize(svc))

    overlap_name = msg_tokens & name_tokens
    overlap_dept = msg_tokens & dept_tokens
    overlap_kw = msg_tokens & kw_pool
    overlap_desc = msg_tokens & desc_tokens

    if name_tokens:
        score += len(overlap_name) / len(name_tokens) * 3.0
    if dept_tokens:
        score += len(overlap_dept) / len(dept_tokens) * 2.0
    if kw_pool:
        score += len(overlap_kw) / max(len(kw_pool), 1) * 2.0
    if desc_tokens:
        score += len(overlap_desc) / max(len(desc_tokens), 1) * 1.0

    return score


def find_authority(message: str, top_k: int = 3) -> list[dict[str, Any]]:
    """Find the best-matching authorities for a user message.

    Returns up to *top_k* authorities sorted by match score descending.
    Empty list if no match exceeds the minimum threshold.
    """
    msg_tokens = _tokenize(message)
    if not msg_tokens:
        msg_tokens = set(message.lower().split())

    authorities = authority_service.list_active()
    if not authorities:
        return []

    # Check escalation intent first
    escalation_conf = detect_escalation_intent(message)
    if escalation_conf >= 0.8:
        # Try department alias routing
        dept_hint = _department_from_aliases(message)
        if dept_hint:
            filtered = [a for a in authorities if a["department_name"].lower() == dept_hint.lower()]
            if filtered:
                authorities = filtered

    scored: list[tuple[float, dict[str, Any]]] = []
    for auth in authorities:
        score = _score_authority(msg_tokens, auth)
        priority_boost = max(0, 20 - (auth.get("priority", 10) or 10)) / 20.0
        total = score + priority_boost
        if total > 0.5:
            scored.append((total, auth))

    scored.sort(key=lambda x: -x[0])
    results = []
    for s, auth in scored[:top_k]:
        entry = dict(auth)
        entry["_match_score"] = round(s, 2)
        results.append(entry)

    return results


def format_contact_card(authority: dict[str, Any]) -> dict[str, Any]:
    """Format an authority record into a chatbot-friendly contact card."""
    return {
        "type": "authority_contact",
        "id": authority.get("id", ""),
        "department_name": authority.get("department_name", ""),
        "authority_name": authority.get("authority_name", ""),
        "designation": authority.get("designation"),
        "email": authority.get("email", ""),
        "phone": authority.get("phone", ""),
        "alternate_phone": authority.get("alternate_phone"),
        "office_address": authority.get("office_address"),
        "office_location": authority.get("office_location"),
        "office_timings": authority.get("office_timings"),
        "working_days": authority.get("working_days"),
        "emergency_contact": authority.get("emergency_contact"),
        "services_offered": authority.get("services_offered", []),
        "description": authority.get("description"),
        "match_score": authority.get("_match_score", 0.0),
    }
