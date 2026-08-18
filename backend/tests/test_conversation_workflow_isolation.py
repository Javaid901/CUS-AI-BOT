"""
backend/tests/test_conversation_workflow_isolation.py

Regression battery for conversation workflow isolation.

Covers the workflow-leakage bug class where a label typed or chipped by the
user (admission, programme levels, programme ids, domain keywords) was
hijacked by the semantic classifier into an unrelated workflow:

      Admission -> Undergraduate            -> wrongly "Colleges"        (now "UG Programmes")
      Admission -> [ug chip]                -> wrongly "Results"        (now "UG Programmes")
      BCA                                   -> wrongly "BCA Eligibility" (now "BCA overview")

Fixes under test:
  1. intent_router.classify() resolves literal navigation labels FIRST
     (keyword map + level-word map) and only falls back to the semantic
     classifier for natural-language phrasing.
  2. planner stage_0c semantic-topic enrichment is disabled when the message
     is itself a navigation label (option id / bare programme / bare level /
     domain keyword).
  3. planner routes a bare `level` entity to its own navigation response
     instead of letting it drop into misclassification.

Run:  python3 tests/test_conversation_workflow_isolation.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chat.intent_router import classify as classify_nav
from app.database import SessionLocal, create_all
from app.orchestrator.context import ConversationContext
from app.orchestrator.extractor import extract_entities
from app.orchestrator.planner import plan

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def _plan(raw: str, ctx: ConversationContext | None = None):
    ctx = ctx or ConversationContext()
    chat = f"isol-{uuid.uuid4()}"
    return plan(raw, ctx, chat, extract_entities(raw))


# ---------------------------------------------------------------------------
# 1. Literal navigation labels resolve deterministically (never semantically
#    warped by the embedding model)
# ---------------------------------------------------------------------------

_LITERAL_CASES = [
    ("ug", "ug"),
    ("pg", "pg"),
    ("phd", "phd"),
    ("integrated", "integrated"),
    ("dyd", "dyd"),
    ("undergraduate", "ug"),
    ("postgraduate", "pg"),
    ("admission", "admissions"),
    ("admissions", "admissions"),
    ("fee structure", "fee"),
    ("fees", "fee"),
    ("results", "results"),
    ("result", "results"),
    ("courses", "courses"),
    ("colleges", "colleges"),
    ("hostel", "hostel"),
    ("scholarships", "scholarships"),
]


def test_classify_literal_first() -> None:
    print("-- classify: literal labels resolve deterministically --")
    for raw, expected in _LITERAL_CASES:
        it, cat = classify_nav(raw)
        check(f"classify {raw!r} -> broad/{expected}",
              it == "broad" and cat == expected,
              f"got=({it}, {cat})")


_test_natural_language = [
    ("available programmes", "courses"),
    ("what courses are offered", None),    # question word -> specific (RAG)
    ("constituent colleges", "colleges"),
    ("tuition fees", "fee"),
    ("exam schedule", "datesheet"),
]


def test_classify_semantic_fallback() -> None:
    print("-- classify: natural-language fallback preserved --")
    for raw, expected in _test_natural_language:
        it, cat = classify_nav(raw)
        if expected is None:
            check(f"classify specific {raw!r}", it == "specific", f"got=({it}, {cat})")
        else:
            check(f"classify fallback {raw!r}", it == "broad" and cat == expected, f"got=({it}, {cat})")


# ---------------------------------------------------------------------------
# 2. Planner-level regression
# ---------------------------------------------------------------------------

def test_plan_level_keyword() -> None:
    print("-- planner: bare level keyword -> its own navigation --")
    for raw, expect_title in [
        ("undergraduate", "UG Programmes"),
        ("ug", "UG Programmes"),
        ("pg", "PG Programmes"),
        ("phd", "PhD Programmes"),
    ]:
        p = _plan(raw)
        title = (p.response or {}).get("title")
        check(f"plan {raw!r} -> {expect_title}",
              p.action in ("navigation", "structured") and title == expect_title,
              f"got action={p.action} title={title}")


def test_plan_bare_programme() -> None:
    print("-- planner: bare programme keeps overview (no topic hijack) --")
    for raw in ("BCA", "MBA"):
        p = _plan(raw)
        check(f"plan {raw!r} -> programme detail",
              p.action in ("catalogue", "structured"),
              f"got action={p.action} target={p.target}")
        check(f"plan {raw!r} not hijacked by semantic topic",
              not (p.target and "/" in p.target),
              f"got target={p.target}")


def test_plan_slot_fill_kept() -> None:
    print("-- planner: conversational slot-fill still works --")
    for raw in ("fee", "how much", "eligibility"):
        p = _plan(raw)
        check(f"plan {raw!r} -> slot_fill/programme",
              p.action == "slot_fill" and p.target == "programme",
              f"got action={p.action} target={p.target}")


def test_plan_workflow_sequence() -> None:
    print("-- planner: full admissions workflow stays inside admissions --")
    ctx = ConversationContext()
    chat = f"isol-{uuid.uuid4()}"

    def step(raw):
        e = extract_entities(raw)
        return plan(raw, ctx, chat, e)

    p1 = step("Admission")
    check("step1 admission", p1.action == "navigation" and (p1.response or {}).get("title") == "Admissions",
          f"got action={p1.action} title={(p1.response or {}).get('title')}")

    p2 = step("Undergraduate")
    check("step2 undergraduate -> UG Programmes",
          p2.action == "navigation" and (p2.response or {}).get("title") == "UG Programmes",
          f"got action={p2.action} title={(p2.response or {}).get('title')}")

    p3 = step("BA")
    check("step3 BA -> programme detail", p3.action in ("catalogue", "structured"),
          f"got action={p3.action} target={p3.target}")

    p4 = step("fee")
    check("step4 fee after BA -> structured fee",
          p4.action in ("catalogue", "structured") and p4.target and p4.target != "programme",
          f"got action={p4.action} target={p4.target}")


# ---------------------------------------------------------------------------
# 3. Engine end-to-end (real SSE events; no LLM/RAG needed for these flows)
# ---------------------------------------------------------------------------

_E2E_FLOWS = [
    ("Admission -> Undergraduate", ["Admission", "Undergraduate"], "UG Programmes", ("Results", "Colleges")),
    ("Admission -> ug chip", ["Admission", "ug"], "UG Programmes", ("Results",)),
    ("Admission -> Undergraduate -> BA", ["Admission", "Undergraduate", "BA"], "BA (Bachelor of Arts)", ("Results",)),
    ("Admission -> Undergraduate -> BCA -> fee", ["Admission", "Undergraduate", "BCA", "fee"], "Fee Structure", ("Results",)),
    ("phd typed", ["phd"], "PhD Programmes", ("Results",)),
]


def _flow_titles(evs) -> list[str]:
    return [str(e.get("title")) for e in evs if e.get("title")]


def _e2e_flow(name: str, messages: list[str], expected: str, forbidden: tuple[str, ...]) -> None:
    db = SessionLocal()
    try:
        user_id = f"u-{uuid.uuid4()}"
        chat = f"chat-{uuid.uuid4()}"

        async def ask(msg):
            from app.orchestrator.engine import process
            events = []
            async for ev in process(db, user_id, msg, chat):
                events.append(ev)
            return events

        for idx, msg in enumerate(messages):
            evos = asyncio.run(ask(msg))
            titles = _flow_titles(evos)
            joined = " | ".join(titles)
            if idx == len(messages) - 1:
                check(f"e2e {name} step [{msg!r}] -> '{expected}'",
                      any(expected in t for t in titles),
                      f"titles={joined}")
            for forb in forbidden:
                check(f"e2e {name} step [{msg!r}] no '{forb}'",
                      not any(forb.lower() in t.lower() for t in titles),
                      f"titles={joined}")
    finally:
        db.close()


def test_engine_e2e() -> None:
    print("-- engine e2e: workflow isolation --")
    for name, messages, expected, forbidden in _E2E_FLOWS:
        _e2e_flow(name, messages, expected, forbidden)


def test_back_navigation() -> None:
    print("-- back navigation (path-aware, stays in workflow) --")
    from app.orchestrator.query_understanding import process_query
    qr = process_query("back")
    check("query_understanding keeps 'back' (no fuzzy BA correction)",
          qr["clean"] == "back", f"got={qr['clean']!r}")

    from app.chat.intent_router import set_nav_path
    chat = f"back-{uuid.uuid4()}"
    set_nav_path(chat, ["admissions", "ug", "ba"])
    e = extract_entities("back")
    p = plan("back", ConversationContext(), chat, e)
    title = (p.response or {}).get("title")
    check("planner 'back' -> parent level response",
          p.action in ("navigation", "welcome") and title in ("UG Programmes", "How can I help you?"),
          f"got action={p.action} title={title}")
    check("planner 'back' not hijacked into programme detail",
          "Bachelor" not in str(title), f"got title={title}")


def _e2e_back(name: str, messages: list[str], expected: str, forbidden: tuple[str, ...]) -> None:
    db = SessionLocal()
    try:
        user_id = f"u-{uuid.uuid4()}"
        chat = f"chat-{uuid.uuid4()}"

        async def ask(msg):
            from app.orchestrator.engine import process
            events = []
            async for ev in process(db, user_id, msg, chat):
                events.append(ev)
            return events

        for idx, msg in enumerate(messages):
            evos = asyncio.run(ask(msg))
            titles = _flow_titles(evos)
            joined = " | ".join(titles)
            if idx == len(messages) - 1:
                check(f"e2e {name} final -> {expected!r}",
                      any(expected in t for t in titles), f"titles={joined}")
            for forb in forbidden:
                check(f"e2e {name} step [{msg!r}] no '{forb}'",
                      not any(forb.lower() in t.lower() for t in titles),
                      f"titles={joined}")
    finally:
        db.close()


def test_back_engine_e2e() -> None:
    print("-- engine e2e: back within admissions workflow --")
    _e2e_back("Admission -> ug -> back", ["Admission", "ug", "back"], "Admissions", ("Welcome", "Results"))
    _e2e_back("Admission -> ug -> BA -> back", ["Admission", "ug", "BA", "back"], "Admissions", ("Results",))
    _e2e_back("back at top level", ["back"], "How can I help you?", ())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    create_all()
    test_classify_literal_first()
    test_classify_semantic_fallback()
    test_plan_level_keyword()
    test_plan_bare_programme()
    test_plan_slot_fill_kept()
    test_plan_workflow_sequence()
    test_engine_e2e()
    test_back_navigation()
    test_back_engine_e2e()
    print(f"\nTotal checks: {len(PASS) + len(FAIL)} | Passed: {len(PASS)} | Failed: {len(FAIL)}")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()