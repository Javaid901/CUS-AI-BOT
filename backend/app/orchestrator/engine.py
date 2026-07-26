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
from app.services.registry import get_connector

# ---------------------------------------------------------------------------
# Service keyword detection (source: extractor.py)
# ---------------------------------------------------------------------------


def _detect_service_intent(message: str) -> str | None:
    text = message.strip().lower()
    for phrase in SERVICE_PATTERNS:
        if phrase in text:
            return SERVICE_KEYWORDS[phrase]
    return None


def _anon_session_id(chat_id: str) -> str:
    """Derive a stable anonymous session ID from a chat_id (SHA1 prefix)."""
    return hashlib.sha1(chat_id.encode()).hexdigest()[:16]


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
            if state.last_intent == "service_result" and state.service_context:
                sem_match = text.strip().lower()
                if sem_match.startswith("sem") and len(sem_match) > 3 and sem_match[3:].isdigit():
                    ctx.service_params["semester"] = sem_match[3:]
                    async for event in _route_student_service(db, user_id, text, chat_id, state, state.service_context):
                        yield event
                    return
            service_name = _detect_service_intent(text)

        # ----- Stage 3: Service intent -----
        if service_name:
            with stage_timer("service_routing"):
                # Set service context before routing
                set_active_service(state.context, service_name)
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
        async for event in run_chat(db, user_id, query, chat_id):
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

    # Fallback
    state.last_intent = "knowledge"
    async for event in run_chat(db, user_id, message, chat_id):
        yield event


# ---------------------------------------------------------------------------
# Context update helpers
# ---------------------------------------------------------------------------


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
    
    If the conversation context already contains programme/semester info,
    it is passed to the connector for personalized responses.
    Falls back to the existing service routing for auth + fetch.
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

    # If the service does not require auth, skip straight to fetch
    if not connector.requires_auth:
        async for event in _fetch_service_data(db, user_id, message, chat_id, state, service_name, connector, None):
            yield event
        return

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

    # Proceed with standard auth flow
    async for event in _route_service(db, user_id, message, chat_id, state, service_name):
        yield event

    # Record service usage analytics (fire-and-forget)
    anon_session = _anon_session_id(chat_id)
    asyncio.ensure_future(collect_event(
        anon_session_id=anon_session,
        conversation_id=chat_id,
        planner_action="connector",
        response_source="connector",
        route_chosen="service",
        service_requested=service_name,
        detected_programme=ctx.programme,
        detected_college=ctx.college,
        detected_service=service_name,
        query_original=ctx.query_original,
        query_corrected=ctx.query_corrected,
    ))


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
        params["semester"] = str(state.student_semester)

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
            "message": result.error or f"Could not retrieve {connector.display_name}. Please try again.",
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
    if await service_needs_auth(chat_id, service_name):
        # --- Save pending request before showing auth form ---
        state.pending_service = service_name
        state.pending_action = "fetch"
        state.pending_query = message
        state.pending_params = dict(state.context.service_params)
        from app.utils.logging import log as _log
        _log.info("Pending Service: service=%s query=%s params=%s", service_name, message, state.pending_params)

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
            "message": f"To access {service_name.replace('_', ' ').title()}, please sign in with your university credentials.",
            "fields": [
                {"id": "registration_number", "label": "Registration Number", "type": "text", "placeholder": "e.g. CUS-2023-0001"},
                {"id": "password", "label": "Password", "type": "password", "placeholder": "Enter your password"},
            ],
            "submit_label": "Sign In",
        }
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        state.last_intent = "awaiting_credentials"
        return

    async for event in _handle_service_query(db, user_id, message, chat_id, state, service_name):
        yield event


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

    parts = message.split("||", 1)
    reg_no = parts[0].strip()
    password = parts[1].strip() if len(parts) > 1 else ""

    if not reg_no or not password:
        yield {
            "type": "auth_form",
            "service": service,
            "title": "Invalid Input",
            "message": "Please enter both Registration Number and Password.",
            "fields": [
                {"id": "registration_number", "label": "Registration Number", "type": "text"},
                {"id": "password", "label": "Password", "type": "password"},
            ],
            "submit_label": "Sign In",
        }
        yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        return

    auth = await get_auth_state(chat_id, service)
    auth.attempt_count += 1
    auth.status = "pending"

    # --- Real student auth: validate against Student table + bcrypt ---
    from app.auth.security import verify_password
    from app.models import Student, StudentSession

    student = db.query(Student).filter(Student.reg_no == reg_no, Student.is_active == True).first()
    if student is None or not verify_password(password, student.hashed_password):
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
        params["semester"] = str(state.student_semester)

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
                "message": result.error or f"Could not retrieve {service_name}. Please try again later.",
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
