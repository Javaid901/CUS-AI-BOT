"""
backend/app/catalogue/backend.py

Catalogue request handlers used by the AI Orchestrator.

`handle_catalogue()` turns a catalogue "request" dict (produced by detect.py)
into the SSE event list the engine forwards. `continue_pending()` resumes a
picker flow using the value the user selected in the follow-up message.

Scheme hierarchy flow (op chain):
  schemes -> levels -> list -> menu -> <detail op>
Continuation contract (option ids produced by responses.py):
  - scheme options      -> scheme UUID id    (selector catalogue_scheme)
  - level options       -> "level:ug"        (selector catalogue_level)
  - programme options   -> programme UUID id (selector catalogue_programme)
  - menu options        -> "menu:<op>"       (selector catalogue_menu)
  - semester options    -> "semester:2"      (selector catalogue_semester)
  - minor options       -> minor UUID id     (selector catalogue_minor)
Any message may also resolve to a programme / scheme by name, alias or code.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.orchestrator.context import ConversationContext
from app.orchestrator.state import ConversationState

from app.catalogue import responses as rsp
from app.catalogue import service

_log = logging.getLogger("cus")


# ---------------------------------------------------------------------------
# Single-shot request handler
# ---------------------------------------------------------------------------


async def handle_catalogue(
    db,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
    request: dict[str, Any],
) -> list[dict[str, Any]]:
    """Execute a catalogue request dict; returns the event list to yield."""
    ctx = state.context
    op = request.get("op") or "list"

    handler = _OP_HANDLERS.get(op)
    if handler is None:
        return _fallback_events(chat_id)

    payload = handler(ctx, request)
    if payload is None:
        return _fallback_events(chat_id)

    _apply_catalogue_context(ctx, request)
    _add_context(payload, ctx)

    state.last_intent = "navigation"
    events: list[dict[str, Any]] = [payload, {"type": "done", "chat_id": chat_id, "cited_chunks": []}]

    _remember_pending(state, op, request)
    return events


async def continue_pending(
    db,
    user_id: str,
    message: str,
    chat_id: str,
    state: ConversationState,
) -> list[dict[str, Any]] | None:
    """Resume a catalogue picker using the user's selected option.

    Returns the events to yield, or None when the message cannot be resolved
    to a menu choice (the pending picker is preserved so the user can retry).
    """
    pending = state.catalogue_pending
    if not pending:
        return None
    text = message.strip().lower()

    # Exit words leave the picker and fall through to the normal pipeline.
    if text in ("back", "cancel", "stop", "exit", "reset"):
        state.catalogue_pending = None
        return None

    if pending.get("op") == "scheme_choice":
        return await _continue_scheme(db, user_id, message, chat_id, state, pending, text)

    # ---- Scheme choice -> level picker ----
    if pending.get("op") == "schemes_choice":
        scheme = service.resolve_academic_scheme(text)
        if not scheme:
            return None
        return await handle_catalogue(
            db, user_id, message, chat_id, state,
            {"op": "levels", "scheme": scheme["id"], "scheme_name": scheme.get("name")},
        )

    # ---- Level choice -> programme list for the chosen scheme ----
    if pending.get("op") == "levels_choice":
        level = _parse_level(text)
        if level is None:
            return None
        return await handle_catalogue(
            db, user_id, message, chat_id, state,
            {
                "op": "list",
                "scheme": pending.get("scheme"),
                "scheme_name": pending.get("scheme_name"),
                "level": level,
            },
        )

    # ---- Programme choice -> programme menu ----
    if pending.get("op") == "programmes_choice":
        resolved = service.resolve_programme(text)
        if not resolved:
            return None
        return await handle_catalogue(
            db, user_id, message, chat_id, state,
            {"op": "menu", "programme": resolved["id"], "code": resolved.get("code"), "name": resolved.get("name")},
        )

    # ---- Menu selection -> detail op for the stored programme ----
    if pending.get("op") == "menu":
        return await _continue_menu(db, user_id, message, chat_id, state, pending, text)

    # ---- Programme-first flows ("which programme?") ----
    if not pending.get("programme"):
        resolved = service.resolve_programme(text)
        if not resolved:
            return None
        request: dict[str, Any] = {
            "op": pending.get("op") or "overview",
            "programme": resolved["id"],
            "code": resolved.get("code"),
            "name": resolved.get("name"),
        }
        if pending.get("category"):
            request["category"] = pending["category"]
        if pending.get("params"):
            request.update(pending["params"])
        return await handle_catalogue(db, user_id, message, chat_id, state, request)

    prog_id = pending["programme"]

    # ---- Semester flows ----
    if pending.get("op") == "semester_subjects":
        semester = _extract_semester(text)
        if semester is None:
            return None
        return await handle_catalogue(
            db, user_id, message, chat_id, state,
            {"op": "semester_subjects", "programme": prog_id, "semester": semester},
        )

    # ---- Minor flows ----
    if pending.get("op") == "minor_subjects":
        minor_name = service.resolve_minor_name(prog_id, text)
        if not minor_name:
            return None
        return await handle_catalogue(
            db, user_id, message, chat_id, state,
            {"op": "minor_subjects", "programme": prog_id, "minor": minor_name},
        )

    # ---- Curriculum document flows ----
    if pending.get("op") == "curriculum_doc":
        doc = service.find_curriculum_document(prog_id, text)
        if not doc:
            return None
        return await handle_catalogue(
            db, user_id, message, chat_id, state,
            {"op": "curriculum_doc", "programme": prog_id, "document": doc},
        )

    # ---- Fallback: re-run the op for the stored programme ----
    return None


async def _continue_scheme(db, user_id, message, chat_id, state, pending, text: str) -> list[dict[str, Any]] | None:
    """Resolve a scheme-hub option id ("scheme:list", "scheme:major", ...)."""
    scheme = pending.get("scheme")
    scheme_name = pending.get("scheme_name")
    choice = None
    if text.startswith("scheme:"):
        choice = text.split("scheme:", 1)[1].strip().split()[0]

    direct_ops = {"vac", "sec", "aec"}
    if choice in direct_ops:
        return await handle_catalogue(
            db, user_id, message, chat_id, state,
            {"op": choice},
        )
    if choice == "list":
        return await handle_catalogue(
            db, user_id, message, chat_id, state,
            {"op": "list", "scheme": scheme, "scheme_name": scheme_name, "level": None},
        )
    if choice in ("major", "minor", "semesters", "credits", "curriculum", "outcomes"):
        op_map = {
            "major": {"op": "subjects", "category": "major"},
            "minor": {"op": "minors"},
            "semesters": {"op": "semesters"},
            "credits": {"op": "credits"},
            "curriculum": {"op": "curriculum"},
            "outcomes": {"op": "outcomes"},
        }
        return await handle_catalogue(
            db, user_id, message, chat_id, state,
            {"op": "programme_pick", "pending": op_map[choice]},
        )

    # Natural language from within the hub: resolve a programme by name/code.
    resolved = service.resolve_programme(text)
    if resolved:
        return await handle_catalogue(
            db, user_id, message, chat_id, state,
            {"op": "menu", "programme": resolved["id"], "code": resolved.get("code"), "name": resolved.get("name")},
        )
    return None


async def _continue_menu(db, user_id, message, chat_id, state, pending, text: str) -> list[dict[str, Any]] | None:
    """Resolve a programme-menu choice to a detail op for the stored programme."""
    prog_id = pending.get("programme")
    if not prog_id:
        return None

    # 1) Typed option id ("menu:overview", "menu:semesters", ...)
    if text.startswith("menu:"):
        op = text.split("menu:", 1)[1].strip().split()[0]
        allowed = {"overview", "eligibility", "fee", "semesters", "subjects",
                   "minors", "vac", "sec", "aec", "outcomes", "curriculum"}
        if op not in allowed:
            return None
        request: dict[str, Any] = {"op": op, "programme": prog_id}
        if op in ("vac", "sec", "aec"):
            request["category"] = op
        if op == "subjects":
            request["category"] = "major"
        return await handle_catalogue(db, user_id, message, chat_id, state, request)

    # 2) Programme switch from within a menu
    resolved = service.resolve_programme(text)
    if resolved and resolved["id"] != prog_id:
        return await handle_catalogue(
            db, user_id, message, chat_id, state,
            {"op": "menu", "programme": resolved["id"], "code": resolved.get("code"), "name": resolved.get("name")},
        )

    # 3) Natural language ("fee structure", "semester subjects", "eligibility")
    try:
        from app.catalogue import detect
        req = detect.detect_catalogue_request(text, state.context, None)
    except Exception:
        req = None
    if req and req.get("op"):
        op = req["op"]
        if op in ("subjects", "semesters", "semester_subjects", "minors", "credits",
                  "outcomes", "curriculum", "fee", "eligibility", "vac", "sec", "aec", "overview"):
            request = {"op": op, "programme": prog_id}
            for key in ("semester", "category", "minor"):
                if req.get(key) is not None:
                    request[key] = req[key]
            return await handle_catalogue(db, user_id, message, chat_id, state, request)
    return None


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------


def _apply_catalogue_context(ctx: ConversationContext, request: dict[str, Any]) -> None:
    """Persist catalogue programme / scheme / level / minor / semester / category."""
    if request.get("programme"):
        ctx.catalogue_programme_id = request["programme"]
    if request.get("code"):
        ctx.catalogue_programme_code = request["code"]
        ctx.programme = request["code"].lower()
        ctx.programme_id = request["code"].lower()
        ctx.domain = "academic"
    if request.get("name") and not ctx.catalogue_programme_code:
        ctx.catalogue_programme_code = request["name"]
    if request.get("scheme"):
        ctx.catalogue_scheme = request["scheme"]
    if request.get("scheme_name"):
        ctx.catalogue_scheme_name = request["scheme_name"]
    if request.get("scheme_code"):
        ctx.catalogue_scheme_code = request["scheme_code"]
    if request.get("level"):
        ctx.catalogue_level = request["level"]
        ctx.level = request["level"]
    if request.get("category"):
        ctx.catalogue_category = request["category"]
    if request.get("semester") is not None:
        ctx.catalogue_semester = int(request["semester"])
    if request.get("minor"):
        ctx.catalogue_minor = request["minor"]


def _add_context(payload: dict[str, Any], ctx: ConversationContext) -> None:
    try:
        from app.orchestrator.engine import _add_context_to_response
        _add_context_to_response(payload, ctx)
    except Exception:
        pass


def _remember_pending(state: ConversationState, op: str, request: dict[str, Any]) -> None:
    """Set the picker continuation for flows that need a follow-up choice."""
    if op == "scheme":
        state.catalogue_pending = {
            "op": "scheme_choice",
            "scheme": request.get("scheme"),
            "scheme_name": request.get("scheme_name"),
            "scheme_code": request.get("scheme_code"),
        }
        return
    if op == "schemes":
        state.catalogue_pending = {
            "op": "schemes_choice",
            "level": request.get("level"),
        }
        return
    if op == "levels":
        state.catalogue_pending = {
            "op": "levels_choice",
            "scheme": request.get("scheme"),
            "scheme_name": request.get("scheme_name"),
        }
        return
    if op == "list":
        state.catalogue_pending = {
            "op": "programmes_choice",
            "scheme": request.get("scheme"),
            "scheme_name": request.get("scheme_name"),
            "level": request.get("level"),
        }
        return
    if op == "menu":
        state.catalogue_pending = {
            "op": "menu",
            "programme": request.get("programme"),
        }
        return
    if op == "semesters":
        state.catalogue_pending = {
            "op": "semester_subjects",
            "programme": request.get("programme"),
        }
        return
    if op == "minors":
        state.catalogue_pending = {
            "op": "minor_subjects",
            "programme": request.get("programme"),
        }
        return
    if op == "programme_pick":
        inner = request.get("pending") or {}
        params = dict(request.get("params") or {})
        params.setdefault("op", inner.get("op"))
        if inner.get("category"):
            params.setdefault("category", inner["category"])
        if inner.get("semester") is not None:
            params.setdefault("semester", inner["semester"])
        state.catalogue_pending = {
            "op": inner.get("op") or params.get("op") or "overview",
            "programme": None,
            "category": request.get("category"),
            "params": params,
        }
        return
    # Single-shot ops clear any previous continuation
    state.catalogue_pending = None


# ---------------------------------------------------------------------------
# Op builders
# ---------------------------------------------------------------------------


def _schemes_payload(ctx: ConversationContext, request: dict[str, Any]):
    schemes = service.list_academic_schemes()
    if not schemes:
        return None
    return rsp.scheme_options_response(schemes, request.get("level"))


def _scheme_payload(ctx: ConversationContext, request: dict[str, Any]):
    """Scheme hub: overview + DB-driven exploration options for a scheme."""
    scheme_id = request.get("scheme")
    if not scheme_id:
        return None
    scheme = service.academic_scheme_by_id(scheme_id)
    if not scheme:
        return None
    programmes = service.list_programmes(scheme=scheme_id)
    if not programmes:
        return None
    levels = sorted({(p.get("level") or "") for p in programmes} - {""})
    counts: dict[str, int] = {}
    for p in programmes:
        lvl = p.get("level") or ""
        counts[lvl] = counts.get(lvl, 0) + 1
    return rsp.scheme_hub_response(scheme, levels, counts)


def _levels_payload(ctx: ConversationContext, request: dict[str, Any]):
    scheme = request.get("scheme")
    if not scheme:
        return None
    programmes = service.list_programmes(scheme=scheme)
    if not programmes:
        return None
    levels = sorted({(p.get("level") or "") for p in programmes} - {""})
    scheme_name = request.get("scheme_name")
    if not scheme_name:
        resolved = service.academic_scheme_by_id(scheme)
        scheme_name = resolved.get("name") if resolved else None
    if len(levels) == 1:
        # Only one level under the scheme — skip the level picker (data-driven).
        only = levels[0]
        filtered = [p for p in programmes if (p.get("level") or "") == only]
        return rsp.programme_list_response(filtered, only, scheme_name)
    return rsp.level_options_response(scheme_name, levels)


def _list_payload(ctx: ConversationContext, request: dict[str, Any]):
    programmes = service.list_programmes(level=request.get("level"), scheme=request.get("scheme"))
    if not programmes:
        return None
    return rsp.programme_list_response(programmes, request.get("level"), request.get("scheme_name"))


def _menu_payload(ctx: ConversationContext, request: dict[str, Any]):
    prog = service.get_programme(request.get("programme"))
    if not prog:
        return None
    items = _menu_items(prog)
    if not items:
        return None
    scheme_name = request.get("scheme_name")
    if not scheme_name and prog.get("scheme_name"):
        scheme_name = prog.get("scheme_name")
    return rsp.programme_menu_response(prog, items, scheme_name)


def _overview_payload(ctx: ConversationContext, request: dict[str, Any]):
    prog = service.get_programme(request.get("programme"))
    if not prog:
        return None
    return rsp.overview_response(prog)


def _eligibility_payload(ctx: ConversationContext, request: dict[str, Any]):
    prog = service.get_programme(request.get("programme"))
    if not prog or not prog.get("eligibility"):
        return None
    return rsp.eligibility_response(prog)


def _fee_payload(ctx: ConversationContext, request: dict[str, Any]):
    prog = service.get_programme(request.get("programme"))
    if not prog or not prog.get("fee_structure"):
        return None
    return rsp.fee_response(prog)


def _requested_payload(ctx: ConversationContext, request: dict[str, Any]):
    """Field-level answer for granular questions (compound / duration /
    documents / scheme / ...). Presents every resolved field on one card and
    flags fields with no published value for the engine's RAG cascade."""
    from app.catalogue.knowledge import resolve_information_request
    from app.catalogue.responses import requested_response

    prog = service.get_programme(request.get("programme"))
    if not prog:
        return None
    fields = request.get("fields") or []
    found, missing = resolve_information_request(None, prog, fields)
    request["missing_fields"] = missing
    return rsp.requested_response(prog, fields, found, missing)


def _subjects_payload(ctx: ConversationContext, request: dict[str, Any]):
    prog = service.get_programme(request.get("programme"))
    if not prog:
        return None
    category = request.get("category")
    semester = request.get("semester")
    if category:
        subjects = service.get_major_subjects(request["programme"], semester=semester) if category == "major" \
            else service.get_category_subjects(category, programme_id=request["programme"], semester=semester)
    else:
        subjects = service.get_subjects(programme_id=request["programme"], semester=semester)
    prog_name = prog.get("name") or prog.get("code") or ""
    return rsp.subject_category_response(prog_name, category or "generic", subjects)


def _semesters_payload(ctx: ConversationContext, request: dict[str, Any]):
    prog = service.get_programme(request.get("programme"))
    if not prog:
        return None
    sems = service.get_semesters(request["programme"])
    if not sems:
        return None
    return rsp.semester_options_response(prog.get("name") or prog.get("code") or "", sems)


def _semester_subjects_payload(ctx: ConversationContext, request: dict[str, Any]):
    prog = service.get_programme(request.get("programme"))
    semester = request.get("semester")
    if not prog or semester is None:
        return None
    subjects = service.get_semester_subjects(request["programme"], semester=int(semester), category=request.get("category"))
    return rsp.semester_subjects_response(prog.get("name") or prog.get("code") or "", int(semester), subjects)


def _minors_payload(ctx: ConversationContext, request: dict[str, Any]):
    prog = service.get_programme(request.get("programme"))
    if not prog:
        return None
    minors = service.get_minor_disciplines(request["programme"])
    if not minors:
        return None
    return rsp.minors_response(prog.get("name") or prog.get("code") or "", minors)


def _minor_subjects_payload(ctx: ConversationContext, request: dict[str, Any]):
    minor = request.get("minor")
    prog_id = request.get("programme")
    if not minor or not prog_id:
        return None
    subjects = service.get_minor_subjects(prog_id, minor=minor)
    if not subjects:
        return None
    return rsp.minor_subjects_response(minor, subjects)


def _category_payload(ctx: ConversationContext, request: dict[str, Any]):
    category = request.get("op")
    if category not in ("vac", "sec", "aec"):
        return None
    prog_id = request.get("programme")
    subjects = service.get_category_subjects(category, programme_id=prog_id)
    if not subjects:
        subjects = service.get_category_subjects(category)
    if not subjects:
        return None
    prog_name = None
    if prog_id:
        prog = service.get_programme(prog_id)
        if prog:
            prog_name = prog.get("name") or prog.get("code")
    return rsp.subject_category_response(prog_name, category, subjects)


def _credits_payload(ctx: ConversationContext, request: dict[str, Any]):
    prog = service.get_programme(request.get("programme"))
    if not prog:
        return None
    subjects = service.get_subjects(programme_id=request["programme"])
    return rsp.credits_response(prog.get("name") or prog.get("code") or "", prog.get("total_credits"), subjects)


def _outcomes_payload(ctx: ConversationContext, request: dict[str, Any]):
    prog = service.get_programme(request.get("programme"))
    if not prog:
        return None
    outcomes = service.get_learning_outcomes(request["programme"])
    return rsp.outcomes_response(prog.get("name") or prog.get("code") or "", outcomes)


def _curriculum_payload(ctx: ConversationContext, request: dict[str, Any]):
    prog = service.get_programme(request.get("programme"))
    if not prog:
        return None
    documents = service.get_curriculum_documents(request["programme"])
    return rsp.curriculum_response(prog, documents)


def _curriculum_doc_payload(ctx: ConversationContext, request: dict[str, Any]):
    doc = request.get("document")
    if not doc:
        return None
    fields = [
        {"label": "Document", "value": doc.get("title") or doc.get("filename") or "-"},
    ]
    if doc.get("semester"):
        fields.append({"label": "Semester", "value": str(doc["semester"])})
    if doc.get("uploaded_at"):
        fields.append({"label": "Uploaded", "value": str(doc["uploaded_at"])})
    message = "Ask me anything specific about this document and I will look it up."
    return {"type": "detail", "title": "Curriculum Document", "fields": fields, "message": message}


def _programme_pick_payload(ctx: ConversationContext, request: dict[str, Any]):
    return rsp.programme_pick_response()


def _curriculum_subject_search_payload(ctx: ConversationContext, request: dict[str, Any]):
    """Answer a subject-code/name lookup from the active uploaded curriculum."""
    prog = service.get_programme(request.get("programme"))
    if not prog:
        return None
    code = request.get("code")
    query = code or ctx.catalogue_programme_code or prog.get("code") or ""
    try:
        hits = service.curriculum_subject_search(None, prog.get("code") or "", query)
    except Exception:
        hits = None
    if not hits:
        return None
    fields = []
    for h in hits[:10]:
        name = h.get("name") or "-"
        parts = [f"Semester {h.get('_semester')}"]
        if h.get("category"):
            parts.append(h["category"])
        if h.get("credits") is not None:
            parts.append(f"{h['credits']} credits")
        if h.get("hours") is not None:
            parts.append(f"{h['hours']} hours")
        fields.append({"label": h.get("code") or name, "value": " · ".join(str(p) for p in parts)})
    title = f"{prog.get('name') or prog.get('code') or ''} — Subject"
    if code:
        title = f"{title} {code}"
    return {"type": "detail", "title": title, "fields": fields, "message": "From the published curriculum document."}


_OP_HANDLERS: dict[str, Any] = {
    "schemes": _schemes_payload,
    "scheme": _scheme_payload,
    "levels": _levels_payload,
    "list": _list_payload,
    "menu": _menu_payload,
    "overview": _overview_payload,
    "eligibility": _eligibility_payload,
    "fee": _fee_payload,
    "requested": _requested_payload,
    "subjects": _subjects_payload,
    "semesters": _semesters_payload,
    "semester_subjects": _semester_subjects_payload,
    "minors": _minors_payload,
    "minor_subjects": _minor_subjects_payload,
    "vac": _category_payload,
    "sec": _category_payload,
    "aec": _category_payload,
    "credits": _credits_payload,
    "outcomes": _outcomes_payload,
    "curriculum": _curriculum_payload,
    "curriculum_doc": _curriculum_doc_payload,
    "curriculum_subject_search": _curriculum_subject_search_payload,
    "programme_pick": _programme_pick_payload,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fallback_events(chat_id: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "detail",
            "title": "Academic Catalogue",
            "fields": [],
            "message": "Sorry — I could not find matching structured catalogue data for that. Let me check the knowledge base instead.",
        },
        {"type": "done", "chat_id": chat_id, "cited_chunks": []},
    ]


def _extract_semester(text: str) -> int | None:
    """Parse a semester choice: 'semester:2', 'semester 2', '2', 'second'."""
    if "semester:" in text:
        try:
            return int(text.split("semester:", 1)[1].strip().split()[0])
        except (ValueError, IndexError):
            return None
    m = _SEM_PATTERN.search(text)
    if m:
        return int(m.group(0))
    return _SEM_WORDS.get(text)


_SEM_PATTERN = re.compile(r"\b([1-8])\b")
_SEM_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4,
    "fifth": 5, "sixth": 6, "semester 1": 1, "semester 2": 2,
    "semester 3": 3, "semester 4": 4, "semester 5": 5, "semester 6": 6,
    "sem 1": 1, "sem 2": 2, "sem 3": 3, "sem 4": 4,
}

_LEVEL_MAP: dict[str, str] = {
    "ug": "ug", "undergraduate": "ug", "under graduate": "ug", "undergrad": "ug",
    "pg": "pg", "postgraduate": "pg", "post graduate": "pg",
    "phd": "phd", "ph.d": "phd", "doctorate": "phd", "doctoral": "phd",
    "integrated": "integrated",
}


def _parse_level(text: str) -> str | None:
    """Parse a level choice: 'level:ug', 'ug', 'undergraduate', 'pg', ..."""
    lowered = " ".join(str(text or "").strip().lower().split())
    if "level:" in lowered:
        ballpark = lowered.split("level:", 1)[1].strip().split()[0]
        return _LEVEL_MAP.get(ballpark)
    for word, level in _LEVEL_MAP.items():
        if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", lowered):
            return level
    return None


def _menu_items(prog: dict[str, Any]) -> list[tuple[str, str]]:
    """Build the DB-driven programme menu (only items with data are shown)."""
    pid = prog.get("id")
    if not pid:
        return []
    items: list[tuple[str, str]] = [("overview", "Programme Overview")]
    if prog.get("eligibility"):
        items.append(("eligibility", "Eligibility"))
    if prog.get("fee_structure"):
        items.append(("fee", "Fee Structure"))
    if service.get_semesters(pid):
        items.append(("semesters", "Semester-wise Subjects"))
    if service.get_major_subjects(pid):
        items.append(("subjects", "Major Subjects"))
    if service.get_minor_disciplines(pid):
        items.append(("minors", "Minor Subjects"))
    if service.get_category_subjects("vac", programme_id=pid):
        items.append(("vac", "VAC Courses"))
    if service.get_category_subjects("sec", programme_id=pid):
        items.append(("sec", "SEC Courses"))
    if service.get_category_subjects("aec", programme_id=pid):
        items.append(("aec", "AEC Courses"))
    if service.get_learning_outcomes(pid):
        items.append(("outcomes", "Learning Outcomes"))
    if service.get_curriculum_documents(pid):
        items.append(("curriculum", "Curriculum"))
    return items