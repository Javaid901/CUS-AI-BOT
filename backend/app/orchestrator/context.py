"""
backend/app/orchestrator/context.py

Hierarchical conversation context engine.

Provides:
  - ConversationContext: tracks domain, level, programme, topic, etc.
  - PROGRAMME_ALIASES: maps full names / abbreviations to programme IDs
  - CONTEXT_TOPICS: topics that make sense as follow-up queries
  - augment_query(): prepend programme context to short follow-ups
  - detect_programme_switch(): detect if message targets a different programme
  - is_short_followup(): heuristic for context-dependent questions

This module is stateless — context is stored in ConversationState.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Programme aliases — maps every known name/abbrev -> programme ID
# ---------------------------------------------------------------------------

# (id, full_name, *aliases)
_PROGRAMME_ALIAS_ENTRIES: list[tuple[str, str, ...]] = [
    ("ba", "BA", "Bachelor of Arts", "bachelor of arts", "B.A.", "B.A"),
    ("bsc", "B.Sc", "Bachelor of Science", "bachelor of science", "BSc", "B.Sc.", "B.SC"),
    ("bcom", "B.Com", "Bachelor of Commerce", "bachelor of commerce", "BCom", "B.COM"),
    ("bba", "BBA", "Bachelor of Business Administration", "bachelor of business administration"),
    ("bca", "BCA", "Bachelor of Computer Applications", "bachelor of computer applications"),
    ("btech", "B.Tech", "Bachelor of Technology", "bachelor of technology", "BTech"),
    ("bed", "B.Ed", "Bachelor of Education", "bachelor of education", "BEd"),
    ("ma", "MA", "Master of Arts", "master of arts", "M.A.", "M.A"),
    ("msc", "M.Sc", "Master of Science", "master of science", "MSc", "M.Sc."),
    ("mcom", "M.Com", "Master of Commerce", "master of commerce", "MCom"),
    ("mba", "MBA", "Master of Business Administration", "master of business administration"),
    ("mca", "MCA", "Master of Computer Applications", "master of computer applications"),
    ("med", "M.Ed", "Master of Education", "master of education", "MEd"),
    ("phd", "PhD", "Ph.D.", "doctor of philosophy"),
]

# Reverse lookup: any alias -> programme ID (lowercased)
PROGRAMME_ALIASES: dict[str, str] = {}
for entry in _PROGRAMME_ALIAS_ENTRIES:
    prog_id = entry[0]
    for alias in entry:
        PROGRAMME_ALIASES[alias.lower().strip()] = prog_id

# Also add bare numeric/alpha programme IDs
for entry in _PROGRAMME_ALIAS_ENTRIES:
    pid = entry[0]
    PROGRAMME_ALIASES.setdefault(pid, pid)

# Pre-compiled pattern to find programme mentions in free text.
# Matches whole-word programme names/aliases.
# Exported for use by extractor.py
PROGRAMME_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in sorted(PROGRAMME_ALIASES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
# Keep old name for internal backward compat
_PROGRAMME_PATTERN = PROGRAMME_PATTERN

# ---------------------------------------------------------------------------
# Context topics — things users ask about a programme
# ---------------------------------------------------------------------------

CONTEXT_TOPICS: dict[str, str] = {
    "fee": "fee",
    "fees": "fee",
    "fee structure": "fee",
    "eligibility": "eligibility",
    "eligible": "eligibility",
    "duration": "duration",
    "how many years": "duration",
    "admission mode": "admission_mode",
    "admission process": "admission_mode",
    "how to apply": "admission_mode",
    "documents": "documents",
    "documents required": "documents",
    "required documents": "documents",
    "specializations": "specializations",
    "subjects": "specializations",
    "syllabus": "syllabus",
    "dates": "dates",
    "important dates": "dates",
    "prospectus": "prospectus",
    "seats": "seats",
    "intake": "seats",
    "placement": "placement",
    "placements": "placement",
    "career": "career",
    "career options": "career",
}

_CONTEXT_TOPIC_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in sorted(CONTEXT_TOPICS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Context model
# ---------------------------------------------------------------------------


@dataclass
class ConversationContext:
    """Hierarchical context for the current conversation.

    Fields are progressively narrowed: domain -> level -> programme -> topic.
    Once a field is set, it persists until explicitly changed or cleared.

    College fields track the active college context for the College Intelligence
    Module. They follow the same persistence rules.

    Clarification fields track when the engine needs additional information.
    """

    domain: str | None = None
    level: str | None = None
    programme: str | None = None
    programme_id: str | None = None
    semester: str | None = None
    department: str | None = None
    topic: str | None = None
    last_document: str | None = None
    pending_clarification: str | None = None
    clarification_field: str | None = None

    # College Intelligence fields
    college: str | None = None        # college ID
    college_name: str | None = None   # display name
    college_programme: str | None = None  # programme ID within a college
    college_topic: str | None = None  # topic within college context

    # Query understanding metadata
    query_original: str | None = None    # original raw user message
    query_clean: str | None = None       # corrected/normalized version
    query_corrected: bool = False        # whether corrections were applied
    last_selected_entity: str | None = None  # 'college' | 'programme' | 'topic' — last thing user picked

    # Student service context
    active_service: str | None = None     # currently active service (e.g. "results", "admit_card")
    service_step: str | None = None       # current step: "auth_needed" | "fetching" | "complete"
    service_params: dict[str, str] = field(default_factory=dict)  # collected params (programme, semester, etc.)
    service_session: str | None = None    # opaque session token, NEVER a password


def needs_clarification(ctx: ConversationContext) -> str | None:
    """Return the field that needs clarification, or None.

    Checks if context has partial information that needs a follow-up
    before answering (e.g., level known but programme unknown).
    """
    if ctx.pending_clarification:
        return ctx.clarification_field
    return None


# ---------------------------------------------------------------------------
# Programme detection in free text
# ---------------------------------------------------------------------------


def detect_programme_switch(message: str) -> str | None:
    """Detect if the message mentions a different programme.

    Returns the programme ID if a known programme is mentioned,
    or None if no programme reference is found.
    """
    text = message.strip().lower()
    match = _PROGRAMME_PATTERN.search(text)
    if match:
        alias = match.group(0).lower()
        return PROGRAMME_ALIASES.get(alias)
    return None


# ---------------------------------------------------------------------------
# Follow-up detection
# ---------------------------------------------------------------------------

# Words that typically start open-ended questions rather than follow-ups.
# Exported for use by extractor.py
QUESTION_STARTERS = (
    "what", "why", "when", "where", "which", "who", "whom", "whose",
    "how", "can", "could", "would", "will", "do", "does", "did",
    "is", "are", "was", "were", "has", "have", "had",
    "tell", "show", "list", "explain", "describe", "define",
)
_QUESTION_STARTERS = QUESTION_STARTERS

# Domain-level broad keywords the user might type alone.
# Exported for use by extractor.py
DOMAIN_KEYWORDS = {
    "admissions", "admission", "courses", "course", "fee", "fees",
    "results", "result", "datesheet", "syllabus", "scholarship",
    "scholarships", "notices", "notice", "downloads", "download",
    "hostel", "examination", "exam", "departments", "department",
    "colleges", "college", "contact", "ug", "pg", "phd", "integrated",
    "dyd", "back",
}
_DOMAIN_KEYWORDS = DOMAIN_KEYWORDS


def is_short_followup(message: str) -> bool:
    """Heuristic: is this a short context-dependent query?

    Returns True if the message:
      - Is 1-4 words (short)
      - Does NOT start with a question word
      - Is NOT a known domain keyword / option
    """
    text = message.strip().lower()
    words = text.split()
    if len(words) > 4:
        return False
    if len(words) == 0:
        return False
    first = words[0]
    if first in _QUESTION_STARTERS:
        return False
    clean = text.rstrip("?.,!;:")
    if clean in _DOMAIN_KEYWORDS:
        return False
    # Single word that looks like a known programme alias -> not a follow-up
    return not (len(words) == 1 and clean in PROGRAMME_ALIASES)


# ---------------------------------------------------------------------------
# Query augmentation
# ---------------------------------------------------------------------------


def augment_query(message: str, ctx: ConversationContext) -> str:
    """Augment a short follow-up query with programme context.

    If the user has an active programme and the message looks like
    a follow-up topic (fee, eligibility, etc.), prepend programme info
    so RAG receives a more specific query.

    Example:
      ctx = {programme: "bca", level: "ug", domain: "admissions"}
      message = "fee"
      -> "BCA fee details for undergraduate admissions"

    Returns the original message unchanged if no context is active
    or the message does not match a known topic.
    """
    if not ctx.programme:
        return message

    topic = None
    match = _CONTEXT_TOPIC_PATTERN.search(message.lower())
    if match:
        topic = CONTEXT_TOPICS.get(match.group(0).lower())

    if not topic:
        return message

    # Map programme_id to human-readable name
    prog_label = _get_programme_label(ctx.programme) or ctx.programme.upper()
    level_label = ctx.level.upper() if ctx.level else ""
    domain_label = ctx.domain or ""

    parts = [f"{prog_label} {topic.replace('_', ' ')}"]
    if level_label:
        parts.append(level_label)
    if domain_label and domain_label not in str(parts).lower():
        parts.append(domain_label)
    parts.append(message)

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_known_actions(programme_id: str) -> list[dict[str, str]]:
    """Return action buttons for a programme (fee, dates, etc.)."""
    # Simple default action set; intent_router has richer data
    return [
        {"id": "fee", "label": "Fee Structure"},
        {"id": "eligibility", "label": "Eligibility"},
        {"id": "duration", "label": "Duration"},
        {"id": "dates", "label": "Important Dates"},
    ]


def _get_programme_label(programme_id: str) -> str | None:
    """Get a human-readable label for a programme ID."""
    for entry in _PROGRAMME_ALIAS_ENTRIES:
        if entry[0] == programme_id:
            return entry[1]
    return None


# ---------------------------------------------------------------------------
# Context update helpers
# ---------------------------------------------------------------------------


DETAIL_TOPIC_MAP: dict[str, str] = {
    "fee": "fee",
    "fee structure": "fee",
    "view fee structure": "fee",
    "eligibility": "eligibility",
    "duration": "duration",
    "dates": "dates",
    "important dates": "dates",
    "prospectus": "prospectus",
    "open prospectus": "prospectus",
}

# College-specific topic keywords — extends context topics
COLLEGE_TOPICS: dict[str, str] = {
    "about": "about",
    "overview": "about",
    "departments": "departments",
    "department": "departments",
    "courses": "courses",
    "programmes": "courses",
    "programme": "courses",
    "admissions": "admissions",
    "admission": "admissions",
    "fee": "fee",
    "fees": "fee",
    "fee structure": "fee",
    "eligibility": "eligibility",
    "eligible": "eligibility",
    "facilities": "facilities",
    "facility": "facilities",
    "contact": "contact",
    "phone": "contact",
    "email": "contact",
    "website": "contact",
    "address": "contact",
    "principal": "principal",
    "library": "library",
    "hostel": "hostel",
    "sports": "sports",
    "laboratories": "laboratories",
    "labs": "laboratories",
    "placement": "placement",
    "placements": "placement",
    "location": "contact",
    "notices": "notices",
    "notice": "notices",
    "prospectus": "prospectus",
    "anti ragging": "anti_ragging",
}

_LEVEL_KEYWORDS = {"ug", "pg", "phd", "integrated", "dyd"}


def update_context_from_nav(
    ctx: ConversationContext,
    response: dict[str, Any],
    selection: str | None = None,
) -> ConversationContext:
    """Update context fields based on a navigation response."""
    rtype = response.get("type", "")
    title = (response.get("title") or "").lower()
    cat = selection.lower() if selection else ""

    # Domain detection from top-level navigation
    if rtype == "options" and not ctx.domain:
        domain_map = {
            "admissions": "admissions",
            "fee": "fee",
            "fee structure": "fee",
            "courses": "courses",
            "results": "results",
            "datesheet": "datesheet",
            "scholarships": "scholarships",
            "notices": "notices",
            "downloads": "downloads",
            "hostel": "hostel",
            "examination": "examination",
            "departments": "departments",
            "colleges": "colleges",
            "contact": "contact",
        }
        for keyword, domain in domain_map.items():
            if keyword in title or keyword == cat:
                ctx.domain = domain
                break

    # Level detection
    if cat in _LEVEL_KEYWORDS:
        ctx.level = cat

    # Programme detection
    if cat in PROGRAMME_ALIASES:
        ctx.programme_id = cat
        ctx.programme = cat
        ctx.topic = None  # Reset topic on programme switch

    # Topic detection from action selection
    if cat in DETAIL_TOPIC_MAP:
        ctx.topic = DETAIL_TOPIC_MAP[cat]
    elif rtype == "detail":
        ctx.topic = None

    return ctx


# ---------------------------------------------------------------------------
# College context helpers
# ---------------------------------------------------------------------------

COLLEGE_DOMAIN_MAP: dict[str, str] = {
    "about": "about",
    "overview": "about",
    "departments": "departments",
    "courses": "courses",
    "programmes": "courses",
    "admissions": "admissions",
    "fee": "fee",
    "fee structure": "fee",
    "eligibility": "eligibility",
    "facilities": "facilities",
    "contact": "contact",
    "phone": "contact",
    "address": "contact",
    "location": "contact",
    "principal": "principal",
    "library": "library",
    "hostel": "hostel",
    "sports": "sports",
    "laboratories": "laboratories",
    "placement": "placement",
    "notices": "notices",
    "prospectus": "prospectus",
    "anti_ragging": "anti_ragging",
}


def update_context_for_college(
    ctx: ConversationContext,
    college_id: str,
    college_name: str,
    topic: str | None = None,
) -> None:
    """Update context when a college is selected or referenced."""
    ctx.college = college_id
    ctx.college_name = college_name
    if topic:
        resolved = COLLEGE_DOMAIN_MAP.get(topic.lower(), topic.lower())
        ctx.college_topic = resolved
    else:
        ctx.college_topic = None


def clear_college_context(ctx: ConversationContext) -> None:
    """Clear college-specific context (when user explicitly changes college)."""
    ctx.college = None
    ctx.college_name = None
    ctx.college_programme = None
    ctx.college_topic = None


# ---------------------------------------------------------------------------
# Service context helpers
# ---------------------------------------------------------------------------


def set_active_service(ctx: ConversationContext, service: str) -> None:
    """Set the active student service and reset step."""
    ctx.active_service = service
    ctx.service_step = "auth_needed" if _service_requires_auth(service) else "fetching"
    # Copy known programme from conversation context as a service param hint
    if ctx.programme and "programme" not in ctx.service_params:
        ctx.service_params["programme"] = ctx.programme
    if ctx.level and "level" not in ctx.service_params:
        ctx.service_params["level"] = ctx.level


def clear_service_context(ctx: ConversationContext) -> None:
    """Clear service-specific context (on completion or cancellation)."""
    ctx.active_service = None
    ctx.service_step = None
    ctx.service_params = {}
    ctx.service_session = None


def _service_requires_auth(service: str) -> bool:
    """Check whether a service requires portal authentication."""
    # All current student services require auth; update if services change
    non_auth_services = {"helpdesk"}
    return service not in non_auth_services
