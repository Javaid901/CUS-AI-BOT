"""
backend/app/orchestrator/contract.py

Canonical query contract — the single internal representation of what the
user actually wants. Every downstream component (planner, retrieval, answer
generation) consumes a QueryContract instead of raw text.

The contract is built from:
  1. explicit entities in the current message (highest priority)
  2. strongly inferred entities (semantic intent, domain rules)
  3. previous conversational context
  4. generic defaults (lowest priority)

Rule: explicit current-query entities ALWAYS override stale context so
"Tell me about BBA. -> What is MCA eligibility?" resolves to MCA.

No LLM calls — pure rules for speed and determinism.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Intent taxonomy (university domain)
# ---------------------------------------------------------------------------

INTENTS: tuple[str, ...] = (
    "greeting",
    "programme_information",
    "programme_discovery",
    "curriculum",
    "subjects",
    "semester_subjects",
    "fee_information",
    "admission_information",
    "admission_requirements",
    "eligibility",
    "college_information",
    "college_programmes",
    "department_information",
    "scholarship",
    "notice",
    "notification",
    "date_sheet",
    "examination",
    "contact_information",
    "student_service",
    "result",
    "attendance",
    "grievance",
    "comparison",
    "clarification",
    "overview",
    "unknown",
)


@dataclass
class QueryContract:
    """Canonical understanding of a user request."""

    intent: str = "unknown"
    operation: str | None = None          # catalogue op or sub-action ("subjects", "fee", ...)
    programme: str | None = None          # primary programme id (lowercase code)
    programmes: list[str] = field(default_factory=list)   # comparison targets (2+)
    programme_id: str | None = None       # catalogue row UUID when resolved
    level: str | None = None              # ug | pg | phd | integrated
    scheme: str | None = None             # cbcs | nep | nep2020
    semester: int | None = None
    college: str | None = None            # college id
    department: str | None = None
    topic: str | None = None              # canonical topic key ("fee", "eligibility", ...)
    service: str | None = None            # student-service id (transactional)
    authority: str | None = None          # office / department name
    confidence: float = 0.0
    needs_clarification: bool = False
    clarification_field: str | None = None
    comparison: bool = False
    is_transactional: bool = False        # result/attendance/grievance style requests
    original: str = ""
    raw: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Serializable form (for state, logs, analytics)."""
        return {
            "intent": self.intent,
            "operation": self.operation,
            "programme": self.programme,
            "programmes": list(self.programmes),
            "programme_id": self.programme_id,
            "level": self.level,
            "scheme": self.scheme,
            "semester": self.semester,
            "college": self.college,
            "department": self.department,
            "topic": self.topic,
            "service": self.service,
            "authority": self.authority,
            "confidence": round(self.confidence, 3),
            "needs_clarification": self.needs_clarification,
            "clarification_field": self.clarification_field,
            "comparison": self.comparison,
            "is_transactional": self.is_transactional,
        }


# ---------------------------------------------------------------------------
# Intent inference helpers
# ---------------------------------------------------------------------------

# Catalogue op -> intent/operation mapping (kept in sync with catalogue ops).
_CATALOGUE_OP_TO_INTENT: dict[str, tuple[str, str]] = {
    "schemes": ("programme_discovery", "list"),
    "list": ("programme_discovery", "list"),
    "overview": ("programme_information", "overview"),
    "menu": ("programme_information", "overview"),
    "subjects": ("curriculum", "subjects"),
    "semesters": ("curriculum", "semesters"),
    "semester_subjects": ("semester_subjects", "subjects"),
    "minors": ("curriculum", "minors"),
    "minor_subjects": ("curriculum", "subjects"),
    "vac": ("curriculum", "vac"),
    "sec": ("curriculum", "sec"),
    "aec": ("curriculum", "aec"),
    "credits": ("curriculum", "credits"),
    "outcomes": ("curriculum", "outcomes"),
    "curriculum": ("curriculum", "curriculum"),
    "fee": ("fee_information", "fee"),
    "eligibility": ("eligibility", "eligibility"),
    "requested": ("programme_information", "fields"),
    "programme_pick": ("programme_information", "pick"),
    "scheme": ("programme_information", "scheme"),
}

_SERVICE_TO_INTENT: dict[str, str] = {
    "results": "result",
    "attendance": "attendance",
    "admit_card": "student_service",
    "exam_form": "examination",
    "fee": "student_service",
    "registration": "student_service",
    "re_evaluation": "student_service",
    "xerox_copy": "student_service",
    "semester_admission": "student_service",
    "migration": "student_service",
    "transcript": "student_service",
    "backlog": "student_service",
    "profile": "student_service",
    "degree": "student_service",
    "helpdesk": "contact_information",
}

_TOPIC_TO_INTENT: dict[str, str] = {
    "fee": "fee_information",
    "eligibility": "eligibility",
    "admission_mode": "admission_information",
    "documents": "admission_requirements",
    "duration": "programme_information",
    "dates": "date_sheet",
    "specializations": "curriculum",
    "syllabus": "curriculum",
    "results": "result",
    "attendance": "attendance",
    "prospectus": "admission_information",
    "seats": "admission_information",
    "placement": "programme_information",
    "career": "programme_information",
}

# ---------------------------------------------------------------------------
# Contract builder
# ---------------------------------------------------------------------------


def intent_from_catalogue_op(op: str | None) -> tuple[str, str | None]:
    """Map a catalogue op to (intent, operation)."""
    if not op:
        return ("programme_information", None)
    pair = _CATALOGUE_OP_TO_INTENT.get(op)
    if pair:
        return pair
    return ("programme_information", op)


def build_contract(
    message: str,
    entities: Any,          # ExtractedEntities
    ctx: Any | None = None,  # ConversationContext
    semantic_intent: str | None = None,
    semantic_confidence: float = 0.0,
    catalogue_req: dict[str, Any] | None = None,
    plan_action: str | None = None,
    plan_target: str | None = None,
) -> QueryContract:
    """Build the canonical query contract for a message.

    Priority: explicit current-message entities > inferred > context > default.
    """
    c = QueryContract(original=message.strip(), raw=message.strip())

    # ---- Explicit entities from the current message ----
    c.programme = getattr(entities, "programme", None)
    c.level = getattr(entities, "level", None)
    c.scheme = getattr(entities, "scheme", None)
    c.semester = getattr(entities, "semester", None)
    c.topic = getattr(entities, "topic", None)
    c.service = getattr(entities, "service", None)
    c.college = getattr(entities, "college", None)
    c.confidence = getattr(entities, "confidence", 0.5) or 0.5

    programmes = getattr(entities, "programmes", None) or []
    if programmes:
        c.programmes = list(programmes)
        c.comparison = len(programmes) > 1

    # ---- Context fill-in (only where the message itself is silent) ----
    if ctx is not None:
        if not c.programme:
            c.programme = getattr(ctx, "programme", None)
        if not c.programme_id:
            c.programme_id = getattr(ctx, "catalogue_programme_id", None) or getattr(ctx, "programme_id", None)
        if not c.level:
            c.level = getattr(ctx, "level", None)
        if not c.scheme:
            c.scheme = getattr(ctx, "academic_scheme", None) or getattr(ctx, "catalogue_scheme_code", None)
        if c.semester is None and getattr(ctx, "semester", None) is not None:
            try:
                c.semester = int(ctx.semester)
            except (TypeError, ValueError):
                c.semester = getattr(ctx, "catalogue_semester", None)
        if not c.college:
            c.college = getattr(ctx, "college", None)
        if not c.topic and getattr(ctx, "topic", None):
            c.topic = ctx.topic
        # Last contract carries the resolved catalogue programme row id
        if not c.programme_id:
            last = getattr(ctx, "_last_contract", None) or {}
            c.programme_id = last.get("programme_id") if isinstance(last, dict) else None

    # ---- Semantic intent (silent fallback, never overrides explicit) ----
    if semantic_intent and semantic_confidence >= 0.55:
        if semantic_intent in ("fee", "fee_information") and not c.topic:
            c.topic = "fee"
        if semantic_intent in ("eligibility", "admission_requirements") and not c.topic:
            c.topic = "eligibility"
        if semantic_intent in ("subjects", "curriculum") and not c.topic:
            c.topic = "subjects"

    # ---- Catalogue request enrichments (resolved structured route) ----
    if catalogue_req:
        op = catalogue_req.get("op")
        c.operation = op
        intent, _op = intent_from_catalogue_op(op)
        c.intent = intent
        if catalogue_req.get("programme"):
            c.programme = catalogue_req["programme"] or c.programme
            c.programme_id = catalogue_req["programme"]
        if catalogue_req.get("code"):
            c.programme = str(catalogue_req["code"]).lower() or c.programme
        if catalogue_req.get("semester") is not None:
            c.semester = int(catalogue_req["semester"])
        if catalogue_req.get("scheme"):
            c.scheme = catalogue_req["scheme"] or c.scheme
        if c.topic is None:
            c.topic = op if op not in ("schemes", "list", "overview", "menu", "programme_pick") else None
        c.confidence = max(c.confidence, 0.9)

    # ---- Service (transactional) ----
    if c.service:
        c.is_transactional = True
        c.intent = _SERVICE_TO_INTENT.get(c.service, "student_service")

    # ---- Grievance ----
    if plan_action == "grievance":
        c.intent = "grievance"
        c.is_transactional = True
        c.needs_clarification = False

    # ---- Comparison ----
    if c.comparison:
        c.intent = "comparison"

    # ---- Topic-driven intent (only when nothing stronger exists) ----
    if c.intent in ("unknown", "programme_information") and not c.is_transactional:
        if c.topic:
            c.intent = _TOPIC_TO_INTENT.get(c.topic, "programme_information")
            if c.topic == "eligibility":
                c.intent = "eligibility"
            if c.topic in ("admission_mode", "admission_process"):
                c.intent = "admission_information"
        elif c.programme and plan_action in ("catalogue", "structured", "navigation"):
            c.intent = "programme_information"
        elif c.scheme and not c.programme:
            c.intent = "programme_discovery"

    # ---- Clarification ----
    if plan_action == "slot_fill":
        c.needs_clarification = True
        c.clarification_field = plan_target or "programme"
        c.intent = "clarification"

    if not c.programme and not c.topic and not c.service and not c.comparison:
        c.confidence = min(c.confidence, 0.4)

    return c
