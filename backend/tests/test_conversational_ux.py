"""
backend/tests/test_conversational_ux.py

Conversational UX matrix — validates the master conversational intelligence
refactor end to end (engine level, catalogue paths only; no Ollama required):

  1. NLU phrasing variants for the same intent ("fee structure of BCA",
     "what is the fee for MCA", "how much is BBA", ...) -> DIRECT answers.
  2. Contextual follow-ups ("tell me about BBA" -> "what is the fee?").
  3. Context override ("tell me about BBA" -> "what is MCA eligibility?").
  4. Multi-entity / programme comparison ("difference between BBA and BCA").
  5. Ambiguity handling ("fee structure" -> targeted slot-fill question).
  6. Slot-fill continuation ("fee structure" -> "MCA" -> MCA fee card).
  7. Quick Help semantic shortcuts ("Courses" / "Fee Structure" / "Admissions").
  8. Canonical query contract attached to every plan.
  9. Retrieval scoping helpers (foreign-programme chunks filtered).
 10. SSE framing (multi-line tokens never break the stream).

Run:  python -m pytest tests/test_conversational_ux.py -q
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register catalogue tables before any session)

from app.database import SessionLocal, create_all

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def _ensure_seeded():
    create_all()
    from app.catalogue.seed import seed_catalogue
    db = SessionLocal()
    try:
        return seed_catalogue(db)
    finally:
        db.close()


def _event_types(evs) -> str:
    return ", ".join(e.get("type", "") for e in evs)


def _titles(evs) -> list[str]:
    return [e.get("title") or "" for e in evs if e.get("type") in ("options", "detail")]


def _texts(evs) -> list[str]:
    return [e.get("text") or "" for e in evs if e.get("type") == "token"]


def _found(evs, needle: str) -> bool:
    return any(needle.lower() in t.lower() for t in _titles(evs) + _texts(evs))


def _catalogue_fee_events(evs) -> bool:
    """True when the reply is a catalogue fee answer (structured card or text)."""
    if _found(evs, "Fee Structure"):
        return True
    if _found(evs, "fee"):
        return True
    return any("fee" in t.lower() for t in _texts(evs))


# ---------------------------------------------------------------------------
# 1. NLU phrasing variants -> direct answers
# ---------------------------------------------------------------------------

_DEFAULT_VARIANTS = [
    "fee structure of BCA",
    "what is the fee for MCA",
    "fees of BBA",
    "how much is the BCA fee",
    "cost of BCA programme",
    "what is the tuition fee for MBA",
    "eligibility criteria for MCA",
    "what are the admission requirements for BBA",
    "who can apply for M.Com",
    "subjects in BCA",
    "semester 2 subjects of BCA",
    "tell me about BBA",
    "BBA",
]


def test_direct_answer_variants():
    print("-- 1. NLU phrasing variants -> direct answers --")
    _ensure_seeded()
    db = SessionLocal()
    try:
        user_id = str(uuid.uuid4())
        chat = str(uuid.uuid4())

        async def _ask(msg):
            from app.orchestrator.engine import process
            events = []
            async for ev in process(db, user_id, msg, chat):
                events.append(ev)
            return events

        for raw in _DEFAULT_VARIANTS:
            evs = asyncio.run(_ask(raw))
            structured = any(e.get("type") in ("detail", "options") for e in evs)
            error = any(e.get("type") == "error" for e in evs)
            check(
                f"direct answer {raw!r}",
                structured and not error,
                f"types={_event_types(evs)}",
            )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. Contextual follow-up
# ---------------------------------------------------------------------------


def test_contextual_followup():
    print("-- 2. contextual follow-up: BBA -> fee --")
    _ensure_seeded()
    db = SessionLocal()
    try:
        user_id = str(uuid.uuid4())
        chat = str(uuid.uuid4())

        async def _ask(msg):
            from app.orchestrator.engine import process
            events = []
            async for ev in process(db, user_id, msg, chat):
                events.append(ev)
            return events

        evs1 = asyncio.run(_ask("tell me about BBA"))
        check("BBA overview first", _found(evs1, "BBA"), _event_types(evs1))

        evs2 = asyncio.run(_ask("what is the fee?"))
        check(
            "fee follow-up resolves to BBA (no programme repeated)",
            _found(evs2, "BBA") and _catalogue_fee_events(evs2),
            f"types={_event_types(evs2)} titles={_titles(evs2)}",
        )

        evs3 = asyncio.run(_ask("and what about eligibility?"))
        check(
            "second follow-up keeps programme context",
            _found(evs3, "BBA"),
            f"titles={_titles(evs3)}",
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. Context override — explicit new programme wins
# ---------------------------------------------------------------------------


def test_context_override():
    print("-- 3. context override: BBA -> MCA eligibility --")
    _ensure_seeded()
    db = SessionLocal()
    try:
        user_id = str(uuid.uuid4())
        chat = str(uuid.uuid4())

        async def _ask(msg):
            from app.orchestrator.engine import process
            events = []
            async for ev in process(db, user_id, msg, chat):
                events.append(ev)
            return events

        asyncio.run(_ask("tell me about BBA"))
        evs = asyncio.run(_ask("what is MCA eligibility?"))
        check(
            "explicit MCA overrides BBA context",
            _found(evs, "MCA"),
            f"titles={_titles(evs)}",
        )
        # The BBA answer must not be shown for the MCA question
        check(
            "no BBA fee leakage into MCA answer",
            not _found(evs, "BBA"),
            f"titles={_titles(evs)}",
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4. Programme comparison (multi-entity)
# ---------------------------------------------------------------------------


def test_comparison():
    print("-- 4. comparison: BBA vs BCA --")
    _ensure_seeded()
    db = SessionLocal()
    try:
        user_id = str(uuid.uuid4())
        chat = str(uuid.uuid4())

        async def _ask(msg):
            from app.orchestrator.engine import process
            events = []
            async for ev in process(db, user_id, msg, chat):
                events.append(ev)
            return events

        evs = asyncio.run(_ask("difference between BBA and BCA"))
        check(
            "comparison detail rendered",
            _found(evs, "Comparison"),
            f"types={_event_types(evs)} titles={_titles(evs)}",
        )
        check(
            "both programmes present",
            _found(evs, "BBA") and _found(evs, "BCA"),
            f"titles={_titles(evs)}",
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 5. Ambiguity -> targeted slot-fill question
# ---------------------------------------------------------------------------


def test_ambiguity_slot_fill():
    print("-- 5. ambiguity: bare fee structure -> slot-fill --")
    _ensure_seeded()
    db = SessionLocal()
    try:
        user_id = str(uuid.uuid4())
        chat = str(uuid.uuid4())

        async def _ask(msg):
            from app.orchestrator.engine import process
            events = []
            async for ev in process(db, user_id, msg, chat):
                events.append(ev)
            return events

        evs = asyncio.run(_ask("fee structure"))
        check(
            "targeted 'which programme?' question",
            _found(evs, "programme"),
            f"types={_event_types(evs)} texts={_texts(evs)}",
        )
        check(
            "programme chips offered",
            any(e.get("type") == "options" for e in evs),
            _event_types(evs),
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 6. Slot-fill continuation — the answer continues the ORIGINAL request
# ---------------------------------------------------------------------------


def test_slot_fill_continuation():
    print("-- 6. slot-fill continuation: 'fee structure' -> 'MCA' --")
    _ensure_seeded()
    db = SessionLocal()
    try:
        user_id = str(uuid.uuid4())
        chat = str(uuid.uuid4())

        async def _ask(msg):
            from app.orchestrator.engine import process
            events = []
            async for ev in process(db, user_id, msg, chat):
                events.append(ev)
            return events

        asyncio.run(_ask("fee structure"))
        evs = asyncio.run(_ask("MCA"))
        check(
            "MCA fee answered after slot-fill (not the MCA overview)",
            _catalogue_fee_events(evs) and _found(evs, "MCA"),
            f"types={_event_types(evs)} titles={_titles(evs)} texts={_texts(evs)}",
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 7. Quick Help semantic shortcuts
# ---------------------------------------------------------------------------


def test_quick_help():
    print("-- 7. Quick Help semantic shortcuts --")
    _ensure_seeded()
    db = SessionLocal()
    try:
        user_id = str(uuid.uuid4())
        chat = str(uuid.uuid4())

        async def _ask(msg):
            from app.orchestrator.engine import process
            events = []
            async for ev in process(db, user_id, msg, chat):
                events.append(ev)
            return events

        evs_courses = asyncio.run(_ask("Courses"))
        check(
            "'Courses' opens the academic scheme picker",
            _found(evs_courses, "Academic Scheme"),
            f"titles={_titles(evs_courses)}",
        )

        evs_fee = asyncio.run(_ask("Fee Structure"))
        check(
            "'Fee Structure' asks which programme (slot-fill)",
            _found(evs_fee, "programme"),
            f"titles={_titles(evs_fee)}",
        )

        evs_adm = asyncio.run(_ask("Admissions"))
        check(
            "'Admissions' shows admission options",
            any(e.get("type") == "options" for e in evs_adm),
            _event_types(evs_adm),
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 8. Canonical query contract attached to plans
# ---------------------------------------------------------------------------


def test_plan_contract():
    print("-- 8. canonical query contract --")
    _ensure_seeded()
    db = SessionLocal()
    try:
        from app.orchestrator.planner import plan
        from app.orchestrator.context import ConversationContext
        from app.orchestrator.extractor import extract_entities

        ctx = ConversationContext()
        p = plan("what is the fee for MCA", ctx, "chat-x", extract_entities("what is the fee for MCA"))
        contract = (p.extra or {}).get("contract")
        check("contract attached", isinstance(contract, dict), str(p.extra))
        if contract:
            check("contract intent = fee_information", contract.get("intent") == "fee_information", str(contract))
            check("contract programme = mca", contract.get("programme") == "mca", str(contract))
            check("contract action = catalogue", p.action == "catalogue", p.action)

        ctx2 = ConversationContext()
        p2 = plan("fee structure", ctx2, "chat-x", extract_entities("fee structure"))
        c2 = (p2.extra or {}).get("contract") or {}
        check("slot-fill contract marks clarification", c2.get("needs_clarification") is True, str(c2))
        check("slot-fill contract clarification field", c2.get("clarification_field") == "programme", str(c2))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 9. Retrieval scoping — foreign-programme chunks filtered
# ---------------------------------------------------------------------------


def test_retrieval_scoping():
    print("-- 9. retrieval scoping --")
    from app.chat.service import _scope_chunks, _scope_note

    chunks = [
        {"document_title": "BCA Curriculum", "heading": "Semester 2", "content": "BCA subjects here"},
        {"document_title": "MCA Curriculum", "heading": "Semester 1", "content": "MCA subjects here"},
        {"document_title": "University Admission Prospectus", "heading": "General", "content": "admission details for all programmes"},
    ]
    scoped = _scope_chunks(chunks, {"programme": "bca"})
    check("foreign MCA chunk dropped", all("MCA Curriculum" not in (c.get("document_title") or "") for c in scoped), str([c.get("document_title") for c in scoped]))
    check("target chunk kept", any(c.get("document_title") == "BCA Curriculum" for c in scoped), str([c.get("document_title") for c in scoped]))
    check("general chunk kept", any(c.get("document_title") == "University Admission Prospectus" for c in scoped), str([c.get("document_title") for c in scoped]))

    note = _scope_note({"programme": "bca", "academic_scheme": "nep2020", "semester": 2})
    check("scope note names the programme", note and "BCA" in note, str(note))
    check("scope note carries scheme+semester", note and "NEP2020" in note and "2" in note, str(note))

    cmp_note = _scope_note({"programme": "bca", "programmes": ["bba", "bca"]})
    check("comparison scope keeps both", cmp_note and "BBA" in cmp_note and "BCA" in cmp_note, str(cmp_note))
    check("no scope note without programme", _scope_note({}) is None)


# ---------------------------------------------------------------------------
# 10. SSE framing — multi-line tokens survive
# ---------------------------------------------------------------------------


def test_sse_framing():
    print("-- 10. SSE framing --")
    from app.chat.routes import _sse

    frame = _sse(None, "line one\nline two\n\nfinal")
    lines = frame.rstrip("\n").split("\n")
    check("every data line prefixed", all(ln.startswith("data: ") for ln in lines), repr(frame))
    check("newline-delimited blocks intact", frame.count("\n\n") >= 1, repr(frame))

    ev_frame = _sse("error", '{"message":"x"}')
    check("event frames unchanged", ev_frame == 'event: error\ndata: {"message":"x"}\n\n', repr(ev_frame))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_all() -> None:
    tests = [
        test_direct_answer_variants,
        test_contextual_followup,
        test_context_override,
        test_comparison,
        test_ambiguity_slot_fill,
        test_slot_fill_continuation,
        test_quick_help,
        test_plan_contract,
        test_retrieval_scoping,
        test_sse_framing,
    ]
    for test in tests:
        test()
    print(f"\nPASS={len(PASS)} FAIL={len(FAIL)}")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    run_all()