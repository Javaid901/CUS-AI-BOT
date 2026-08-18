"""
backend/tests/test_smart_orchestrator.py

Acceptance battery for the Smart AI Service Orchestrator.

Covers the required test categories from the specification:

  1. Catalogue / NEP          — nep, nep courses, bca under nep, bca fee,
                                bca subjects, semester subjects, major/minor/
                                VAC/SEC/AEC, credits
  2. Student services         — results, attendance, fee, transcript,
                                admit card, registration
  3. Admissions               — requirements, eligibility, documents, fee
  4. Website knowledge        — latest notices, admission/exam notification,
                                holiday notice, academic calendar
  5. Authorities / colleges   — registrar, exam wing, affiliated colleges
  6. Context switching        — BCA -> fee -> subjects -> semester -> result;
                                NEP -> course list -> BCA -> eligibility
  7. Spelling / typo support  — nepp, admision, attendence, reslt, bcaa,
                                mcaa, subjcts, cources
  8. Failure handling         — missing info, low confidence, ambiguous

Run:  python tests/test_smart_orchestrator.py          (or via pytest)
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register catalogue tables before any session)

from app.database import SessionLocal, create_all
from app.orchestrator.context import ConversationContext
from app.orchestrator.extractor import extract_entities
from app.orchestrator.query_understanding import process_query

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
        seed_catalogue(db)
    finally:
        db.close()
    _ensure_authority_seeds()


def _ensure_authority_seeds():
    """Insert the university offices the authority tests rely on.

    Idempotent: skipped when the departments already exist. The in-memory
    authority cache is refreshed so matcher lookups see the rows.
    """
    from app.authority.models import Authority
    from app.authority.service import authority_service

    rows = [
        Authority(
            department_name="Registrar Office",
            authority_name="Registrar",
            designation="Registrar",
            email="registrar@example.edu",
            phone="0194-0000000",
            keywords='["registrar", "registration office", "office"]',
            services_offered='["registration"]',
            description="The Registrar is the custodian of academic records and admission procedures.",
        ),
        Authority(
            department_name="Controller of Examinations",
            authority_name="Controller of Examinations",
            designation="Controller of Examinations",
            email="coe@example.edu.pk",
            phone="0194-0000001",
            keywords='["exams", "examination", "exam", "coe"]',
            services_offered='["results", "datesheet", "hall tickets"]',
            description="The Controller of Examinations handles examination notifications, results and datesheets.",
        ),
    ]
    db = SessionLocal()
    try:
        existing = {r.department_name for r in db.query(Authority).all()}
        added = False
        for r in rows:
            if r.department_name not in existing:
                db.add(r)
                added = True
        if added:
            db.commit()
        authority_service.refresh_cache(db)
    finally:
        db.close()


def _plan(raw: str, ctx: ConversationContext | None = None):
    from app.orchestrator.planner import plan
    ctx = ctx or ConversationContext()
    e = extract_entities(raw)
    return plan(raw, ctx, f"smart-{uuid.uuid4()}", e)


# ---------------------------------------------------------------------------
# 1. Catalogue / NEP
# ---------------------------------------------------------------------------

_CATALOGUE_CASES = {
    "nep": ("catalogue", "scheme"),
    "nepp": ("catalogue", "scheme"),
    "nep courses": ("catalogue", "list"),
    "show ug courses under nep": ("catalogue", "list"),
    "bca under nep": ("catalogue", "overview"),
    "major subjects in bca": ("catalogue", "subjects"),
    "minor subjects in bca": ("catalogue", "minors"),
    "vac courses": ("catalogue", "vac"),
    "sec courses": ("catalogue", "sec"),
    "aec courses": ("catalogue", "aec"),
    "subjects of bca": ("catalogue", "subjects"),
    "credits of bca": ("catalogue", "credits"),
}


def test_catalogue():
    print("-- 1. catalogue / NEP routing --")
    for raw, (action, target) in sorted(_CATALOGUE_CASES.items()):
        p = _plan(raw)
        ok = p.action == action and p.target == target
        check(f"plan {raw!r} -> {action}/{target}", ok,
              f"got action={p.action} target={p.target}")


# ---------------------------------------------------------------------------
# 2. Student services
# ---------------------------------------------------------------------------

_SERVICE_CASES = {
    "results": ("connector", "results"),
    "my attendance": ("connector", "attendance"),
    "fee receipt": ("connector", "fee"),
    "transcript": ("connector", "transcript"),
    "download admit card": ("connector", "admit_card"),
    "registration": ("connector", "registration"),
}


def test_student_services():
    print("-- 2. student service routing --")
    for raw, (action, target) in sorted(_SERVICE_CASES.items()):
        p = _plan(raw)
        ok = p.action == action and p.target == target
        check(f"service {raw!r} -> {action}/{target}", ok,
              f"got action={p.action} target={p.target}")


# ---------------------------------------------------------------------------
# 3. Admissions
# ---------------------------------------------------------------------------

_ADMISSION_CASES = {
    "admission requirements for bca": ("catalogue", "eligibility"),
    "fee structure of bca": ("catalogue", "fee"),
    "eligibility for bca": ("catalogue", "eligibility"),
    "documents required for admission": ("slot_fill", "programme"),
}


def test_admissions():
    print("-- 3. admission routing --")
    for raw, (action, target) in sorted(_ADMISSION_CASES.items()):
        p = _plan(raw)
        ok = p.action == action and p.target == target
        check(f"admission {raw!r} -> {action}/{target}", ok,
              f"got action={p.action} target={p.target}")


# ---------------------------------------------------------------------------
# 4. Website knowledge — current notices must route to the synced website
#    knowledge (never to a dead-end menu)
# ---------------------------------------------------------------------------

_NEWS_CASES = [
    "latest admission notice",
    "latest circular",
    "examination notification",
    "holiday notice",
    "academic calendar",
    "latest exam notification",
]


def test_website_knowledge():
    print("-- 4. website knowledge (news) routing --")
    for raw in _NEWS_CASES:
        p = _plan(raw)
        check(f"news {raw!r} -> news/rag", p.action in ("news", "rag"),
              f"got action={p.action} target={p.target} reason={p.reason}")
        # Never a dead-end menu for a news query
        check(f"news {raw!r} not a menu", p.action != "navigation", p.reason)


# ---------------------------------------------------------------------------
# 5. Authorities / colleges
# ---------------------------------------------------------------------------


def test_authorities_and_colleges():
    print("-- 5. authorities / colleges --")
    p = _plan("who is registrar")
    check("who is registrar -> authority", p.action == "authority",
          f"got action={p.action} target={p.target}")

    p = _plan("who handles exams")
    check("who handles exams -> authority", p.action == "authority",
          f"got action={p.action} target={p.target}")

    p = _plan("which college offers bca")
    check("which college offers bca -> options", p.action == "navigation"
          and p.response and p.response.get("type") == "options",
          f"got action={p.action} type={p.response and p.response.get('type')}")


# ---------------------------------------------------------------------------
# 6. Context switching (conversation memory)
# ---------------------------------------------------------------------------

_SCENARIOS = [
    {
        "name": "BCA -> fee -> subjects -> semester -> result",
        "steps": [
            ("bca", "catalogue", "overview"),
            ("fee", "catalogue", "fee"),
            ("subjects", "catalogue", "subjects"),
            ("semester 3 subjects", "catalogue", "semester_subjects"),
            ("result", "connector", "results"),
        ],
    },
    {
        "name": "NEP -> course list -> BCA -> eligibility",
        "steps": [
            ("nep", "catalogue", "scheme"),
            ("nep courses", "catalogue", "list"),
            ("bca", "catalogue", "overview"),
            ("eligibility", "catalogue", "eligibility"),
        ],
    },
    {
        "name": "fee after programme memory (bare topic)",
        "steps": [
            ("tell me about bca", "catalogue", "overview"),
            ("fee", "catalogue", "fee"),
            ("and eligibility", "catalogue", "eligibility"),
        ],
    },
    {
        "name": "programme switch mid-flow",
        "steps": [
            ("bca fee", "catalogue", "fee"),
            ("mca", "catalogue", "overview"),
            ("fee", "catalogue", "fee"),
        ],
    },
]


def test_context_switching():
    print("-- 6. context switching --")
    from app.orchestrator.planner import plan

    for scenario in _SCENARIOS:
        ctx = ConversationContext()
        chat = f"smart-{uuid.uuid4()}"
        for step_idx, (raw, action, target) in enumerate(scenario["steps"]):
            e = extract_entities(raw)
            p = plan(raw, ctx, chat, e)
            ok = p.action == action and p.target == target
            check(
                f"{scenario['name']} step{step_idx + 1} {raw!r} -> {action}/{target}",
                ok,
                f"got action={p.action} target={p.target} reason={p.reason}",
            )


# ---------------------------------------------------------------------------
# 7. Spelling / typo tolerance
# ---------------------------------------------------------------------------

_TYPO_CASES = {
    "nepp": "nep",
    "admision": "admission",
    "attendence": "attendance",
    "attandance": "attendance",
    "reslt": "result",
    "bcaa": "bca",
    "mcaa": "mca",
    "subjcts": "subjects",
    "cources": "courses",
}


def test_typo_tolerance():
    print("-- 7. spelling / typo tolerance --")
    for raw, expected in sorted(_TYPO_CASES.items()):
        qr = process_query(raw)
        check(f"clean {raw!r}", qr["clean"] == expected, f"got={qr['clean']!r}")
    # typo forms must still route to the right action
    p = _plan("bcaa fee")
    check("typo 'bcaa' routes to catalogue fee", p.action == "catalogue" and p.target == "fee",
          f"got action={p.action} target={p.target}")


# ---------------------------------------------------------------------------
# 8. Failure handling / confidence
# ---------------------------------------------------------------------------


def test_failure_handling():
    print("-- 8. confidence / failure handling --")
    p = _plan("fee")
    check("bare fee -> one missing-slot question", p.action == "slot_fill"
          and p.target == "programme", f"got action={p.action} target={p.target}")

    p = _plan("xyzzy qwerty")
    check("low confidence -> RAG, no dead-end", p.action in ("rag", "news", "clarify"),
          f"got action={p.action}")

    p = _plan("what is the schedule for sem 4 of mca")
    check("ambiguous schedule -> useful answer, no dead-end",
          p.action in ("rag", "news", "clarify", "slot_fill", "structured", "catalogue")
          and p.action not in ("navigation", "welcome"),
          f"got action={p.action} reason={p.reason}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    t0 = time.time()
    _ensure_seeded()
    test_catalogue()
    test_student_services()
    test_admissions()
    test_website_knowledge()
    test_authorities_and_colleges()
    test_context_switching()
    test_typo_tolerance()
    test_failure_handling()
    print(f"\nTotal checks: {len(PASS) + len(FAIL)} | Passed: {len(PASS)} | "
          f"Failed: {len(FAIL)} | time: {time.time() - t0:.1f}s")
    if FAIL:
        print("FAILED:", FAIL[:10])
        sys.exit(1)


_ensure_seeded()  # pytest mode parity: tables + catalogue seed (idempotent)

if __name__ == "__main__":
    main()