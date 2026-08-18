"""
backend/app/orchestrator/engine.py

AI Orchestration Engine V2 — planner-driven, fast, context-aware.

Flow:
  1. Get conversation state + context
  2. Extract entities (fast rule-based)
  3. Handle auth flow / service detection
  4. Planner decides execution path
  5. Execute plan (structured / navigation / connector / rag / clarify)
  6. Update context
  7. Yield SSE-compatible events

Every stage is timed and logged via the metrics module.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.orm import Session

from app.analytics.collector import (
    collect_event,
    collect_knowledge_gap,
)
from app.chat.intent_router import (
    _PROGRAMME_DETAILS,
    WELCOME_OPTIONS,
)
from app.chat.service import run_chat
from app.college.service import CollegeService
from app.orchestrator.context import (
    PROGRAMME_ALIASES,
    ConversationContext,
    clear_college_context,
    clear_service_context,
    set_active_service,
    update_context_for_college,
)
from app.orchestrator.extractor import (
    SERVICE_KEYWORDS,
    SERVICE_PATTERNS,
    extract_entities,
)
from app.orchestrator.metrics import log_stage, stage_timer
from app.orchestrator.planner import plan
from app.orchestrator.state import (
    Breadcrumb,
    ConversationState,
    clear_state,
    get_auth_state,
    get_state,
    push_breadcrumb,
    service_needs_auth,
)
from app.services.registry import get_connector, get_service_options

# ---------------------------------------------------------------------------
# Service keyword detection (source: extractor.py)
# ---------------------------------------------------------------------------


def _detect_service_intent(message: str) -> str | None:
    text = message.strip().lower()
    
    # Stage 1: Exact keyword matching (fast path, deterministic)
    for phrase in SERVICE_PATTERNS:
        if phrase in text:
            return SERVICE_KEYWORDS[phrase]
    
    # Stage 2: Fuzzy matching for typos/phrases not in keyword dictionary
    from app.orchestrator.student_session import fuzzy_service_match
    fuzzy_result = fuzzy_service_match(text)
    if fuzzy_result:
        return fuzzy_result
    
    return None


def _is_informational_question(text: str) -> bool:
    """True when a service-noun message is phrased as a general knowledge
    question rather than a private-data or action request.

    "What is course registration?"  -> informational (topic question)
    "What is my CGPA?"              -> private (possessive) -> service
    "Show my attendance"            -> action verb -> service
    "When will results be announced?" -> informational (announcement news)

    Shared implementation lives in extractor.py so the planner can use it
    too (engine -> planner import would be circular).
    """
    from app.orchestrator.extractor import is_informational_question as _shared
    return _shared(text)


# ---------------------------------------------------------------------------
# Apply-request detection (confirmation flow) and subject extraction
# ---------------------------------------------------------------------------

# Action-ids the frontend sends as plain messages (detail-card buttons).
_ACTION_SUFFIXES = ("apply", "fill", "register", "view", "download", "check_status", "status")

# Services that support an apply-type action (confirmation + optional subjects).
_APPLY_ACTION_SERVICES = frozenset({
    "results", "re_evaluation", "xerox_copy", "migration", "registration",
    "exam_form", "semester_admission",
})

# Apply-style verbs that turn a plain service mention into a request.
_APPLY_VERBS = ("apply", "fill", "submit", "register", "file", "request")

# Services that collect subject names before execution.
_SUBJECT_REQUIRED_SERVICES = frozenset({"exam_form", "re_evaluation"})

_CONFIRM_YES = frozenset({
    "confirm", "confirm_apply", "yes", "y", "ok", "okay", "submit",
    "proceed", "go ahead", "do it", "1",
})
_CONFIRM_NO = frozenset({
    "cancel", "cancel_apply", "no", "n", "abort", "not now",
    "never mind", "don't", "dont", "2",
})


def _action_service(text: str) -> str | None:
    """Map an action-id message ("re_evaluation.apply") to its service name."""
    t = text.strip().lower()
    if "." not in t:
        return None
    prefix, suffix = t.rsplit(".", 1)
    if suffix not in _ACTION_SUFFIXES:
        return None
    connector = get_connector(prefix)
    if connector is not None:
        return connector.name
    return None


def _is_apply_request(text: str, service: str) -> bool:
    """True when a service mention reads as an apply/register/fill request.

    Pure action-ids like "admit_card.view" / "fee_receipt.download" route
    straight to the service; "re_evaluation.apply" / apply verbs go through
    the confirmation step.
    """
    if service not in _APPLY_ACTION_SERVICES:
        return False
    t = text.strip().lower()
    if t.endswith((".apply", ".fill", ".register")):
        return True
    if t.endswith((".view", ".download", ".check_status", ".status")):
        return False
    return any(v in t for v in _APPLY_VERBS)


def _extract_subject_list(text: str) -> list[str]:
    """Pull subject names from free text ("maths, physics and chemistry")."""
    t = text.strip()
    for lead in ("subjects", "subject", "for", "for subjects"):
        if t.lower().startswith(lead):
            t = t[len(lead):].strip(" :,;-")
            break
    parts = re.split(r"[,;/\n]|\band\b|&", t)
    subjects: list[str] = []
    for p in parts:
        name = re.sub(r"\s+", " ", p).strip(" .-")
        if name and name not in subjects:
            subjects.append(name)
        if len(subjects) >= 20:
            break
    return subjects


def _sanitize_service_error(err: str | None, fallback: str) -> str:
    """Sanitize connector errors before they reach the user.

    Internal fingerprints (SQL, drivers, paths, tracebacks) never leak into
    chat responses — they are replaced with a friendly fallback.
    """
    if not err:
        return fallback
    line = err.splitlines()[0].strip()
    lowered = line.lower()
    leaky = (
        "sqlalchemy", "sqlite", "psycopg", "postgres", "operationalerror",
        "integrityerror", "not null constraint", "unique constraint",
        "traceback", "file \"", "c:\\", "\\app\\", "exception:", "at 0x",
    )
    if any(m in lowered for m in leaky) or len(line) > 200:
        return fallback
    return line


def _anon_session_id(chat_id: str) -> str:
    """Derive a stable anonymous session ID from a chat_id (SHA1 prefix)."""
    return hashlib.sha1(chat_id.encode()).hexdigest()[:16]


_WORD_NUMBERS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}


def _parse_semester(text: str, default: int, word_ok: bool = True) -> int | None:
    """Parse a semester number from user text.

    Handles "sem 5", "5th semester", "fourth sem", "semester 6" and the
    relative forms "next/current/previous semester" (based on `default`).
    Returns None when the text contains no semester reference.
    """
    t = (text or "").strip().lower()
    if not t:
        return None

    # Relative forms first (they need the default)
    if "next semester" in t or "next sem" in t:
        return default + 1
    if "current semester" in t or "current sem" in t:
        return default
    if "previous semester" in t or "previous sem" in t:
        return max(1, default - 1)

    # Digit forms: "sem 5", "semester 6"
    m = re.search(r"\b(?:sem(?:ester)?)[\s:-]*(\d{1,2})\b", t)
    if m:
        return max(1, int(m.group(1)))

    # Digit forms: "5th semester", "1st sem"
    m = re.search(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:sem|semester)\b", t)
    if m:
        return max(1, int(m.group(1)))

    # Word forms
    if word_ok:
        for word, num in _WORD_NUMBERS.items():
            if word in t and ("sem" in t or "semester" in t):
                return num

    return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def process(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Main orchestration entry point. Yields SSE-compatible event dicts.
    """
    with stage_timer("total"):
        state = await get_state(chat_id)
        text = message.strip()
        ctx = state.context

        # ----- Stage 1: Entity extraction -----
        with stage_timer("entity_extraction"):
            entities = extract_entities(text)
        log_stage("entity_extraction", f"prog={entities.programme} topic={entities.topic} service={entities.service}")

        # ----- Stage 1b: Catalogue picker continuation -----
        # A previous catalogue turn (scheme picker, level picker, semester /
        # minor / curriculum-doc selector) left state.catalogue_pending. The
        # next message is that picker's option id (a scheme UUID, "level:ug",
        # "menu:fee", "semester:2", ...) — continue the flow directly instead
        # of planning it as free text.
        if state.catalogue_pending:
            with stage_timer("catalogue_continue"):
                try:
                    from app.catalogue.backend import continue_pending
                    events = await continue_pending(db, user_id, text, chat_id, state)
                except Exception as exc:
                    from app.utils.logging import log as _log
                    _log.error("catalogue continue failed chat=%s: %s", chat_id, exc)
                    events = None
            if events:
                for event in events:
                    yield event
                return
            state.catalogue_pending = None  # unresolvable -> normal flow

        # ----- Stage 1c: Slot-fill continuation -----
        # The planner asked for a missing slot ("Which programme?") and stored
        # the pending topic on state. If this message provides the missing
        # entity (and brings no topic of its own), resolve the ORIGINAL
        # request directly ("MCA" after "fee structure of which programme?"
        # must answer MCA fees, not show the MCA overview).
        if state.slot_topic and not entities.topic:
            resolved_slot = await _try_resolve_slot_fill(db, text, chat_id, state)
            if resolved_slot is not None:
                async for event in resolved_slot:
                    yield event
                return
        elif state.slot_topic and entities.topic:
            # User restated a topic of their own — the pending slot expires.
            state.slot_topic = None
            state.slot_request = None

        # ----- Stage 2: Service param collection / Auth flow check / Action continuation -----
        with stage_timer("auth_check"):
            if state.last_intent == "awaiting_service_params":
                async for event in _handle_service_param_input(db, user_id, text, chat_id, state):
                    yield event
                return
            if state.last_intent == "awaiting_credentials":
                async for event in _handle_auth_flow(db, user_id, text, chat_id, state):
                    yield event
                return
            if state.last_intent == "awaiting_confirm":
                async for event in _handle_confirm_input(db, user_id, text, chat_id, state):
                    yield event
                return
            if state.last_intent == "awaiting_subject":
                async for event in _handle_subject_input(db, user_id, text, chat_id, state):
                    yield event
                return
            if state.last_intent == "service_result" and state.service_context:
                sem_match = text.strip().lower()
                if sem_match.startswith("sem") and len(sem_match) > 3 and sem_match[3:].isdigit():
                    ctx.service_params["semester"] = sem_match[3:]
                    state.current_semester = int(sem_match[3:])
                    async for event in _route_student_service(db, user_id, text, chat_id, state, state.service_context):
                        yield event
                    return
                # Relative semester words ("next semester", "current semester")
                base_sem = state.current_semester or state.student_semester or 1
                rel_sem = _parse_semester(text, base_sem)
                if rel_sem:
                    ctx.service_params["semester"] = str(rel_sem)
                    state.current_semester = rel_sem
                    async for event in _route_student_service(db, user_id, text, chat_id, state, state.service_context):
                        yield event
                    return
            service_name = _detect_service_intent(text) or _action_service(text)

# ----- Student Services catalogue request -----
        # "student services" must show the Student Services catalogue,
        # NOT the generic university information menu.
        text_lower = text.strip().lower()
        student_service_phrases = (
            "student services",
            "student service",
            "show student services",
            "open student services",
            "student services please",
            "i need student services",
            "access student services",
            "student portal services",
            "my student services",
        )
        # Special case: helpdesk should NOT show Student Services catalogue
        # or require auth - it should give general information publicly
        if text_lower == "helpdesk":
            from app.orchestrator.student_session import portal_menu_payload
            
            # Helpdesk goes to general info, not Student Services catalogue
            # Yield a detail response instead
            yield {
                "type": "detail",
                "title": "Helpdesk / Support",
                "message": "Contact the university helpdesk for assistance.",
                "context": ctx.__dict__ if ctx else {},
            }
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
            return
        
        # Exact match or fuzzy tolerate minor typos via normalization
        import unicodedata
        normalized = (
            unicodedata.normalize("NFD", text_lower)
            .encode("ascii", "ignore")
            .decode("ascii")
        )
        is_student_service = (
            text_lower in student_service_phrases
            or normalized in [p.encode("ascii", "ignore").decode("ascii") for p in student_service_phrases]
        )
        if is_student_service:
            yield {
                "type": "options",
                "title": "Student Services",
                "message": "Here are the available Student Services. Please select one or type the service you need.",
                "options": get_service_options(),
                "context": ctx.__dict__ if ctx else {},
            }
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
            return

        # ----- Stage 3: Service intent -----
        if service_name:
            # A complaint that merely mentions a service ("result nahi aa
            # raha", "exam form nahi mila") is a GRIEVANCE, not a service
            # request. The planner's raw-message grievance detector decides
            # these; let it run instead of pulling the user into the
            # credential flow for a service they did not ask to use.
            try:
                from app.grievance.detect import detect_grievance
                if detect_grievance(text)["is_grievance"]:
                    service_name = None
            except Exception:
                pass
            # Office/contact questions ("who should I contact about my
            # result?") are authority queries, not service requests — the
            # planner resolves the office and routes there.
            if service_name:
                try:
                    from app.orchestrator.planner import _detect_authority_intent
                    if _detect_authority_intent(text):
                        service_name = None
                except Exception:
                    pass
            # Informational phrasing ("what is course registration?",
            # "when will results be announced?") asks about the service as
            # a topic — answer from knowledge instead of demanding login.
            if service_name and _is_informational_question(text):
                service_name = None
        if service_name:
            with stage_timer("service_routing"):
                # Set service context before routing
                set_active_service(state.context, service_name)
                # Apply-type requests get a confirmation step before execution
                if _is_apply_request(text, service_name):
                    state.pending_service = service_name
                    state.pending_action = "confirm_apply"
                    state.pending_query = text
                    state.pending_params = dict(state.context.service_params)
                    state.last_intent = "awaiting_confirm"
                    yield {
                        "type": "options",
                        "title": "Confirm request",
                        "message": f"Would you like me to submit this {service_name.replace('_', ' ').title()} request for you?",
                        "options": [
                            {"id": "confirm_apply", "label": "Yes, submit it"},
                            {"id": "cancel_apply", "label": "Cancel"},
                        ],
                    }
                    yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
                    return
                async for event in _route_student_service(db, user_id, text, chat_id, state, service_name):
                    yield event
            return

        # ----- Stage 4: Planner -----
        plan_t0 = time.perf_counter()
        planner_plan = plan(text, ctx, chat_id, entities)
        planner_latency_ms = int((time.perf_counter() - plan_t0) * 1000)
        log_stage("planning", f"action={planner_plan.action} target={planner_plan.target} confidence={planner_plan.confidence:.2f} reason={planner_plan.reason} ({planner_latency_ms}ms)")

        # ----- Stage 5: Execute plan -----
        async for event in _execute_plan(db, user_id, text, chat_id, state, ctx, entities, planner_plan, planner_latency_ms=planner_latency_ms):
            yield event

        # ----- Stage 6: Persist canonical query contract -----
        # The last contract lets later turns inherit resolved fields (e.g. the
        # catalogue programme row UUID) without re-resolving from raw text.
        try:
            contract = (planner_plan.extra or {}).get("contract")
            if isinstance(contract, dict):
                state.last_contract = contract
                ctx._last_contract = contract
        except Exception:
            pass


async def _execute_plan(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
    ctx: ConversationContext,
    entities: Any,
    plan_result: Any,
    planner_latency_ms: int | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Execute a plan and yield SSE events."""
    action = plan_result.action
    anon_session = _anon_session_id(chat_id)
    t0 = time.perf_counter()

    def _make_event(**kw: Any) -> dict[str, Any]:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        base = {
            "anon_session_id": anon_session,
            "conversation_id": chat_id,
            "planner_action": action,
            "detected_intent": state.last_intent,
            "response_time_ms": elapsed_ms,
            "planner_latency_ms": planner_latency_ms,
            "detected_programme": entities.programme or ctx.programme,
            "detected_topic": entities.topic or ctx.topic,
            "detected_college": ctx.college,
            "detected_level": entities.level or ctx.level,
            "query_original": ctx.query_original,
            "query_corrected": ctx.query_corrected,
        }
        base.update(kw)
        return {k: v for k, v in base.items() if v is not None}

    if action == "welcome":
        state = await get_state(chat_id)
        preserved = {
            "student_reg_no": state.student_reg_no,
            "student_name": state.student_name,
            "student_programme": state.student_programme,
            "student_semester": state.student_semester,
            "student_session_id": state.student_session_id,
        }
        await clear_state(chat_id)
        if any(v is not None for v in preserved.values()):
            state = await get_state(chat_id)
            for k, v in preserved.items():
                setattr(state, k, v)
        yield WELCOME_OPTIONS
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        state.last_intent = "navigation"
        asyncio.ensure_future(collect_event(**_make_event(
            response_source="welcome", route_chosen="welcome",
            conversation_completed=True,
        )))
        return

    if action == "greeting":
        yield {"type": "token", "text": "Hello! Welcome to the CUS AI Assistant. I can help you with admissions, courses, fee details, exam schedules, and more. Select a topic below or type your question."}
        yield WELCOME_OPTIONS
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        state.last_intent = "navigation"
        asyncio.ensure_future(collect_event(**_make_event(
            response_source="greeting", route_chosen=action,
            conversation_completed=True,
        )))
        return

    if action == "structured":
        response = plan_result.response
        if response:
            _update_context_from_plan(ctx, plan_result)
            _add_context_to_response(response, ctx)
            yield response
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
            state.last_intent = "navigation"
            asyncio.ensure_future(collect_event(**_make_event(
                response_source="structured", route_chosen=action,
                structured_lookup_used=True,
                conversation_completed=True,
            )))
        return

    if action == "navigation":
        response = plan_result.response
        if response:
            _update_context_from_plan(ctx, plan_result)
            await _update_nav_breadcrumb(chat_id, state, response, message)
            _add_context_to_response(response, ctx)
            yield response
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
            state.last_intent = "navigation"
            asyncio.ensure_future(collect_event(**_make_event(
                response_source="navigation", route_chosen=action,
                conversation_completed=True,
            )))
        else:
            await clear_state(chat_id)
            yield WELCOME_OPTIONS
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return

    if action == "connector":
        async for event in _route_service(db, user_id, message, chat_id, state, plan_result.target):
            yield event
        asyncio.ensure_future(collect_event(**_make_event(
            response_source="connector", route_chosen=action,
            service_requested=plan_result.target,
        )))
        return

    if action == "rag":
        query = plan_result.target or message
        _update_context_from_rag(ctx, entities, query)
        state.last_intent = "knowledge"
        rag_t0 = time.perf_counter()
        async for event in run_chat(db, user_id, query, chat_id, context=_build_rag_context(ctx, entities)):
            yield event
        rag_ms = int((time.perf_counter() - rag_t0) * 1000)
        asyncio.ensure_future(collect_event(**_make_event(
            response_source="rag", route_chosen=action,
            rag_used=True, rag_latency_ms=rag_ms,
            conversation_completed=True,
            query_original=ctx.query_original,
            query_corrected=ctx.query_corrected,
        )))
        return

    if action == "clarify":
        yield _build_clarification(ctx, plan_result.target)
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        state.last_intent = "clarification"
        asyncio.ensure_future(collect_knowledge_gap(
            gap_type="repeated_clarification",
            query_text=message,
            suggestion=f"User needed clarification on: {plan_result.target}",
        ))
        asyncio.ensure_future(collect_event(**_make_event(
            response_source="clarification", route_chosen=action,
        )))
        return

    if action == "authority":
        async for event in _handle_authority_route(db, user_id, message, chat_id, state, plan_result):
            yield event
        asyncio.ensure_future(collect_event(**_make_event(
            response_source="authority", route_chosen=action,
            service_requested=plan_result.target,
        )))
        return

    if action == "grievance":
        extra = plan_result.extra or {}
        prefill = extra.get("query") or message
        category = extra.get("category") or "Other"
        yield {"type": "token", "text": "I hear you — let me set up a grievance report so the right office can look into it."}
        yield {"type": "grievance", "payload": {"prefill": prefill, "category": category}}
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        state.last_intent = "grievance"
        asyncio.ensure_future(collect_event(**_make_event(
            response_source="grievance", route_chosen=action,
            conversation_completed=True,
        )))
        return

    if action == "llm":
        state.last_intent = "knowledge"
        llm_t0 = time.perf_counter()
        async for event in run_chat(db, user_id, message, chat_id):
            yield event
        llm_ms = int((time.perf_counter() - llm_t0) * 1000)
        asyncio.ensure_future(collect_event(**_make_event(
            response_source="llm", route_chosen=action,
            llm_used=True, llm_latency_ms=llm_ms,
            conversation_completed=True,
        )))
        return

    if action == "catalogue":
        # Structured academic catalogue route (schemes / programmes / subjects /
        # fee / eligibility / semesters / credits / outcomes / curriculum...).
        # The handler stores any picker continuation on state.catalogue_pending.
        req = (plan_result.extra or {}).get("req")
        if req:
            async for event in _handle_catalogue(db, user_id, message, chat_id, state, req):
                yield event
            return
        # No request payload — fall through to knowledge retrieval.
        query = plan_result.target or message
        state.last_intent = "knowledge"
        async for event in run_chat(db, user_id, query, chat_id, context=_build_rag_context(ctx, entities)):
            yield event
        return

    if action == "slot_fill":
        # Missing-entity question ("Which programme?") — remember the pending
        # topic so the user's next message continues the ORIGINAL request.
        extra = plan_result.extra or {}
        pending_topic = extra.get("pending_topic") or entities.topic
        slot_field = extra.get("slot") or plan_result.target or "programme"
        state.slot_topic = pending_topic
        state.slot_request = {"topic": pending_topic, "slot": slot_field}
        state.last_intent = "slot_fill"
        yield _build_slot_fill_question(pending_topic, slot_field)
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        asyncio.ensure_future(collect_event(**_make_event(
            response_source="slot_fill", route_chosen=action,
            conversation_completed=True,
        )))
        return

    if action == "news":
        # Current notices / circulars / calendar from the synced website
        # knowledge base — never a dead-end menu.
        query = plan_result.target or message
        _update_context_from_rag(ctx, entities, query)
        state.last_intent = "knowledge"
        rag_t0 = time.perf_counter()
        async for event in run_chat(db, user_id, query, chat_id, context=_build_rag_context(ctx, entities)):
            yield event
        rag_ms = int((time.perf_counter() - rag_t0) * 1000)
        asyncio.ensure_future(collect_event(**_make_event(
            response_source="news", route_chosen=action,
            rag_used=True, rag_latency_ms=rag_ms,
            conversation_completed=True,
        )))
        return

    if action == "comparison":
        # Side-by-side programme comparison — structured catalogue data when
        # both programmes exist, else scoped knowledge retrieval.
        async for event in _handle_comparison(db, user_id, message, chat_id, state, ctx, entities, plan_result):
            yield event
        return

    # Fallback (unknown action) — never silently dead-end
    from app.utils.logging import log as _log
    _log.warning("unhandled planner action=%s target=%s — falling back to knowledge", action, plan_result.target)
    state.last_intent = "knowledge"
    async for event in run_chat(db, user_id, message, chat_id):
        yield event


# ---------------------------------------------------------------------------
# Context update helpers
# ---------------------------------------------------------------------------


async def _try_resolve_slot_fill(
    db: Session,
    text: str,
    chat_id: str,
    state: ConversationState,
) -> AsyncGenerator[dict[str, Any], None] | None:
    """Resolve a pending slot-fill with the user's reply.

    When the planner asked "Which programme?" for a pending topic (fee,
    eligibility, ...) and the user now names a programme, this synthesizes
    "<programme> <topic>" and runs it through the same catalogue detector so
    the ORIGINAL request is answered directly.
    """
    ctx = state.context
    pending_topic = state.slot_topic
    try:
        from app.orchestrator.extractor import extract_entities as _extract
        syn_entities = _extract(f"{text} {pending_topic.replace('_', ' ')}")
        if not syn_entities.programme and not ctx.programme:
            return None
        from app.catalogue.detect import detect_catalogue_request
        req = detect_catalogue_request(f"{text} {pending_topic.replace('_', ' ')}", ctx, syn_entities)
        if not req:
            return None
        state.slot_topic = None
        state.slot_request = None
        return _handle_catalogue(db, "slot_resolve", text, chat_id, state, req, entities=syn_entities)
    except Exception:
        state.slot_topic = None
        state.slot_request = None
        return None


async def _handle_catalogue(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
    request: dict[str, Any],
    entities: Any = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Execute a catalogue request and yield its SSE events.

    Exceptions are contained: the user gets a friendly message (with a trace
    identifier logged server-side) instead of a broken stream.
    """
    from app.catalogue.backend import handle_catalogue
    from app.utils.logging import log as _log

    anon_session = _anon_session_id(chat_id)
    try:
        events = await handle_catalogue(db, user_id, message, chat_id, state, request)
    except Exception as exc:
        _log.error("catalogue handler failed chat=%s req=%s: %s", chat_id, request.get("op"), exc)
        yield {
            "type": "error",
            "message": "I couldn't load that information right now. Please try again in a moment.",
            "ref": anon_session[:8],
        }
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return
    for event in events:
        yield event
    if entities is not None:
        asyncio.ensure_future(collect_event(
            anon_session_id=anon_session,
            conversation_id=chat_id,
            planner_action="catalogue",
            response_source="catalogue",
            route_chosen=request.get("op", "catalogue"),
            detected_programme=getattr(entities, "programme", None) or state.context.programme,
            detected_topic=getattr(entities, "topic", None) or state.context.topic,
            conversation_completed=True,
        ))


async def _handle_comparison(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
    ctx: ConversationContext,
    entities: Any,
    plan_result: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    """Answer a programme comparison.

    Structured catalogue data (fee, eligibility, duration, credits, subjects)
    is rendered side-by-side when both programmes exist in the catalogue;
    otherwise the request falls back to knowledge retrieval scoped to both
    programme names.
    """
    programmes = list(getattr(entities, "programmes", None) or [])
    if not programmes:
        programmes = list(getattr(ctx, "programmes", None) or [])
    if len(programmes) < 2:
        programmes = None

    try:
        from app.catalogue.service import get_programme, resolve_programme
        if programmes:
            resolved: list[tuple[str, dict]] = []
            for pid in programmes:
                row = resolve_programme(pid)
                if not row:
                    continue
                detail = get_programme(row["id"])
                if detail:
                    resolved.append((pid, detail))
            if len(resolved) >= 2:
                rows = _render_comparison_rows(resolved)
                yield {
                    "type": "detail",
                    "title": "Programme Comparison",
                    "message": f"Here's how the requested programmes compare:",
                    "fields": rows,
                    "context": {"breadcrumbs": ["Programmes", "Comparison"]},
                }
                yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
                state.last_intent = "knowledge"
                ctx.programmes = programmes
                asyncio.ensure_future(collect_event(
                    anon_session_id=_anon_session_id(chat_id),
                    conversation_id=chat_id,
                    planner_action="comparison",
                    response_source="catalogue",
                    route_chosen="comparison",
                    detected_programme=programmes[0],
                    conversation_completed=True,
                ))
                return
    except Exception:
        pass

    # Fallback: scoped knowledge retrieval mentioning both programmes
    _update_context_from_rag(ctx, entities, message)
    state.last_intent = "knowledge"
    async for event in run_chat(db, user_id, message, chat_id, context=_build_rag_context(ctx, entities)):
        yield event


def _render_comparison_rows(resolved: list[tuple[str, dict]]) -> list[dict[str, str]]:
    """Render a side-by-side comparison table from catalogue programme data."""
    labels = {
        "code": "Code",
        "name": "Name",
        "level": "Level",
        "duration_years": "Duration (years)",
        "total_credits": "Total Credits",
        "minor_count": "Minors Offered",
        "subject_count": "Subjects",
        "scheme_name": "Academic Scheme",
    }
    rows: list[dict[str, str]] = []

    def _code(pid: str, detail: dict) -> str:
        return str(detail.get("code") or pid).upper()

    def _value(detail: dict, field: str) -> str:
        value = detail.get(field)
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, (list, tuple)):
            return f"{len(value)}" if value else "—"
        return str(value)

    for field in ("code", "name", "level", "scheme_name", "duration_years", "total_credits", "minor_count", "subject_count"):
        if field in ("code", "name"):
            continue
        if all(detail.get(field) is None for _, detail in resolved):
            continue
        rows.append({"label": labels[field], **{_code(pid, detail): _value(detail, field) for pid, detail in resolved}})

    fee_rows = {}
    for pid, detail in resolved:
        fee = detail.get("fee_structure")
        if isinstance(fee, list) and fee:
            fee_rows[_code(pid, detail)] = "; ".join(
                f"{e.get('label')}: {e.get('value')}" for e in fee if isinstance(e, dict) and e.get("value")
            ) or "—"
        elif fee:
            fee_rows[_code(pid, detail)] = str(fee)
        else:
            fee_rows[_code(pid, detail)] = "—"
    if any(v != "—" for v in fee_rows.values()):
        rows.append({"label": "Fee Structure", **fee_rows})

    rows.append({"label": "Eligibility", **{_code(pid, detail): str(detail.get("eligibility") or "—") for pid, detail in resolved}})
    return rows


def _build_slot_fill_question(topic: str | None, field: str) -> dict[str, Any]:
    """Build the targeted missing-entity question for slot-fill."""
    topic_label = (topic or "that").replace("_", " ")
    message = f"I can help with {topic_label} — which programme would you like to check?"
    options: list[dict[str, str]] = []
    try:
        from app.catalogue.service import list_catalogue_programmes
        rows = list_catalogue_programmes()[:8]
        for row in rows:
            code = str(row.get("code") or "").strip()
            if code:
                options.append({"id": code.lower(), "label": code})
    except Exception:
        pass
    if not options:
        options = [{"id": p, "label": p.upper()} for p in ("bca", "bba", "ba", "bsc", "bcom", "mca", "mba", "mcom")]
    return {
        "type": "options",
        "title": "Which programme?",
        "message": message,
        "options": options,
    }


def _update_context_from_plan(ctx: ConversationContext, plan_result: Any) -> None:
    """Update context fields based on the executed plan."""
    extra = plan_result.extra or {}

    # College context update from extra data
    college_id = extra.get("college_id")
    if college_id:
        college = CollegeService.get_college(college_id)
        college_name = college["name"] if college else college_id
        target = plan_result.target or ""
        # Extract topic from target (e.g., "college/{id}/fee" -> "fee")
        college_topic = None
        if "/" in target:
            parts = target.split("/")
            if len(parts) >= 3:
                college_topic = parts[-1]
        update_context_for_college(ctx, college_id, college_name, college_topic)
        ctx.last_selected_entity = "college"
    else:
        # If no college in the plan and no college reference, keep existing college context
        # Only clear if user explicitly starts fresh
        pass

    if plan_result.action == "structured":
        target = plan_result.target or ""
        if target.startswith("college/"):
            # College structured response — don't touch non-college context fields
            return
        if "/" in target:
            prog, topic = target.split("/", 1)
            ctx.programme = prog
            ctx.programme_id = prog
            ctx.topic = topic
            ctx.last_selected_entity = "programme"
        elif target in PROGRAMME_ALIASES:
            ctx.programme = target
            ctx.programme_id = target
            _derive_level(ctx, target)
            ctx.last_selected_entity = "programme"
        elif target in ("ug", "pg", "phd", "integrated", "dyd"):
            ctx.level = target
        elif target in ("admissions", "fee", "courses", "results", "datesheet",
                         "syllabus", "scholarships", "notices", "downloads",
                         "hostel", "examination", "departments", "colleges", "contact"):
            ctx.domain = target
            # If user navigates to "colleges", don't carry old college context
            if target == "colleges":
                clear_college_context(ctx)

    elif plan_result.action == "navigation":
        # Try to derive context from target first
        target = plan_result.target
        if target:
            if target.startswith("college/"):
                return
            if target in PROGRAMME_ALIASES:
                ctx.programme = target
                ctx.programme_id = target
                ctx.last_selected_entity = "programme"
            elif target in ("ug", "pg", "phd", "integrated", "dyd"):
                ctx.level = target
            elif target in ("admissions", "fee", "courses", "results", "datesheet",
                             "syllabus", "scholarships", "notices", "downloads",
                             "hostel", "examination", "departments", "colleges", "contact"):
                ctx.domain = target
                if target == "colleges":
                    clear_college_context(ctx)
        # Also try to derive from the response title
        if plan_result.response:
            title = (plan_result.response.get("title") or "").lower()
            if not ctx.level:
                for lvl in ("ug", "pg", "phd", "integrated", "dyd"):
                    if lvl in title:
                        ctx.level = lvl
                        break
            if not ctx.domain:
                for dom in ("admissions", "courses", "fee", "results", "datesheet",
                            "syllabus", "scholarships", "notices", "downloads",
                            "hostel", "examination", "departments", "colleges", "contact"):
                    if dom in title:
                        ctx.domain = dom
                        break

    ctx.pending_clarification = None
    ctx.clarification_field = None


def _build_rag_context(ctx: ConversationContext, entities: Any) -> dict[str, Any]:
    """Build the retrieval context dict passed into RAG.

    Scopes retrieval to the active college (when the conversation is
    college-anchored) and augments it with programme/topic/scheme/semester
    context so retrieved chunks match the conversation, not just the words.
    """
    rag_ctx: dict[str, Any] = {}
    if ctx.college:
        rag_ctx["college_id"] = ctx.college
    if ctx.college_name:
        rag_ctx["college_name"] = ctx.college_name
    if entities is not None and getattr(entities, "programme", None):
        rag_ctx["programme"] = entities.programme
    elif ctx.programme:
        rag_ctx["programme"] = ctx.programme
    if entities is not None and getattr(entities, "programmes", None):
        rag_ctx["programmes"] = entities.programmes
    elif ctx.programmes:
        rag_ctx["programmes"] = ctx.programmes
    if entities is not None and getattr(entities, "topic", None):
        rag_ctx["topic"] = entities.topic
    elif ctx.topic:
        rag_ctx["topic"] = ctx.topic
    if getattr(ctx, "academic_scheme", None):
        rag_ctx["academic_scheme"] = ctx.academic_scheme
    if getattr(ctx, "catalogue_scheme_code", None) and not rag_ctx.get("academic_scheme"):
        rag_ctx["academic_scheme"] = ctx.catalogue_scheme_code
    if getattr(ctx, "catalogue_semester", None) is not None:
        rag_ctx["semester"] = ctx.catalogue_semester
    if getattr(ctx, "semester", None) and "semester" not in rag_ctx:
        try:
            rag_ctx["semester"] = int(ctx.semester)
        except (TypeError, ValueError):
            pass
    if getattr(ctx, "catalogue_category", None):
        rag_ctx["category"] = ctx.catalogue_category
    rag_ctx["scope"] = "college" if ctx.college else "university"
    return rag_ctx


def _update_context_from_rag(ctx: ConversationContext, entities: Any, query: str) -> None:
    """Update context after a RAG response."""
    if entities.programme:
        ctx.programme = entities.programme
        ctx.programme_id = entities.programme
        _derive_level(ctx, entities.programme)
    if entities.topic:
        ctx.topic = entities.topic
    if entities.domain:
        ctx.domain = entities.domain
    ctx.last_document = None
    ctx.pending_clarification = None
    ctx.clarification_field = None


def _derive_level(ctx: ConversationContext, programme: str) -> None:
    known_ug = {"ba", "bsc", "bcom", "bba", "bca", "btech", "bed"}
    known_pg = {"ma", "msc", "mcom", "mba", "mca", "med"}
    if programme in known_ug:
        ctx.level = "ug"
    elif programme in known_pg:
        ctx.level = "pg"
    elif programme == "phd":
        ctx.level = "phd"


# ---------------------------------------------------------------------------
# Clarification builder
# ---------------------------------------------------------------------------


def _build_clarification(ctx: ConversationContext, field: str | None) -> dict[str, Any]:
    """Build a clarification question for the user."""
    if field == "programme":
        return {
            "type": "options",
            "title": "Which programme?",
            "message": f"You selected {ctx.level.upper() if ctx.level else 'a'} level. Which specific programme are you interested in?",
            "options": [
                {"id": "bca", "label": "BCA"},
                {"id": "bba", "label": "BBA"},
                {"id": "ba", "label": "BA"},
                {"id": "bsc", "label": "B.Sc"},
                {"id": "bcom", "label": "B.Com"},
            ],
        }
    if field == "domain":
        return {
            "type": "options",
            "title": "How can I help you?",
            "message": "What would you like to know about?",
            "options": [
                {"id": "admissions", "label": "Admissions"},
                {"id": "fee", "label": "Fee Structure"},
                {"id": "courses", "label": "Courses"},
                {"id": "results", "label": "Results"},
                {"id": "examination", "label": "Examinations"},
            ],
        }
    return {
        "type": "options",
        "title": "Can you clarify?",
        "message": "I'm not sure what you're looking for.",
        "options": [
            {"id": "admissions", "label": "Admissions"},
            {"id": "courses", "label": "Courses"},
            {"id": "fee", "label": "Fee"},
            {"id": "results", "label": "Results"},
        ],
    }


# ---------------------------------------------------------------------------
# Context-to-response helper
# ---------------------------------------------------------------------------


def _add_context_to_response(response: dict, ctx: ConversationContext) -> None:
    """Attach context breadcrumb trail to the response for frontend rendering."""
    crumbs = []
    if ctx.college_name:
        crumbs.append(ctx.college_name)
    if ctx.college_programme:
        crumbs.append(ctx.college_programme.upper())
    elif ctx.college_topic and ctx.college_topic not in ("about",):
        crumbs.append(ctx.college_topic.replace("_", " ").title())
    if ctx.domain:
        crumbs.append(ctx.domain.title() if ctx.domain != "admissions" else "Admissions")
    if ctx.level:
        crumbs.append(ctx.level.upper())
    if ctx.programme:
        label = _get_programme_label(ctx.programme) or ctx.programme.upper()
        crumbs.append(label)
    if ctx.topic and ctx.topic not in ("", None):
        crumbs.append(ctx.topic.replace("_", " ").title())
    result: dict = {"breadcrumbs": crumbs} if crumbs else {}
    if ctx.programme:
        result["programme"] = ctx.programme
    if ctx.college:
        result["college"] = ctx.college
        result["college_name"] = ctx.college_name
    if result:
        response["context"] = result

    # Attach query understanding metadata to response
    if ctx.query_original and ctx.query_corrected:
        response["_query"] = {
            "original": ctx.query_original,
            "clean": ctx.query_clean,
            "corrected": True,
        }


# ---------------------------------------------------------------------------
# Breadcrumb helper
# ---------------------------------------------------------------------------


async def _update_nav_breadcrumb(
    chat_id: str,
    state: ConversationState,
    response: dict,
    text: str,
) -> None:
    title = response.get("title", "") or response.get("message", "")
    crumb = Breadcrumb(label=title or text, type=response.get("type", "nav"))
    await push_breadcrumb(chat_id, crumb)


# ---------------------------------------------------------------------------
# Label helper
# ---------------------------------------------------------------------------


def _get_programme_label(programme_id: str) -> str | None:
    detail = _PROGRAMME_DETAILS.get(programme_id)
    if detail:
        return detail.get("title")
    return None


# ---------------------------------------------------------------------------
# Auth flow
# ---------------------------------------------------------------------------


async def _handle_auth_flow(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
) -> AsyncGenerator[dict[str, Any], None]:
    if message.strip().lower() == "back":
        state.service_context = None
        state.last_intent = None
        await clear_state(chat_id)
        yield WELCOME_OPTIONS
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return
    async for event in _handle_service_auth(db, user_id, message, chat_id, state):
        yield event


# ---------------------------------------------------------------------------
# Service param collection
# ---------------------------------------------------------------------------


async def _handle_service_param_input(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
) -> AsyncGenerator[dict[str, Any], None]:
    """Handle user input when the system is waiting for service parameters."""
    ctx = state.context
    text = message.strip()
    if text.lower() == "back":
        clear_service_context(ctx)
        state.last_intent = None
        yield WELCOME_OPTIONS
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return

    # Store the user's free-text response as service params for the active service
    if ctx.active_service:
        connector = get_connector(ctx.active_service)
        if connector:
            ctx.service_params["query"] = text
            # If programme/semester detected, store those too
            from app.orchestrator.extractor import extract_entities
            ent = extract_entities(text)
            if ent.programme:
                ctx.service_params["programme"] = ent.programme
            if ent.level:
                ctx.service_params["level"] = ent.level

    # Proceed to auth flow for the active service
    ctx.service_step = "auth_ready"
    if ctx.active_service:
        async for event in _route_service(db, user_id, text, chat_id, state, ctx.active_service):
            yield event
    else:
        yield WELCOME_OPTIONS
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}


# ---------------------------------------------------------------------------
# Apply confirmation & subject collection
# ---------------------------------------------------------------------------


async def _handle_confirm_input(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
) -> AsyncGenerator[dict[str, Any], None]:
    """Resolve a pending apply request: confirm, cancel, or re-prompt."""
    t = message.strip().lower().rstrip("!?., ")
    service = state.pending_service or state.service_context

    if t in _CONFIRM_YES and service:
        state.last_intent = None
        query = state.pending_query or message
        if state.pending_params:
            for k, v in state.pending_params.items():
                state.context.service_params.setdefault(k, v)
        state.pending_service = None
        state.pending_action = None
        state.pending_query = None
        state.pending_params = {}
        label = service.replace("_", " ").title()
        yield {"type": "token", "text": f"Got it — setting up your {label} request now."}
        if service in _SUBJECT_REQUIRED_SERVICES:
            state.last_intent = "awaiting_subject"
            yield {"type": "token", "text": "Which subject(s) should this request cover? List them separated by commas (e.g. \"Mathematics, Physics, English\")."}
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
            return
        async for event in _route_student_service(db, user_id, query, chat_id, state, service):
            yield event
        return

    if t in _CONFIRM_NO or (not service and t in _CONFIRM_YES):
        state.pending_service = None
        state.pending_action = None
        state.pending_query = None
        state.pending_params = {}
        state.last_intent = None
        yield {"type": "token", "text": "No problem — I've cancelled that request. Is there anything else I can help you with?"}
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return

    # Unrecognized reply — re-ask with the confirm chips
    yield {
        "type": "options",
        "title": "Confirm request",
        "message": "Please confirm or cancel the request.",
        "options": [
            {"id": "confirm_apply", "label": "Yes, submit it"},
            {"id": "cancel_apply", "label": "Cancel"},
        ],
    }
    yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}


async def _handle_subject_input(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
) -> AsyncGenerator[dict[str, Any], None]:
    """Collect subject names for a pending apply request."""
    subjects = _extract_subject_list(message)
    if not subjects:
        yield {"type": "token", "text": "I didn't catch the subject names. Please list them separated by commas (e.g. \"Mathematics, Physics, English\")."}
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return

    state.context.service_params["subjects"] = ", ".join(subjects)
    state.last_intent = None
    service = state.service_context or state.pending_service
    if not service:
        yield {"type": "token", "text": "I couldn't find the pending request — please start again."}
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return
    async for event in _route_student_service(db, user_id, message, chat_id, state, service):
        yield event


# ---------------------------------------------------------------------------
# Student service routing (context-aware)
# ---------------------------------------------------------------------------


async def _route_student_service(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
    service_name: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Route a student portal service request with context awareness.
    
    Authentication-first flow:
    1. Check if authenticated (global session or per-service)
    2. If NOT authenticated: save pending service, show auth form
    3. If authenticated: collect service-specific params, then fetch data
    """
    from app.utils.logging import log as _log
    ctx = state.context
    connector = get_connector(service_name)

    if connector is None:
        yield {
            "type": "error",
            "message": f"Service '{service_name}' is not available.",
        }
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return

    # Pre-populate service params from conversation context
    if ctx.programme and "programme" not in ctx.service_params:
        ctx.service_params["programme"] = ctx.programme
    if ctx.level and "level" not in ctx.service_params:
        ctx.service_params["level"] = ctx.level
    if ctx.topic and "topic" not in ctx.service_params:
        ctx.service_params["topic"] = ctx.topic

    # Check if service requires semester (semester-dependent services)
    requires_semester = service_name in {
        "results", "admit_card", "exam_form", "attendance",
        "registration", "semester_admission",
    }

    # Check if service requires subject (subject-dependent services)
    requires_subject = service_name in {"re_evaluation", "xerox_copy"}

    # If the service does not require auth, skip straight to fetch
    if not connector.requires_auth:
        async for event in _fetch_service_data(db, user_id, message, chat_id, state, service_name, connector, None):
            yield event
        return

    # --- AUTHENTICATION CHECK FIRST ---
    # If the student has a global session (authenticated for ANY service),
    # use it — skip per-service auth check entirely.
    if state.student_reg_no is not None:
        _log.info("Session Restored: service=%s student=%s", service_name, state.student_reg_no)
        auth = await get_auth_state(chat_id, service_name)
        session_token = auth.session_token or state.student_session_id
        async for event in _fetch_service_data(db, user_id, message, chat_id, state, service_name, connector, session_token):
            yield event
        return

    # If user is already authenticated for this specific service, skip to fetch
    auth = await get_auth_state(chat_id, service_name)
    if auth.status == "authenticated" and auth.session_token:
        async for event in _fetch_service_data(db, user_id, message, chat_id, state, service_name, connector, auth.session_token):
            yield event
        return

    # --- NOT AUTHENTICATED: Save pending service and start auth flow ---
    # Save the pending service request BEFORE showing auth form
    state.pending_service = service_name
    state.pending_action = "fetch"
    state.pending_query = message
    state.pending_params = dict(state.context.service_params)
    _log.info("Pending Service (awaiting auth): service=%s query=%s params=%s", service_name, message, state.pending_params)

    state.service_context = service_name
    state.last_intent = "service_pending_auth"
    await push_breadcrumb(
        chat_id,
        Breadcrumb(label=service_name.replace("_", " ").title(), type="service", context={"service": service_name}),
    )
    # Show authentication form with service-specific message
    yield {
        "type": "auth_form",
        "service": service_name,
        "title": "Student Login Required",
        "message": f"To access {service_name.replace('_', ' ').title()}, I first need to verify your student account. Please enter your CUS ID / Registration Number.",
        "fields": [
            {"id": "registration_number", "label": "CUS ID / Registration Number", "type": "text", "placeholder": "e.g. CUS-2023-0001"},
            {"id": "date_of_birth", "label": "Date of Birth", "type": "text", "placeholder": "e.g. 15/08/2002"},
        ],
        "submit_label": "Verify",
    }
    yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
    state.last_intent = "awaiting_credentials"
    return


async def _fetch_service_data(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
    service_name: str,
    connector: Any,
    session_token: str | None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Fetch data from a connector with context params and yield the result."""
    ctx = state.context

    # Build params from context + service params
    params = dict(ctx.service_params)
    if ctx.programme and "programme" not in params:
        params["programme"] = ctx.programme
    if ctx.level and "level" not in params:
        params["level"] = ctx.level
    if ctx.college_programme and "programme" not in params:
        params["programme"] = ctx.college_programme

    # Inject authenticated student info
    if state.student_name:
        params["student_name"] = state.student_name
    if state.student_reg_no:
        params["reg_no"] = state.student_reg_no
    if state.student_programme:
        params.setdefault("programme", state.student_programme)
    if state.student_semester and "semester" not in params:
        params["semester"] = str(state.current_semester or state.student_semester)

    result = await connector.fetch(session_token, params)

    if result.success:
        data = result.data
        fields = data.get("fields", [])
        actions = data.get("actions", [])
        ctx.service_step = "complete"
        yield {
            "type": "detail",
            "title": data.get("title", connector.display_name),
            "fields": fields,
            "actions": actions,
            "message": data.get("message", ""),
            "context": {
                "breadcrumbs": ["Student Services", connector.display_name],
                "service": service_name,
            },
        }
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        state.last_intent = "service_result"
        asyncio.ensure_future(collect_event(
            anon_session_id=_anon_session_id(chat_id),
            conversation_id=chat_id,
            planner_action="connector",
            response_source="connector",
            route_chosen="service_fetch",
            service_requested=service_name,
            conversation_completed=True,
            detected_programme=ctx.programme,
        ))
    else:
        ctx.service_step = "auth_needed"
        yield {
            "type": "error",
            "message": _sanitize_service_error(
                result.error,
                f"Could not retrieve {connector.display_name}. Please try again.",
            ),
        }
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}


# ---------------------------------------------------------------------------
# Service routing (existing — used by both old and new flows)
# ---------------------------------------------------------------------------


async def _route_service(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
    service_name: str,
) -> AsyncGenerator[dict[str, Any], None]:
    connector = get_connector(service_name)
    if connector is None:
        yield {"type": "error", "message": f"Service '{service_name}' is not available."}
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return
    if not connector.requires_auth:
        async for event in _handle_service_query(db, user_id, message, chat_id, state, service_name):
            yield event
        return
    # Check authentication first (same as _route_student_service)
    if state.student_reg_no is not None:
        auth = await get_auth_state(chat_id, service_name)
        session_token = auth.session_token or state.student_session_id
        async for event in _handle_service_query(db, user_id, message, chat_id, state, service_name):
            yield event
        return
    auth = await get_auth_state(chat_id, service_name)
    if auth.status == "authenticated" and auth.session_token:
        async for event in _handle_service_query(db, user_id, message, chat_id, state, service_name):
            yield event
        return

    # --- NOT AUTHENTICATED: Save pending service and start auth flow ---
    state.pending_service = service_name
    state.pending_action = "fetch"
    state.pending_query = message
    state.pending_params = dict(state.context.service_params)
    from app.utils.logging import log as _log
    _log.info("Pending Service (awaiting auth): service=%s query=%s params=%s", service_name, message, state.pending_params)

    state.service_context = service_name
    state.last_intent = "service_pending_auth"
    await push_breadcrumb(
        chat_id,
        Breadcrumb(label=service_name.replace("_", " ").title(), type="service", context={"service": service_name}),
    )
    yield {
        "type": "auth_form",
        "service": service_name,
        "title": "Student Login Required",
        "message": f"To access {service_name.replace('_', ' ').title()}, I first need to verify your student account. Please enter your CUS ID / Registration Number.",
        "fields": [
            {"id": "registration_number", "label": "CUS ID / Registration Number", "type": "text", "placeholder": "e.g. CUS-2023-0001"},
            {"id": "date_of_birth", "label": "Date of Birth", "type": "text", "placeholder": "e.g. 15/08/2002"},
        ],
        "submit_label": "Verify",
    }
    yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
    state.last_intent = "awaiting_credentials"
    return


# ---------------------------------------------------------------------------
# Service auth handler
# ---------------------------------------------------------------------------


async def _handle_service_auth(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
) -> AsyncGenerator[dict[str, Any], None]:
    from app.utils.logging import log as _log

    # Use pending_service as the source of truth (saved before auth form was shown)
    service = state.pending_service or state.service_context
    if not service:
        _log.warning("Authentication Success but no pending service — resorting to welcome")
        yield {"type": "error", "message": "No service context found. Please try again."}
        return

    connector = get_connector(service)
    if connector is None:
        yield {"type": "error", "message": f"Service '{service}' is not available."}
        return

    # --- Parse credentials from message ---
    # Support two formats:
    # 1) Pipe-delimited: "reg_no||password"  (backward-compatible with existing tests)
    # 2) Comma-separated: "reg_no, dd-mm-yyyy"  (new spec flow: reg_no + DOB)
    # 3) Labelled: "Registration No: x Password: y" or "ID: x Password: y"
    parts = message.split("||", 1)
    reg_no = parts[0].strip()
    password = parts[1].strip() if len(parts) > 1 else ""

    use_dob_auth = False
    dob = None

    # Check for comma-separated format (reg_no, dob)
    if not password and "," in message:
        comma_parts = [p.strip() for p in message.split(",", 1)]
        if len(comma_parts) == 2:
            reg_no = comma_parts[0].strip()
            dob = comma_parts[1].strip()
            use_dob_auth = True

    # Check for labelled format "Registration No: x Password: y" or "ID: x Password: y"
    if not password:
        from app.orchestrator.student_session import parse_credentials
        parsed = parse_credentials(message)
        if parsed:
            reg_no, password = parsed

    if not reg_no:
        yield {
            "type": "auth_form",
            "service": service,
            "title": "Invalid Input",
            "message": "Please provide your Registration Number.",
            "fields": [
                {"id": "registration_number", "label": "Registration Number", "type": "text", "placeholder": "e.g. CUS-2023-0001"},
            ],
            "submit_label": "Continue",
        }
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return

    # If using DOB auth and no password, validate against dob
    if use_dob_auth and not dob:
        yield {
            "type": "auth_form",
            "service": service,
            "title": "Invalid Input",
            "message": "Please provide both Registration Number and Date of Birth.",
            "fields": [
                {"id": "registration_number", "label": "Registration Number", "type": "text", "placeholder": "e.g. CUS-2023-0001"},
                {"id": "date_of_birth", "label": "Date of Birth", "type": "text", "placeholder": "e.g. 30-10-2001"},
            ],
            "submit_label": "Verify",
        }
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return

    auth = await get_auth_state(chat_id, service)
    auth.attempt_count += 1
    auth.status = "pending"

    # --- Student authentication ---
    from app.auth.security import verify_password
    from app.models import Student, StudentSession

    student = db.query(Student).filter(Student.reg_no == reg_no, Student.is_active == True).first()

    # Validate based on auth type
    auth_success = False
    if use_dob_auth and dob and student:
        # Validate DOB against student record
        from datetime import datetime
        try:
            dob_date = datetime.strptime(dob, "%d-%m-%Y").date()
            student_dob = student.dob if isinstance(student.dob, datetime) else datetime.strptime(str(student.dob), "%d-%m-%Y").date() if student.dob else None
            if student_dob and dob_date == student_dob:
                auth_success = True
        except ValueError:
            pass
    
    if not auth_success and not use_dob_auth and student:
        # Fall back to password validation
        if verify_password(password or "", student.hashed_password):
            auth_success = True
    
    if not auth_success:
        auth.status = "failed"
        auth.last_error = "Invalid registration number or password."
        state.last_intent = "awaiting_credentials"
        _log.info("Authentication Failed: reg_no=%s", reg_no)
        yield {
            "type": "auth_form",
            "service": service,
            "title": "Authentication Failed",
            "message": "Your registration number or password appears to be incorrect. Please try again.",
            "fields": [
                {"id": "registration_number", "label": "Registration Number", "type": "text"},
                {"id": "password", "label": "Password", "type": "password"},
            ],
            "submit_label": "Try Again",
        }
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return
    else:
        if use_dob_auth:
            _log.info("DOB Authentication Success: reg_no=%s", reg_no)
        else:
            _log.info("Password Authentication Success: reg_no=%s", reg_no)

    # Revoke any existing active sessions for this student first
    from datetime import datetime, timezone
    for old_session in db.query(StudentSession).filter(
        StudentSession.student_id == student.id,
        StudentSession.revoked == False,
    ):
        old_session.revoked = True

    # Create persistent session token in the DB
    import uuid
    from datetime import timedelta

    raw_token = uuid.uuid4().hex + uuid.uuid4().hex
    session_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
    db_session = StudentSession(
        student_id=student.id,
        token=raw_token,
        expires_at=session_expiry,
    )
    db.add(db_session)
    db.commit()

    # Store student identity in conversation state for the session duration
    state.student_reg_no = student.reg_no
    state.student_name = student.name
    state.student_programme = student.programme
    state.student_semester = student.current_semester
    state.student_session_id = str(db_session.id)

    auth.status = "authenticated"
    auth.session_token = raw_token
    auth.session_expiry = session_expiry.timestamp()
    state.last_intent = "service_authenticated"

    _log.info("Authentication Success: reg_no=%s name=%s session=%s", reg_no, student.name, str(db_session.id)[:8])
    _log.info("Student Loaded: name=%s programme=%s semester=%s", student.name, student.programme, student.current_semester)
    _log.info("Session Created: id=%s token=%s expiry=%s", str(db_session.id)[:8], raw_token[:8], session_expiry.isoformat())

    # --- Resume pending request ---
    pending_service = state.pending_service
    pending_query = state.pending_query
    # Restore params that were saved before auth form was shown
    if state.pending_params:
        for k, v in state.pending_params.items():
            state.context.service_params.setdefault(k, v)

    _log.info("Executing Pending Service: service=%s query=%s params=%s",
              pending_service, pending_query, state.context.service_params)

    # Clear pending fields so they can't be accidentally re-used
    state.pending_service = None
    state.pending_action = None
    state.pending_query = None
    state.pending_params = {}

    # Execute the pending service request
    async for event in _handle_service_query(db, user_id, pending_query or message, chat_id, state, pending_service or service):
        yield event

    # Revoke any existing active sessions for this student first
    from datetime import datetime, timezone
    for old_session in db.query(StudentSession).filter(
        StudentSession.student_id == student.id,
        StudentSession.revoked == False,
    ):
        old_session.revoked = True

    # Create persistent session token in the DB
    import uuid
    from datetime import timedelta

    raw_token = uuid.uuid4().hex + uuid.uuid4().hex
    session_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
    db_session = StudentSession(
        student_id=student.id,
        token=raw_token,
        expires_at=session_expiry,
    )
    db.add(db_session)
    db.commit()

    # Store student identity in conversation state for the session duration
    state.student_reg_no = student.reg_no
    state.student_name = student.name
    state.student_programme = student.programme
    state.student_semester = student.current_semester
    state.student_session_id = str(db_session.id)

    auth.status = "authenticated"
    auth.session_token = raw_token
    auth.session_expiry = session_expiry.timestamp()
    state.last_intent = "service_authenticated"

    _log.info("Authentication Success: reg_no=%s name=%s session=%s", reg_no, student.name, str(db_session.id)[:8])
    _log.info("Student Loaded: name=%s programme=%s semester=%s", student.name, student.programme, student.current_semester)
    _log.info("Session Created: id=%s token=%s expiry=%s", str(db_session.id)[:8], raw_token[:8], session_expiry.isoformat())

    # --- Resume pending request ---
    pending_service = state.pending_service
    pending_query = state.pending_query
    # Restore params that were saved before auth form was shown
    if state.pending_params:
        for k, v in state.pending_params.items():
            state.context.service_params.setdefault(k, v)

    _log.info("Executing Pending Service: service=%s query=%s params=%s",
              pending_service, pending_query, state.context.service_params)

    # Clear pending fields so they can't be accidentally re-used
    state.pending_service = None
    state.pending_action = None
    state.pending_query = None
    state.pending_params = {}

    # Execute the pending service request
    async for event in _handle_service_query(db, user_id, pending_query or message, chat_id, state, pending_service or service):
        yield event

    _log.info("Response Sent: service=%s", pending_service or service)


# ---------------------------------------------------------------------------
# Service query handler
# ---------------------------------------------------------------------------


async def _handle_service_query(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
    service_name: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Handle service query after authentication. Collects required parameters (semester, subject) if missing."""
    connector = get_connector(service_name)
    if connector is None:
        yield {"type": "error", "message": f"Service '{service_name}' is not available."}
        return

    auth = await get_auth_state(chat_id, service_name)
    session_token = auth.session_token or state.student_session_id

    # Include context params (programme, semester, etc.) so connectors
    # can return personalized responses without asking again.
    ctx = state.context
    params = dict(ctx.service_params)
    if ctx.programme and "programme" not in params:
        params["programme"] = ctx.programme
    if ctx.level and "level" not in params:
        params["level"] = ctx.level

    # Inject authenticated student info into params so connectors return
    # real data instead of placeholders.
    if state.student_name:
        params["student_name"] = state.student_name
    if state.student_reg_no:
        params["reg_no"] = state.student_reg_no
    if state.student_programme:
        params.setdefault("programme", state.student_programme)
    if state.student_semester and "semester" not in params:
        params["semester"] = str(state.current_semester or state.student_semester)

    # --- Service-specific parameter collection AFTER authentication ---
    # Check if service requires semester (semester-dependent services)
    requires_semester = service_name in {
        "results", "admit_card", "exam_form", "attendance",
        "registration", "semester_admission",
    }

    # Check if service requires subject (subject-dependent services)
    requires_subject = service_name in {"re_evaluation", "xerox_copy"}

    # For semester-dependent services: check if semester already known
    if requires_semester and not params.get("semester"):
        # Check if semester is mentioned in the current message
        import re
        sem_match = re.search(r"(semester|sem)\s*[:]?\s*(\d+)", message, re.IGNORECASE)
        if sem_match:
            params["semester"] = sem_match.group(2)
            state.current_semester = int(sem_match.group(2))
            state.context.service_params["semester"] = sem_match.group(2)
        elif state.current_semester:
            # Use remembered semester from previous authentication
            params["semester"] = str(state.current_semester)
            state.context.service_params["semester"] = str(state.current_semester)
        else:
            # Ask for semester
            yield {
                "type": "token",
                "text": f"Your account has been verified successfully. Which semester's {service_name.replace('_', ' ').title()} would you like to check?",
            }
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
            return

    # For subject-dependent services: check if subject already known
    if requires_subject and not params.get("subject"):
        # Check if subject is mentioned in the current message
        from app.orchestrator.extractor import extract_entities
        ent = extract_entities(message)
        if ent.programme and not params.get("programme"):
            params["programme"] = ent.programme
        # If user mentions a subject in their message
        subject_lower = message.lower()
        for subject_phrase in ["mathematics", "physics", "chemistry", "dbms", "english", "computer science"]:
            if subject_phrase in subject_lower:
                # Extract just the subject name
                params["subject"] = subject_phrase.title()
                state.context.service_params["subject"] = subject_phrase.title()
                break
        if not params.get("subject"):
            # Ask for subject
            yield {
                "type": "token",
                "text": f"Your account has been verified. To complete your {service_name.replace('_', ' ').title()} request, which subject would you like to apply for?",
            }
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
            return

    # All required parameters collected - fetch the data
    result = await connector.fetch(session_token, params)
    if result.success:
        data = result.data
        fields = data.get("fields", [])
        actions = data.get("actions", [])
        from app.utils.logging import log as _log
        _log.info("Service Query Result: service=%s fields=%d actions=%d", service_name, len(fields), len(actions))
        yield {
            "type": "detail",
            "title": data.get("title", service_name.replace("_", " ").title()),
            "fields": fields,
            "actions": actions,
            "message": data.get("message", ""),
            "context": {
                "breadcrumbs": ["Student Services", service_name.replace("_", " ").title()],
                "service": service_name,
            },
        }
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        state.last_intent = "service_result"
    else:
        err = (result.error or "").lower()
        if "session expired" in err or "unauthorized" in err:
            auth.status = "none"
            yield {
                "type": "auth_form",
                "service": service_name,
                "title": "Session Expired",
                "message": "Your session has expired. Please sign in again.",
                "fields": [
                    {"id": "registration_number", "label": "Registration Number", "type": "text"},
                    {"id": "password", "label": "Password", "type": "password"},
                ],
                "submit_label": "Sign In",
            }
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
            state.last_intent = "awaiting_credentials"
        else:
            yield {
                "type": "error",
                "message": _sanitize_service_error(
                    result.error,
                    f"Could not retrieve {service_name}. Please try again later.",
                ),
            }


# ---------------------------------------------------------------------------
# Authority routing
# ---------------------------------------------------------------------------


async def _handle_authority_route(
    db: Session,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
    plan_result: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    """Handle authority / escalation requests from the planner.

    Yields a contact card or multiple choices as SSE events.
    """
    authorities = (plan_result.extra or {}).get("authorities", [])
    if not authorities:
        from app.authority.matcher import find_authority
        authorities = find_authority(message, top_k=3)

    if not authorities:
        yield {
            "type": "options",
            "title": "Contact University Office",
            "message": "I couldn't find a specific office for your query. Please select a department below or describe your issue in more detail.",
            "options": [
                {"id": "admissions", "label": "Admissions Office"},
                {"id": "examinations", "label": "Controller of Examinations"},
                {"id": "academic", "label": "Academic Section"},
                {"id": "helpdesk", "label": "Student Help Desk"},
                {"id": "it", "label": "IT Cell"},
                {"id": "general", "label": "General Enquiry"},
            ],
        }
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        state.last_intent = "authority_selection"
        return

    if len(authorities) == 1:
        auth = authorities[0]
        yield _build_authority_card(auth)
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        state.last_intent = "authority_contact"
        return

    # Multiple matches — let the user choose
    yield {
        "type": "options",
        "title": "Which office are you looking for?",
        "message": f"I found {len(authorities)} relevant offices. Please select one:",
        "options": [
            {
                "id": a.get("id", ""),
                "label": f"{a.get('authority_name', '')} ({a.get('department_name', '')})",
                "description": a.get("designation") or a.get("description", "")[:80],
            }
            for a in authorities
        ],
    }
    yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
    state.last_intent = "authority_selection"


def _build_authority_card(authority: dict[str, Any]) -> dict[str, Any]:
    """Format an authority record as a structured contact card."""
    from app.authority.matcher import format_contact_card
    return {
        "type": "detail",
        "title": authority.get("authority_name", "University Office"),
        "message": authority.get("description", ""),
        "fields": [
            {"label": "Department", "value": authority.get("department_name", "")},
            {"label": "Officer", "value": authority.get("designation") or authority.get("authority_name", "")},
            {"label": "Phone", "value": authority.get("phone", "")},
            {"label": "Email", "value": authority.get("email", "")},
            {"label": "Office Timing", "value": authority.get("office_timings") or ""},
            {"label": "Working Days", "value": authority.get("working_days") or ""},
            {"label": "Address", "value": authority.get("office_address") or ""},
        ],
        "actions": [
            {"id": f"call_{authority.get('id', '')}", "label": f"Call {authority.get('phone', '')}", "type": "phone"},
            {"id": f"email_{authority.get('id', '')}", "label": f"Email {authority.get('email', '')}", "type": "email"},
            {"id": f"map_{authority.get('id', '')}", "label": "View on Map", "type": "map", "url": authority.get("office_location")},
            {"id": f"website_{authority.get('id', '')}", "label": "Visit Website", "type": "url", "url": authority.get("website")},
        ],
        "extra": format_contact_card(authority),
    }

