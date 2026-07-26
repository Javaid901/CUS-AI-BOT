"""
backend/app/orchestrator/planner.py

Decision planner — decides the optimal execution path for each user message.

Planner output (Plan):
  action:   "structured" | "navigation" | "connector" | "rag" | "llm" | "clarify" | "welcome"
  target:   the object of the action (programme ID, topic, service name, etc.)
  response: pre-built structured response (if action is "structured" or "navigation")
  confidence: 0.0–1.0
  reason:   human-readable explanation

Decision rules (applied in order):
  1. Reset signal → welcome
  2. Service keyword → connector
  3. Auth flow in progress → continue auth
  4. Known option selection → navigation
  5. Broad keyword → navigation
  6. Programme + topic + structured data exists → structured
  7. Programme + topic + no structured data → rag
  8. Programme + follow-up question → rag
  9. Programme switch detected → navigation (show new programme)
  10. Level + topic → structured if possible else rag
  11. Broad question (what/why/how) with context → rag
  12. Broad question without context → navigation (welcome)
  13. Short ambiguous → clarify
  14. Everything else → rag

The planner is entirely rule-based (no LLM calls) for speed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.chat.intent_router import (
    classify as classify_nav,
)
from app.chat.intent_router import (
    get_broad_response,
    get_nav_path,
    get_selection_response,
    is_option_selection,
)
from app.college import course_map as college_course_map
from app.college.aliases import is_college_reference
from app.college.aliases import resolve as resolve_college
from app.college.service import CollegeService
from app.orchestrator.context import (
    COLLEGE_TOPICS,
    DOMAIN_KEYWORDS,
    PROGRAMME_ALIASES,
    ConversationContext,
    detect_programme_switch,
)
from app.orchestrator.lookup import (
    lookup_field,
    lookup_programme,
)
from app.orchestrator.query_understanding import (
    process_query as process_query_understanding,
)

_college_svc = CollegeService()


@dataclass
class Plan:
    action: str  # structured | navigation | connector | rag | llm | clarify | welcome
    target: str | None = None
    response: dict[str, Any] | None = None
    confidence: float = 0.0
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def plan(
    message: str,
    ctx: ConversationContext,
    chat_id: str,
    entities: Any,  # ExtractedEntities
) -> Plan:
    """Decide the optimal execution path for a user message.

    Args:
        message: raw user message
        ctx: current conversation context
        chat_id: session ID for nav path lookup
        entities: pre-extracted entities

    Returns:
        Plan dataclass with action and optional pre-built response.
    """
    text = message.strip().lower()
    clean = text.rstrip("?.,!;:")
    e = entities

    # ---- Stage 0: Query Understanding preprocessing ----
    # Lightweight normalization, spelling correction, alias expansion.
    # Only run on non-trivial messages (not single option selections).
    if len(text) > 2:
        qr = process_query_understanding(message)
        if qr["corrected"] or qr["expanded"] or qr["confidence"] < 0.9:
            ctx.query_original = qr["original"]
            ctx.query_clean = qr["clean"]
            ctx.query_corrected = qr["corrected"]
            # Use cleaned text for downstream rules
            text = qr["clean"].lower()
            clean = text.rstrip("?.,!;:")
            # Re-extract entities from cleaned text for better routing
            from app.orchestrator.extractor import extract_entities
            e = extract_entities(text)

    # ---- Stage 0b: Semantic intent classification ----
    # Run semantic intent classifier for debugging and enhanced routing.
    # This runs silently; if it fails, we continue with the existing rules.
    _semantic_intent = None
    _semantic_confidence = 0.0
    _semantic_debug = {}
    try:
        from app.orchestrator.intent_classifier import classify as classify_semantic
        _semantic_intent, _semantic_confidence, _semantic_debug = classify_semantic(text)
    except Exception:
        pass

    # ---- Stage 0c: Semantic topic enrichment ----
    # If entity extraction didn't find a topic but semantic classifier
    # strongly suggests one, use it. This enables follow-ups like
    # "Cost?" / "How much?" to resolve to "fee" within programme context.
    _semantic_topic_map = {
        "fee": "fee", "eligibility": "eligibility", "scholarships": "fee",
        "datesheet": "dates", "examination": "examination",
        "results": "results", "contact": "contact",
    }
    if e.topic is None and _semantic_intent in _semantic_topic_map:
        e.topic = _semantic_topic_map[_semantic_intent]

    # ---- Rule 1: Reset signal ----
    if e.is_reset:
        return Plan(
            action="welcome",
            confidence=1.0,
            reason="User requested reset",
        )

    # ---- Rule 2: Back ----
    if e.is_back:
        path = get_nav_path(chat_id)
        if path:
            return Plan(
                action="navigation",
                confidence=1.0,
                reason="User clicked back",
            )
        return Plan(action="welcome", confidence=1.0, reason="No nav path to go back from")

    # ---- Rule 3: Service keyword ----
    if e.service:
        return Plan(
            action="connector",
            target=e.service,
            confidence=0.95,
            reason=f"Service keyword detected: {e.service}",
        )

    # ---- Rule 4: Bare programme name (programme switch without topic) ----
    # Detect when user types just a programme name like "MBA" or "BCA"
    # Must come before option selection so it's treated as a switch, not nav.
    if e.word_count <= 2 and e.programme and not e.topic:
        if not ctx.programme or e.programme != ctx.programme:
            detail = lookup_programme(e.programme)
            if detail:
                ctx.programme = e.programme  # Update context pre-emptively
                return Plan(
                    action="structured",
                    response={
                        "type": "detail",
                        "title": detail.get("title", e.programme.upper()),
                        "fields": detail.get("fields", []),
                        "actions": detail.get("actions", _build_actions(e.programme, None)),
                        "context": _build_context_dict(ctx),
                    },
                    target=e.programme,
                    confidence=0.95,
                    reason=f"Bare programme name: {e.programme}",
                )

    # ---- Rule 5: Programme + topic with structured data (MUST come before option selection) ----
    # When context has an active programme and message is a known topic,
    # structured lookup takes priority over navigation.
    if ctx.programme and e.topic:
        value = lookup_field(ctx.programme, e.topic)
        if value:
            detail = lookup_programme(ctx.programme)
            title = detail.get("title", ctx.programme.upper()) if detail else ctx.programme.upper()
            fields = _build_topic_fields(e.topic, value, ctx.programme)
            return Plan(
                action="structured",
                response={
                    "type": "detail",
                    "title": f"{title} — {e.topic.replace('_', ' ').title()}",
                    "fields": fields,
                    "actions": _build_actions(ctx.programme, e.topic),
                    "context": _build_context_dict(ctx),
                },
                target=f"{ctx.programme}/{e.topic}",
                confidence=0.98,
                reason=f"Structured field lookup: {ctx.programme}/{e.topic}",
                )

    # ---- Rule 6: College context pre-check (before option selection) ----
    # College rules must come before option selection / broad keywords so that
    # queries like "fee" within college context show college-specific data.
    college_ref = is_college_reference(text)
    if college_ref:
        college_id = resolve_college(text)
        college = _college_svc.get_college(college_id) if college_id else None
        if college:
            college_topic = None
            if e.topic:
                college_topic = e.topic
            if not college_topic:
                for kw, mapped in COLLEGE_TOPICS.items():
                    if kw in text:
                        college_topic = mapped
                        break
            if college_topic:
                if college_topic == "fee":
                    fees = _college_svc.get_fees(college_id)
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": f"{college['name']} — Fee Structure",
                            "fields": [{"label": p.upper(), "value": f} for p, f in fees.items()] if fees else [{"label": "Fee", "value": "Contact college for current fee structure"}],
                            "actions": _build_college_actions(college_id, college_topic),
                        },
                        target=f"college/{college_id}/{college_topic}",
                        confidence=0.95,
                        reason=f"College + topic: {college_id}/{college_topic}",
                        extra={"college_id": college_id},
                    )
                if college_topic == "departments":
                    depts = _college_svc.get_departments(college_id)
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": f"{college['name']} — Departments",
                            "fields": [{"label": "Department", "value": d} for d in depts] if depts else [{"label": "Departments", "value": "Information not available"}],
                            "actions": _build_college_actions(college_id, college_topic),
                        },
                        target=f"college/{college_id}/departments",
                        confidence=0.95,
                        reason=f"College departments: {college_id}",
                        extra={"college_id": college_id},
                    )
                if college_topic == "courses":
                    progs = _college_svc.get_programmes(college_id)
                    options = [{"id": p["id"], "label": p["name"]} for p in progs] if progs else []
                    return Plan(
                        action="navigation",
                        response={
                            "type": "options",
                            "title": f"{college['name']} — Programmes",
                            "message": "Select a programme to see details.",
                            "options": options,
                            "context": {"breadcrumbs": [college["name"], "Programmes"]},
                        },
                        target=f"college/{college_id}/courses",
                        confidence=0.95,
                        reason=f"College programmes: {college_id}",
                        extra={"college_id": college_id},
                    )
                if college_topic == "about":
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": college["name"],
                            "fields": [
                                {"label": "About", "value": college.get("about", "")},
                                {"label": "Type", "value": college.get("type", "").title()},
                                {"label": "Established", "value": str(college.get("established", "N/A"))},
                                {"label": "NAAC Grade", "value": college.get("naac", "N/A")},
                                {"label": "Principal", "value": college.get("principal", "N/A")},
                                {"label": "Address", "value": college.get("address", "")},
                                {"label": "District", "value": college.get("district", "")},
                            ],
                            "actions": _build_college_actions(college_id, None),
                        },
                        target=f"college/{college_id}/about",
                        confidence=0.95,
                        reason=f"College about: {college_id}",
                        extra={"college_id": college_id},
                    )
                if college_topic == "contact":
                    contact = _college_svc.get_contact(college_id)
                    fields = [{"label": k.replace("_", " ").title(), "value": v} for k, v in contact.items()] if contact else []
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": f"{college['name']} — Contact",
                            "fields": fields,
                            "actions": _build_college_actions(college_id, "contact"),
                        },
                        target=f"college/{college_id}/contact",
                        confidence=0.95,
                        reason=f"College contact: {college_id}",
                        extra={"college_id": college_id},
                    )
                if college_topic == "facilities":
                    facilities = _college_svc.get_facilities(college_id)
                    fields = [{"label": "Facility", "value": f} for f in facilities] if facilities else [{"label": "Facilities", "value": "Information not available"}]
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": f"{college['name']} — Facilities",
                            "fields": fields,
                            "actions": _build_college_actions(college_id, "facilities"),
                        },
                        target=f"college/{college_id}/facilities",
                        confidence=0.95,
                        reason=f"College facilities: {college_id}",
                        extra={"college_id": college_id},
                    )
                if college_topic == "eligibility":
                    el = _college_svc.get_eligibility(college_id)
                    fields = [{"label": k.upper(), "value": v} for k, v in el.items()] if isinstance(el, dict) else [{"label": "Eligibility", "value": str(el)}]
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": f"{college['name']} — Eligibility",
                            "fields": fields,
                            "actions": _build_college_actions(college_id, "eligibility"),
                        },
                        target=f"college/{college_id}/eligibility",
                        confidence=0.95,
                        reason=f"College eligibility: {college_id}",
                        extra={"college_id": college_id},
                    )
                if college_topic == "principal":
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": f"{college['name']} — Principal",
                            "fields": [{"label": "Principal", "value": college.get("principal", "Not available")}],
                            "actions": _build_college_actions(college_id, None),
                        },
                        target=f"college/{college_id}/principal",
                        confidence=0.95,
                        reason=f"College principal: {college_id}",
                        extra={"college_id": college_id},
                    )
                return Plan(
                    action="rag",
                    target=f"{college['name']} {college_topic.replace('_', ' ')}",
                    confidence=0.7,
                    reason=f"College + topic (fallback to RAG): {college_id}/{college_topic}",
                    extra={"college_id": college_id},
                )
            overview = _college_svc.get_overview(college_id)
            if overview:
                fields = [
                    {"label": "Type", "value": overview.get("type", "").title()},
                    {"label": "Established", "value": str(overview.get("established", "N/A"))},
                    {"label": "NAAC Grade", "value": overview.get("naac", "N/A")},
                    {"label": "Principal", "value": overview.get("principal", "N/A")},
                    {"label": "District", "value": overview.get("district", "")},
                ]
                if overview.get("phone"):
                    fields.append({"label": "Phone", "value": overview["phone"]})
                if overview.get("email"):
                    fields.append({"label": "Email", "value": overview["email"]})
                return Plan(
                    action="structured",
                    response={
                        "type": "detail",
                        "title": college["name"],
                        "fields": fields,
                        "actions": _build_college_actions(college_id, None),
                        "context": {"breadcrumbs": [college["name"]], "college": college_id},
                    },
                    target=f"college/{college_id}",
                    confidence=0.95,
                    reason=f"College selected: {college_id}",
                    extra={"college_id": college_id},
                )

    # ---- Rule 7: Active college context + short follow-up ----
    if ctx.college and _is_college_followup(text, e, ctx):
        college = _college_svc.get_college(ctx.college)
        college_name = college["name"] if college else ctx.college_name or ctx.college
        for kw, mapped in COLLEGE_TOPICS.items():
            if _word_in_text(kw, text):
                if mapped == "fee":
                    fees = _college_svc.get_fees(ctx.college)
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": f"{college_name} — Fee Structure",
                            "fields": [{"label": p.upper(), "value": f} for p, f in fees.items()] if fees else [{"label": "Fee", "value": "Contact college"}],
                            "actions": _build_college_actions(ctx.college, "fee"),
                            "context": {"breadcrumbs": [college_name, "Fee"]},
                        },
                        target=f"college/{ctx.college}/fee",
                        confidence=0.95,
                        reason=f"College context follow-up: {ctx.college}/{mapped}",
                        extra={"college_id": ctx.college},
                    )
                if mapped == "departments":
                    depts = _college_svc.get_departments(ctx.college)
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": f"{college_name} — Departments",
                            "fields": [{"label": "Department", "value": d} for d in depts] if depts else [],
                            "actions": _build_college_actions(ctx.college, "departments"),
                            "context": {"breadcrumbs": [college_name, "Departments"]},
                        },
                        target=f"college/{ctx.college}/departments",
                        confidence=0.95,
                        reason=f"College departments follow-up: {ctx.college}",
                        extra={"college_id": ctx.college},
                    )
                if mapped == "courses":
                    progs = _college_svc.get_programmes(ctx.college)
                    options = [{"id": p["id"], "label": p["name"]} for p in progs] if progs else []
                    return Plan(
                        action="navigation",
                        response={
                            "type": "options",
                            "title": f"{college_name} — Programmes",
                            "message": "Select a programme to see details.",
                            "options": options,
                            "context": {"breadcrumbs": [college_name, "Programmes"]},
                        },
                        target=f"college/{ctx.college}/courses",
                        confidence=0.95,
                        reason=f"College programmes follow-up: {ctx.college}",
                        extra={"college_id": ctx.college},
                    )
                if mapped == "contact":
                    contact = _college_svc.get_contact(ctx.college)
                    fields = [{"label": k.replace("_", " ").title(), "value": v} for k, v in contact.items()] if contact else []
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": f"{college_name} — Contact",
                            "fields": fields,
                            "actions": _build_college_actions(ctx.college, "contact"),
                            "context": {"breadcrumbs": [college_name, "Contact"]},
                        },
                        target=f"college/{ctx.college}/contact",
                        confidence=0.95,
                        reason=f"College contact follow-up: {ctx.college}",
                        extra={"college_id": ctx.college},
                    )
                if mapped == "principal":
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": f"{college_name} — Principal",
                            "fields": [{"label": "Principal", "value": college.get("principal", "Not available") if college else "N/A"}],
                            "actions": _build_college_actions(ctx.college, None),
                            "context": {"breadcrumbs": [college_name, "Principal"]},
                        },
                        target=f"college/{ctx.college}/principal",
                        confidence=0.95,
                        reason=f"College principal follow-up: {ctx.college}",
                        extra={"college_id": ctx.college},
                    )
                if mapped == "facilities":
                    facilities = _college_svc.get_facilities(ctx.college)
                    fields = [{"label": "Facility", "value": f} for f in facilities] if facilities else []
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": f"{college_name} — Facilities",
                            "fields": fields,
                            "actions": _build_college_actions(ctx.college, "facilities"),
                            "context": {"breadcrumbs": [college_name, "Facilities"]},
                        },
                        target=f"college/{ctx.college}/facilities",
                        confidence=0.95,
                        reason=f"College facilities follow-up: {ctx.college}",
                        extra={"college_id": ctx.college},
                    )
                if mapped == "about":
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": college_name,
                            "fields": [
                                {"label": "About", "value": college.get("about", "") if college else ""},
                                {"label": "Type", "value": college.get("type", "").title() if college else ""},
                                {"label": "Established", "value": str(college.get("established", "N/A")) if college else "N/A"},
                                {"label": "NAAC Grade", "value": college.get("naac", "N/A") if college else "N/A"},
                            ],
                            "actions": _build_college_actions(ctx.college, None),
                            "context": {"breadcrumbs": [college_name, "About"]},
                        },
                        target=f"college/{ctx.college}/about",
                        confidence=0.95,
                        reason=f"College about follow-up: {ctx.college}",
                        extra={"college_id": ctx.college},
                    )
                if mapped == "eligibility":
                    el = _college_svc.get_eligibility(ctx.college)
                    fields = [{"label": k.upper(), "value": v} for k, v in el.items()] if isinstance(el, dict) else [{"label": "Eligibility", "value": str(el)}]
                    return Plan(
                        action="structured",
                        response={
                            "type": "detail",
                            "title": f"{college_name} — Eligibility",
                            "fields": fields,
                            "actions": _build_college_actions(ctx.college, "eligibility"),
                            "context": {"breadcrumbs": [college_name, "Eligibility"]},
                        },
                        target=f"college/{ctx.college}/eligibility",
                        confidence=0.95,
                        reason=f"College eligibility follow-up: {ctx.college}",
                        extra={"college_id": ctx.college},
                    )
                if mapped == "notices":
                    return Plan(
                        action="rag",
                        target=f"{college_name} notices notifications",
                        confidence=0.7,
                        reason=f"College notices (RAG): {ctx.college}",
                        extra={"college_id": ctx.college},
                    )
                if mapped == "prospectus":
                    return Plan(
                        action="rag",
                        target=f"{college_name} prospectus admission brochure",
                        confidence=0.7,
                        reason=f"College prospectus (RAG): {ctx.college}",
                        extra={"college_id": ctx.college},
                    )
                return Plan(
                    action="rag",
                    target=f"{college_name} {mapped.replace('_', ' ')}",
                    confidence=0.7,
                    reason=f"College context follow-up: {ctx.college}/{mapped} (RAG)",
                    extra={"college_id": ctx.college},
                )
        return Plan(
            action="rag",
            target=f"{college_name} {text}",
            confidence=0.65,
            reason=f"College context general follow-up: {ctx.college}",
            extra={"college_id": ctx.college},
        )

    # ---- Rule 8: Active college + programme option selection ----
    if ctx.college and e.programme and _college_svc.has_programme(ctx.college, e.programme):
        college = _college_svc.get_college(ctx.college)
        college_name = college["name"] if college else ctx.college_name or ctx.college
        prog_fees = _college_svc.get_programme_fees(ctx.college, e.programme)
        fields = [{"label": "Programme", "value": e.programme.upper()}]
        if prog_fees:
            fields.append({"label": "Fee", "value": prog_fees})
        return Plan(
            action="structured",
            response={
                "type": "detail",
                "title": f"{college_name} — {e.programme.upper()}",
                "fields": fields,
                "actions": [
                    {"id": "fee", "label": "Fee Details"},
                    {"id": "eligibility", "label": "Eligibility"},
                    {"id": "contact", "label": "Contact"},
                ],
                "context": {"breadcrumbs": [college_name, e.programme.upper()], "college": ctx.college},
            },
            target=f"college/{ctx.college}/{e.programme}",
            confidence=0.9,
            reason=f"College programme: {ctx.college}/{e.programme}",
            extra={"college_id": ctx.college},
        )

    # ---- Rule 9a: College-course discovery ----
    # "which colleges offer BCA" / "what courses are offered in GCW"
    # Detect college-course mapping queries
    course_discovery_match = _detect_course_college_query(text, ctx)
    if course_discovery_match:
        qtype = course_discovery_match["type"]
        if qtype == "course_to_colleges":
            pid = course_discovery_match["programme_id"]
            colleges_list = college_course_map.get_colleges_for_programme(pid)
            if colleges_list:
                ctx.last_selected_entity = "programme"
                options = [{"id": c["id"], "label": f"{c['name']} ({c['district']})"} for c in colleges_list]
                from app.orchestrator.context import _get_programme_label
                prog_name = _get_programme_label(pid) or pid.upper()
                return Plan(
                    action="navigation",
                    response={
                        "type": "options",
                        "title": f"Colleges offering {prog_name}",
                        "message": f"The following colleges offer {prog_name}:",
                        "options": options,
                        "context": {"breadcrumbs": [prog_name, "Colleges"]},
                    },
                    target=f"course/{pid}/colleges",
                    confidence=0.95,
                    reason=f"Course→colleges: {pid}",
                )
            return Plan(
                action="rag",
                target=f"which colleges offer {pid}",
                confidence=0.6,
                reason=f"Course→colleges fallback (no data): {pid}",
            )

        if qtype == "college_to_courses":
            cid = course_discovery_match["college_id"]
            college = _college_svc.get_college(cid)
            if college:
                programmes = college_course_map.get_college_programmes(cid)
                options = [{"id": p["id"], "label": p["name"]} for p in programmes]
                ctx.last_selected_entity = "college"
                return Plan(
                    action="navigation",
                    response={
                        "type": "options",
                        "title": f"Programmes at {college['name']}",
                        "message": "Select a programme to see details.",
                        "options": options,
                        "context": {"breadcrumbs": [college['name'], "Programmes"]},
                    },
                    target=f"college/{cid}/courses",
                    confidence=0.95,
                    reason=f"College→courses: {cid}",
                    extra={"college_id": cid},
                )

    # ---- Rule 9b: Follow-up with active programme context and college context ----
    # When user has both active programme AND college: fee/eligibility/documents/etc
    # should apply to the college's programme, not the generic programme.
    if ctx.college and ctx.programme and e.topic:
        # Check if the college has this programme
        if college_course_map.has_college_programme(ctx.college, ctx.programme):
            value = lookup_field(ctx.programme, e.topic)
            if value:
                college = _college_svc.get_college(ctx.college)
                college_name = college["name"] if college else ctx.college_name or ctx.college
                fields = _build_topic_fields(e.topic, value, ctx.programme)
                return Plan(
                    action="structured",
                    response={
                        "type": "detail",
                        "title": f"{college_name} — {ctx.programme.upper()} — {e.topic.replace('_', ' ').title()}",
                        "fields": fields,
                        "actions": _build_actions(ctx.programme, e.topic),
                        "context": {"breadcrumbs": [college_name, ctx.programme.upper(), e.topic.replace('_', ' ').title()]},
                    },
                    target=f"college/{ctx.college}/{ctx.programme}/{e.topic}",
                    confidence=0.95,
                    reason=f"College+programme+topic: {ctx.college}/{ctx.programme}/{e.topic}",
                    extra={"college_id": ctx.college},
                )

    # ---- Rule 9: Option selection (known ID) ----
    if is_option_selection(text):
        response = get_selection_response(chat_id, text)
        if response.get("type") in ("options", "detail"):
            return Plan(
                action="navigation",
                response=response,
                confidence=0.95,
                reason=f"Option selection: {text}",
            )

    # ---- Rule 10: Broad keyword / semantic intent ----
    intent_type, category = classify_nav(text)
    if intent_type == "broad" and category:
        response = get_broad_response(category)
        if response.get("type") in ("options", "detail"):
            extra = {}
            if _semantic_intent and _semantic_intent != category:
                extra["semantic_intent"] = _semantic_intent
                extra["semantic_confidence"] = _semantic_confidence
            return Plan(
                action="navigation",
                response=response,
                target=category,
                confidence=0.9,
                reason=f"Broad intent: {category}",
                extra=extra,
            )

    # ---- Rule 11: Programme + topic without structured data → RAG ----
    if ctx.programme and e.topic:
        # No structured data found, try RAG
        augmented = _augment_for_rag(ctx, e.topic)
        return Plan(
            action="rag",
            target=augmented,
            confidence=0.85,
            reason=f"Programme {ctx.programme} + topic {e.topic} (no structured data)",
            extra={"original_query": text, "augmented_query": augmented},
        )

    # ---- Rule 12: Programme switch detected (with topic, already handled bare name in Rule 4) ----
    prog_switch = detect_programme_switch(message)
    if prog_switch and (not ctx.programme or prog_switch != ctx.programme):
        # If the switch is accompanied by a topic, handle as structured
        if e.topic:
            value = lookup_field(prog_switch, e.topic)
            if value:
                detail = lookup_programme(prog_switch)
                title = detail.get("title", prog_switch.upper()) if detail else prog_switch.upper()
                fields = _build_topic_fields(e.topic, value, prog_switch)
                return Plan(
                    action="structured",
                    response={
                        "type": "detail",
                        "title": f"{title} — {e.topic.replace('_', ' ').title()}",
                        "fields": fields,
                        "actions": _build_actions(prog_switch, e.topic),
                        "context": _build_context_dict(ctx),
                    },
                    target=f"{prog_switch}/{e.topic}",
                    confidence=0.95,
                    reason=f"Programme switch to {prog_switch} with topic {e.topic}",
                )
            return Plan(
                action="rag",
                target=_augment_for_rag_from_prog(prog_switch, e.topic),
                confidence=0.85,
                reason=f"Programme switch to {prog_switch} + topic {e.topic} (no structured data)",
            )
        # Pure programme switch → show programme details
        detail = lookup_programme(prog_switch)
        if detail:
            return Plan(
                action="structured",
                response={
                    "type": "detail",
                    "title": detail.get("title", prog_switch.upper()),
                    "fields": detail.get("fields", []),
                    "actions": detail.get("actions", _build_actions(prog_switch, None)),
                    "context": _build_context_dict(ctx),
                },
                target=prog_switch,
                confidence=0.95,
                reason=f"Programme switch to {prog_switch}",
            )
        return Plan(
            action="navigation",
            target=prog_switch,
            confidence=0.8,
            reason=f"Programme switch to {prog_switch} (fallback to nav)",
        )

    # ---- Rule 13: Level + topic ----

    # ---- Rule 14: Short follow-up with programme context ----
    if ctx.programme and _is_short_followup(text, e):
        augmented = _augment_for_rag(ctx, clean)
        return Plan(
            action="rag",
            target=augmented,
            confidence=0.75,
            reason=f"Short follow-up with programme {ctx.programme}: '{text}'",
            extra={"original_query": text, "augmented_query": augmented},
        )

    # ---- Rule 15: Broad question with context → RAG ----
    if e.is_question and ctx.programme:
        augmented = _augment_for_rag(ctx, text)
        return Plan(
            action="rag",
            target=augmented,
            confidence=0.7,
            reason=f"Question with programme context: {text}",
            extra={"original_query": text, "augmented_query": augmented},
        )

    # ---- Rule 16: Authority / escalation intent ----
    authority_matches = _detect_authority_intent(text)
    if authority_matches:
        return Plan(
            action="authority",
            target=authority_matches[0].get("department_name", ""),
            confidence=0.7,
            reason=f"Authority intent detected: {authority_matches[0].get('authority_name', '')}",
            extra={"authorities": authority_matches, "original_query": text},
        )

    # ---- Rule 17: Broad question without context → RAG ----
    if e.is_question and not ctx.domain:
        return Plan(
            action="rag",
            target=text,
            confidence=0.6,
            reason=f"Question without context, using RAG: {text}",
        )

    # ---- Rule 18: Short ambiguous → clarify ----
    if e.word_count <= 3 and not ctx.domain:
        # See if it matches any domain
        return Plan(
            action="clarify",
            target="domain",
            confidence=0.5,
            reason=f"Short ambiguous message: '{text}'",
        )

    # ---- Rule 19: Everything else → RAG ----
    augmented = _augment_for_rag(ctx, text) if ctx.programme else text
    extra = {"original_query": text, "augmented_query": augmented}
    if _semantic_intent and _semantic_intent != "unknown":
        extra["semantic_intent"] = _semantic_intent
        extra["semantic_confidence"] = _semantic_confidence
    return Plan(
        action="rag",
        target=augmented,
        confidence=0.5,
        reason="Fallback to RAG",
        extra=extra,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_short_followup(text: str, entities: Any) -> bool:
    """Check if this is a short follow-up that benefits from context."""
    if entities.word_count > 4:
        return False
    if entities.is_question:
        return False
    clean = text.rstrip("?.,!;:")
    if clean in DOMAIN_KEYWORDS:
        return False
    return not (entities.word_count == 1 and clean in PROGRAMME_ALIASES)


def _build_topic_fields(topic: str, value: str, programme: str) -> list[dict[str, str]]:
    """Build a fields list for a topic-specific detail response."""
    label_map = {
        "fee": "Fee Structure",
        "eligibility": "Eligibility Criteria",
        "duration": "Programme Duration",
        "admission_mode": "Admission Mode",
        "documents": "Documents Required",
        "specializations": "Specializations",
        "syllabus": "Syllabus",
        "dates": "Important Dates",
        "prospectus": "Prospectus",
        "seats": "Seats / Intake",
        "placement": "Placement",
        "career": "Career Options",
    }
    label = label_map.get(topic, topic.replace("_", " ").title())
    return [{"label": label, "value": value}]


def _build_actions(programme: str | None, current_topic: str | None) -> list[dict[str, str]]:
    """Build action buttons, excluding the current topic."""
    all_actions = [
        {"id": "fee", "label": "Fee Structure"},
        {"id": "eligibility", "label": "Eligibility"},
        {"id": "duration", "label": "Duration"},
        {"id": "dates", "label": "Important Dates"},
        {"id": "documents", "label": "Documents"},
        {"id": "prospectus", "label": "Prospectus"},
    ]
    if current_topic:
        return [a for a in all_actions if a["id"] != current_topic]
    return all_actions


def _build_context_dict(ctx: ConversationContext) -> dict[str, Any]:
    """Build a context dict for frontend breadcrumbs."""
    crumbs = []
    if ctx.domain:
        crumbs.append(ctx.domain.title() if ctx.domain != "admissions" else "Admissions")
    if ctx.level:
        crumbs.append(ctx.level.upper())
    if ctx.programme:
        from app.orchestrator.context import _get_programme_label
        label = _get_programme_label(ctx.programme) or ctx.programme.upper()
        crumbs.append(label)
    result = {"programme": ctx.programme}
    if crumbs:
        result["breadcrumbs"] = crumbs
    return result


def _augment_for_rag(ctx: ConversationContext, text: str) -> str:
    """Build a context-augmented query string for RAG."""
    from app.orchestrator.context import _get_programme_label
    parts = []
    if ctx.programme:
        label = _get_programme_label(ctx.programme) or ctx.programme.upper()
        parts.append(label)
    if ctx.level:
        parts.append(ctx.level.upper())
    if ctx.domain:
        parts.append(ctx.domain.title() if ctx.domain != "admissions" else "Admissions")
    parts.append(text)
    return " ".join(parts)


def _augment_for_rag_from_prog(programme: str, topic: str) -> str:
    from app.orchestrator.context import _get_programme_label
    label = _get_programme_label(programme) or programme.upper()
    return f"{label} {topic.replace('_', ' ')}"


def _build_college_actions(college_id: str, current_topic: str | None) -> list[dict[str, str]]:
    """Build action buttons for a college, excluding the current topic."""
    all_actions = [
        {"id": "about", "label": "About"},
        {"id": "courses", "label": "Courses"},
        {"id": "departments", "label": "Departments"},
        {"id": "admissions", "label": "Admissions"},
        {"id": "fee", "label": "Fee Structure"},
        {"id": "eligibility", "label": "Eligibility"},
        {"id": "facilities", "label": "Facilities"},
        {"id": "contact", "label": "Contact"},
        {"id": "principal", "label": "Principal"},
    ]
    if current_topic:
        return [a for a in all_actions if a["id"] != current_topic]
    return all_actions


def _is_college_followup(text: str, entities: Any, ctx: ConversationContext) -> bool:
    """Check if a short message is a follow-up within college context."""
    if not ctx.college:
        return False
    if entities.word_count > 5:
        return False
    if entities.is_reset or entities.is_back:
        return False
    clean = text.strip().lower().rstrip("?.,!;:")
    # Allow DOMAIN_KEYWORDS that are also valid college topics (e.g. "fee" in college context)
    if clean in DOMAIN_KEYWORDS and clean not in COLLEGE_TOPICS:
        return False
    if clean in PROGRAMME_ALIASES:
        return False
    # Exclude course-college discovery queries so Rule 9a can handle them
    return not _detect_course_college_query(text, ctx)


def _word_in_text(word: str, text: str) -> bool:
    """Check if word appears as a whole word (word-boundary match) in text."""
    return bool(re.search(r"\b" + re.escape(word.lower()) + r"\b", text.strip().lower()))


_COURSE_COLLEGE_PATTERNS: list[tuple[str, str]] = [
    (r"(?:which|what)\s+colleges?\s+(?:offer|offers|offering|have|has|provide|teach|run)\s+(\w+)", "course_to_colleges"),
    (r"(?:which|what)\s+(?:courses?|programmes?)\s+(?:are\s+)?(?:offered|available|taught|run)\s+(?:at|in|by)\s+(.+)", "college_to_courses"),
    (r"(?:list|show|tell)\s+(?:all\s+)?colleges?\s+(?:with|offering|having|for)\s+(\w+)", "course_to_colleges"),
    (r"(?:list|show|tell)\s+(?:all\s+)?(?:courses?|programmes?)\s+(?:at|in|for|offered\s+by)\s+(.+)", "college_to_courses"),
    (r"what\s+(?:courses?|programmes?)\s+(?:does|do)\s+(.+)\s+(?:offer|offers|have|has|provide|teach|run)", "college_to_courses"),
    (r"(\w+)\s+(?:colleges?|college\s+offering)", "course_to_colleges"),
    (r"colleges?\s+(?:that\s+)?(?:offer|offering|offers)\s+(\w+)", "course_to_colleges"),
]


def _detect_course_college_query(text: str, ctx: ConversationContext) -> dict | None:
    """Detect if the message is asking about college-course mappings.

    Returns a dict with {'type': str, 'programme_id': str|None, 'college_id': str|None}
    or None if not a course-college query.
    """
    from app.college.aliases import resolve as resolve_college_alias

    for pattern, qtype in _COURSE_COLLEGE_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            key = m.group(1).strip().lower()

            if qtype == "course_to_colleges":
                # key should be a programme ID or name
                from app.orchestrator.context import PROGRAMME_ALIASES
                pid = PROGRAMME_ALIASES.get(key)
                if pid:
                    return {"type": qtype, "programme_id": pid, "college_id": None}
                # Check if it matches a known topic that happens to be a programme
                known_progs = {"ba", "bsc", "bcom", "bba", "bca", "btech", "bed", "ma", "msc", "mcom", "mba", "mca", "med"}
                if key in known_progs:
                    return {"type": qtype, "programme_id": key, "college_id": None}
                return None

            if qtype == "college_to_courses":
                cid = resolve_college_alias(key)
                if cid:
                    return {"type": qtype, "programme_id": None, "college_id": cid}
                return None

    return None


def _detect_authority_intent(message: str) -> list[dict]:
    """Check if the user is asking about a university office or requesting human contact.

    Only triggers when there is a strong match (score > 2.0) or an explicit escalation
    intent pattern. This prevents broad keyword overlap from stealing RAG queries.
    """
    from app.authority.matcher import detect_escalation_intent, find_authority
    try:
        # Only accept authority matches with high confidence
        matches = find_authority(message, top_k=3)
        strong_matches = [m for m in matches if m.get("_match_score", 0) >= 2.0]
        if strong_matches:
            return strong_matches
        # Escalation intent requires explicit human contact patterns
        esc_conf = detect_escalation_intent(message)
        if esc_conf >= 0.7:
            matches = find_authority(message, top_k=2)
            strong_matches = [m for m in matches if m.get("_match_score", 0) >= 2.0]
            if strong_matches:
                return strong_matches
    except Exception:
        pass
    return []
