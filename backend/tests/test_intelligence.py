"""
backend/tests/test_intelligence.py

Conversational Intelligence upgrade battery.

Validates the 13 intelligence-upgrade requirements with a large battery of
realistic user phrasings:

  A. query_understanding clean-text corrections   (misspellings + synonyms)
  B. extractor entity extraction                   (abbreviations, multi-slot)
  C. catalogue detect op routing                   (direct answers, NEP hub)
  D. planner action routing                        (services, clarification, priority)
  E. conversation context memory scenarios         (follow-ups, smart clarify)
  F. engine end-to-end flows                       (NEP hub, fee, semesters, pickers)
  G. regression / no-false-positive checks

Run:  python tests/test_intelligence.py
"""

from __future__ import annotations

import asyncio
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
from app.orchestrator.state import ConversationState

from app.catalogue import detect, service

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


# ---------------------------------------------------------------------------
# A. Query understanding: misspellings + synonym corrections
# ---------------------------------------------------------------------------

_MISSPELL_CASES = {
    "admision process for bca": "admission process for BCA",
    "attendence percentage": "attendance percentage",
    "my attendence record": "my attendance record",
    "show reslt": "show result",
    "reslts of semester 3": "results of semester 3",
    "bcaa fee structure": "bca fee structure",
    "mcaa syllabus": "mca syllabus",
    "bca syllbus": "BCA syllabus",
    "bca curriculam": "BCA curriculum",
    "semster 4 subjects": "semester 4 subjects",
    "nepp courses": "nep courses",
    "new education policy 2020 details": "new education policy 2020 details",
    "progamme details": "programme details",
    "which cources are available": "which courses are available",
    "scholerhip for bca": "scholarship for BCA",
    "eligibilty for mba": "eligibility for MBA",
    "how much feestructure": "how much fee structure",
    "attendence of students": "attendance of students",
    "exam form last date": "examination form last date",
    "transcrip request": "transcript request",
    "show me the prospectuss": "show me the prospectus",
    "hostel faciltiies": "hostel facilities",
    "admisssion in bca": "admission in BCA",
    "what is the duraton": "what is the duration",
    "cuet score for bca": "CUET score for BCA",
    "sgpa of last semester": "sgpa of last semester",
}


def test_query_understanding():
    print("-- A. query_understanding (clean text) --")
    for raw, expected in sorted(_MISSPELL_CASES.items()):
        qr = process_query(raw)
        check(f"clean {raw!r}", qr["clean"] == expected, f"got={qr['clean']!r} want={expected!r}")
    # Corrections must be flagged so the planner uses the cleaned text.
    qr = process_query("bcaa fee structure")
    check("correction flag set", qr["corrected"] is True, f"got={qr['corrected']}")
    # Legit plurals must NOT be flagged as corrections.
    qr = process_query("semesters of bca")
    check("legit plural untouched", qr["clean"] == "semesters of BCA", f"got={qr['clean']!r}")


# ---------------------------------------------------------------------------
# B. Extractor: abbreviations + multi-slot entities
# ---------------------------------------------------------------------------

_MULTI_SLOT_CASES = {
    # (programme, semester, service, scheme) — the extractor exposes major/minor/
    # vac/sec/aec phrasing as detectables terms, not as a service slot.
    "semester 5 minor subjects of BCA under NEP": ("bca", 5, None, "nep"),
    "semester 3 major subjects of BCA": ("bca", 3, None, None),
    "4th semester subjects of MCA": ("mca", 4, None, None),
    "VAC courses in BCA semester 2": ("bca", 2, None, None),
    "SEC courses of MBA": ("mba", None, None, None),
    "AEC courses in semester 1": (None, 1, None, None),
    "credits of BA English": ("ba", None, None, None),
    "learning outcomes of PhD CS": ("phd", None, None, None),
    "fee for FYUGP programmes": (None, None, None, "nep2020"),
    "SGPA of BCA students": ("bca", None, "results", None),
    "CGPA calculation for MCA": ("mca", None, "results", None),
    "grades of semester 2 BBA": ("bba", 2, "results", None),
}


def test_extractor():
    print("-- B. extractor (entities) --")
    for raw, (prog, sem, service_name, scheme) in sorted(_MULTI_SLOT_CASES.items()):
        e = extract_entities(raw)
        check(
            f"entities {raw!r}",
            (e.programme == prog and e.semester == sem and e.service == service_name and e.scheme == scheme),
            f"got prog={e.programme} sem={e.semester} svc={e.service} scheme={e.scheme}",
        )
    for raw, expect in {
        "how to apply for BCA": "admission_mode",
        "what papers are there in BCA": "specializations",
        "semester modules of MCA": "specializations",  # modules -> specializations
    }.items():
        e = extract_entities(raw)
        check(f"topic {raw!r} -> {e.topic}", e.topic == expect, f"got={e.topic}")


# ---------------------------------------------------------------------------
# C. Catalogue detect: direct answers + NEP hub + smart routing
# ---------------------------------------------------------------------------

_DIRECT_OPS = {
    "fee structure of bca": "fee",
    "fees of bca": "fee",
    "how much is bca": "fee",
    "how much does bca cost": "fee",
    "what are the charges for bca": "fee",
    "what does bca cost": "fee",
    "bca payment details": "fee",
    "bca fee": "fee",
    "eligibility for bca": "eligibility",
    "am i eligible for bca": "eligibility",
    "can i apply to bca": "eligibility",
    "admission requirements for bca": "eligibility",
    "what are the requirements for bca": "eligibility",
    "bca major subjects": "subjects",
    "major subjects of bca": "subjects",
    "subjects in bca": "subjects",
    "bca minor subjects": "minors",
    "semester subjects of bba": "semesters",
    "bca semester 3 subjects": "semester_subjects",
    "bca semester 3": "semester_subjects",
    "semester 4 of mca": None,  # MCA only has semesters 1–2 -> data gate
    "credits for bca": "credits",
    "credit structure of bca": "credits",
    "learning outcomes of bca": "outcomes",
    "bca learning outcomes": "outcomes",
    "tell me about bca": "overview",
    "what is bca": "overview",
    "syllabus of bca": None,  # no curriculum docs in seed -> falls through
    "vac courses": "vac",
    "show sec courses": "sec",
    "list aec courses": "aec",
    "nep": "scheme",
    "NEP 2020": "scheme",
    "new education policy": "scheme",
    "national education policy": "scheme",
    "tell me about NEP": "scheme",
    "what is the NEP": "scheme",
    "traditional": "scheme",
    "nep courses": "list",
    "what are NEP courses": "list",
    "fyugp courses": "list",
    "nep major subjects": "programme_pick",
    "nep semester structure": "scheme",  # scheme hub is the direct answer
    "nep credits": "programme_pick",
    "courses": "schemes",
    "list of ug courses": "schemes",
    "show pg programmes": "schemes",
}


def test_detect_ops():
    print("-- C. catalogue detect (direct ops) --")
    ctx = ConversationContext()
    for raw, expected in sorted(_DIRECT_OPS.items()):
        e = extract_entities(raw)
        req = detect.detect_catalogue_request(raw, ctx, e)
        op = req.get("op") if req else None
        check(f"op {raw!r} -> {expected}", op == expected, f"got={op} req={req}")

    # False positives must stay None (requirement 12: preserve other flows).
    for raw in ["good morning", "what is the timetable", "pls share exam schedule",
                "where is the library", "who is the principal", "how to contact the office"]:
        req = detect.detect_catalogue_request(raw, ctx, extract_entities(raw))
        check(f"no false positive {raw!r}", req is None, f"got={req}")

    # Data-gated: semester 4 does not exist for BCA -> fall through.
    req = detect.detect_catalogue_request("bca semester 4", ctx, extract_entities("bca semester 4"))
    check("data gate (bca sem 4)", req is None, f"got={req}")


# ---------------------------------------------------------------------------
# D. Planner routing: services, clarification, structured-over-RAG priority
# ---------------------------------------------------------------------------


def _plan(raw: str, ctx: ConversationContext | None = None):
    from app.orchestrator.planner import plan
    ctx = ctx or ConversationContext()
    e = extract_entities(raw)
    return plan(raw, ctx, f"intel-{uuid.uuid4()}", e)


_PLANNER_CASES = {
    # service synonyms -> connectors (requirement 2 / 10)
    "show my attendance": ("connector", "attendance"),
    "my presence percentage": ("connector", "attendance"),
    "attendance percentage": ("connector", "attendance"),
    "show my sgpa": ("connector", "results"),
    "what is my cgpa": ("connector", "results"),
    "my grades for last semester": ("connector", "results"),
    "marks of semester 3": ("connector", "results"),
    "download admit card": ("connector", "admit_card"),
    "hall ticket status": ("connector", "admit_card"),
    "get my transcript": ("connector", "transcript"),
    "helpdesk": ("connector", "helpdesk"),
    "fee receipt": ("connector", "fee"),
    # clarification (requirement 7): missing programme -> slot question
    "fee": ("slot_fill", "programme"),
    "eligibility": ("slot_fill", "programme"),
    "how much": ("slot_fill", "programme"),
    # structured data beats RAG (requirement 8)
    "fee structure of bca": ("catalogue", "fee"),
    "eligibility of mca": ("catalogue", "eligibility"),
    "major subjects of bca": ("catalogue", "subjects"),
    "nep": ("catalogue", "scheme"),
    "bca semester 3": ("catalogue", "semester_subjects"),
    "credits for bba": ("catalogue", "credits"),
    "bca learning outcomes": ("catalogue", "outcomes"),
    "bcaa fee": ("catalogue", "fee"),
    "feestructure for bca": ("catalogue", "fee"),
    "eligibilty for mba": ("catalogue", "eligibility"),
}


def test_planner():
    print("-- D. planner routing --")
    for raw, (action, target) in sorted(_PLANNER_CASES.items()):
        p = _plan(raw)
        ok = p.action == action and p.target == target
        check(
            f"plan {raw!r} -> {action}/{target}",
            ok,
            f"got action={p.action} target={p.target}",
        )


# ---------------------------------------------------------------------------
# E. Context memory: follow-ups + smart clarification
# ---------------------------------------------------------------------------

_CONTEXT_SCENARIOS = [
{
        "name": "fee follow-up after programme",
        "steps": [
            ("fee structure of bca", "catalogue", "fee"),
            ("how much", "catalogue", "fee"),   # programme from memory
            ("and eligibility", "catalogue", "eligibility"),
        ],
    },
    {
        "name": "semester follow-up",
        "steps": [
            ("bca semester 3", "catalogue", "semester_subjects"),
            ("subjects", "catalogue", "subjects"),  # programme remembered
        ],
    },
    {
        "name": "NEP hub then scheme exploration",
        "steps": [
            ("nep", "catalogue", "scheme"),
            ("nep courses", "catalogue", "list"),   # scheme from memory
        ],
    },
    {
        "name": "bare fee with programme memory",
        "steps": [
            ("tell me about bca", "catalogue", "overview"),
            ("fee", "catalogue", "fee"),
        ],
    },
    {
        "name": "semester number with programme memory",
        "steps": [
            ("mca", "catalogue", "overview"),       # programme switch -> overview
            ("semester 2", "catalogue", "semester_subjects"),
        ],
    },
    {
        "name": "service memory does not leak into catalogue",
        "steps": [
            ("show my attendance", "connector", "attendance"),
            ("attendance", "connector", "attendance"),
        ],
    },
]


def test_context_memory():
    print("-- E. context memory --")
    from app.orchestrator.planner import plan

    for scenario in _CONTEXT_SCENARIOS:
        ctx = ConversationContext()
        chat = f"intel-{uuid.uuid4()}"
        for step_idx, (raw, action, target) in enumerate(scenario["steps"]):
            e = extract_entities(raw)
            p = plan(raw, ctx, chat, e)
            ok = p.action == action and (target is None or p.target == target)
            check(
                f"{scenario['name']} step{step_idx + 1} {raw!r}",
                ok,
                f"got action={p.action} target={p.target}",
            )


# ---------------------------------------------------------------------------
# F. Engine end-to-end flows (small but real)
# ---------------------------------------------------------------------------

_E2E_FLOWS = [
    {
        "name": "nep hub opens",
        "steps": [
            ("nep", "NEP 2020 Curriculum"),
            ("scheme:list", "NEP 2020 Curriculum"),          # hub -> programme list
            (None, None),                                    # placeholder
        ],
    },
    {
        "name": "fee direct answer",
        "steps": [
            ("fee structure of bca", "Fee Structure"),
        ],
    },
    {
        "name": "eligibility direct answer",
        "steps": [
            ("am i eligible for bca", "Eligibility"),
        ],
    },
    {
        "name": "semester direct answer",
        "steps": [
            ("bca semester 3", "Semester 3"),
        ],
    },
    {
        "name": "major subjects direct answer",
        "steps": [
            ("major subjects of bca", "Major Subjects"),
        ],
    },
    {
        "name": "overview",
        "steps": [
            ("tell me about bca", "Academic Catalogue"),
        ],
    },
    {
        "name": "generic courses -> scheme picker",
        "steps": [
            ("courses", "Academic Scheme"),
        ],
    },
]


def _e2e_flow(name: str, steps: list) -> None:
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

        def ask(msg):
            return asyncio.run(_ask(msg))

        def found(evs, needle):
            return any(needle.lower() in (e.get("title") or "").lower() for e in evs)

        # remove placeholder steps
        steps = [s for s in steps if s[0] is not None]
        # When a flow starts at the hub, skip the empty placeholder at the end
        # and instead drive the continuation inside the flow.
        for idx, (raw, expected) in enumerate(steps):
            evs = ask(raw)
            if expected is None:
                continue
            check(
                f"e2e {name} [{idx + 1}] {raw!r} -> {expected!r}",
                found(evs, expected),
                f"got={[(e.get('type'), e.get('title')) for e in evs if e.get('type') in ('options', 'detail')]}",
            )
    finally:
        db.close()


def test_engine_e2e():
    print("-- F. engine end-to-end --")
    _e2e_flow("nep hub", [
        ("nep", "NEP 2020 Curriculum"),
        ("scheme:list", "NEP 2020 Curriculum"),
    ])
    for flow in _E2E_FLOWS[1:]:
        _e2e_flow(flow["name"], flow["steps"])


# ---------------------------------------------------------------------------
# G. Full planner battery on realistic multi-word phrasings
# ---------------------------------------------------------------------------

_FULL_BATTERY = [
    # (query, action, target) — planner-level
    ("I want to know the fee structure of BCA", "catalogue", "fee"),
    ("Can you tell me the fees for MCA?", "catalogue", "fee"),
    ("What is the tuition fee for BBA?", "catalogue", "fee"),
    ("How much do I need to pay for BCA?", "catalogue", "fee"),
    ("Please share the payment details for MBA", "catalogue", "fee"),
    ("What are the charges for BA English?", "catalogue", "fee"),
    ("Cost of the BCA programme", "catalogue", "fee"),
    ("Is there any admission fee for M.Com?", "catalogue", "fee"),
    ("Am I eligible for PhD in CS?", "catalogue", "eligibility"),
    ("Who can apply for MCA?", "catalogue", "eligibility"),
    ("What are the eligibility criteria for B.Com?", "catalogue", "eligibility"),
    ("Minimum qualification for MBA", "catalogue", "eligibility"),
    ("Can I join B.Sc with 45%?", "catalogue", "eligibility"),
    ("What are the admission requirements for BBA?", "catalogue", "eligibility"),
    ("Show me the major subjects of BCA", "catalogue", "subjects"),
    ("List the main papers in MCA", "catalogue", "subjects"),
    ("What modules are taught in BCA?", "catalogue", "subjects"),
    ("Subjects offered in BA English", "catalogue", "subjects"),
    ("What are the minor disciplines in BCA?", "catalogue", "minors"),
    ("Minor subjects for MBA", "catalogue", "minors"),
    ("Semester-wise subjects of B.Com", "catalogue", "semesters"),
    ("Subjects semester wise for MCA", "catalogue", "semesters"),
    ("What subjects are there in semester 2 of BCA?", "catalogue", "semester_subjects"),
    ("3rd semester subjects of MCA", "catalogue", "semester_subjects"),
    ("Credits required for BCA", "catalogue", "credits"),
    ("How many credits does BBA have?", "catalogue", "credits"),
    ("Total credits in the MCA programme", "catalogue", "credits"),
    ("Learning outcomes of the BCA programme", "catalogue", "outcomes"),
    ("What will I learn in BBA?", "catalogue", "overview"),
    ("Give me details about BCA", "catalogue", "overview"),
    ("Explain the BCA programme", "catalogue", "overview"),
    ("What is FYUGP?", "catalogue", "scheme"),
    ("Explain the New Education Policy", "catalogue", "scheme"),
    ("Tell me about NEP 2020", "catalogue", "scheme"),
    ("What does the NEP mean?", "catalogue", "scheme"),
    ("Show me the programmes under NEP", "catalogue", "list"),
    ("Which courses come under the NEP?", "catalogue", "list"),
    ("List the VAC courses", "catalogue", "vac"),
    ("Which SEC courses are available?", "catalogue", "sec"),
    ("What AEC courses exist?", "catalogue", "aec"),
    ("Show me VAC courses in BCA", "catalogue", "vac"),
    ("Where can I check my attendance?", "connector", "attendance"),
    ("My attendance percentage", "connector", "attendance"),
    ("Check my presence record", "connector", "attendance"),
    ("I need my result", "connector", "results"),
    ("Show my marks card", "connector", "results"),
    ("What did I score last semester?", "connector", "results"),
    ("My SGPA for this semester", "connector", "results"),
    ("CGPA of my programme", "connector", "results"),
    ("Download my admit card", "connector", "admit_card"),
    ("Get my hall ticket", "connector", "admit_card"),
    ("I want to apply for exam form", "connector", "exam_form"),
    ("Examination form last date", "connector", "exam_form"),
    ("Submit my re evaluation request", "connector", "re_evaluation"),
    ("Get my degree certificate", "connector", "degree"),
    ("Check my backlog status", "connector", "backlog"),
    ("Request a migration certificate", "connector", "migration"),
    ("Where is the helpdesk?", "connector", "helpdesk"),
    ("I need a copy of my transcript", "connector", "transcript"),
    ("Get my fee receipt", "connector", "fee"),
    ("How do I register for courses?", "connector", "registration"),
    ("Show my student profile", "connector", "profile"),
    # bare / ambiguous -> clarification
    ("What are the fees?", "slot_fill", "programme"),
    ("Tell me about fees", "slot_fill", "programme"),
    ("Eligibility details", "slot_fill", "programme"),
    ("Cost?", "slot_fill", "programme"),
    # preserved flows (no catalogue false positives)
    ("What is the timetable?", None, None),
    ("Exam schedule please", None, None),
    ("Tell me about the library", None, None),
    ("Contact details of the university", None, None),
]


def test_full_battery():
    print("-- G. full planner battery --")
    for raw, action, target in _FULL_BATTERY:
        p = _plan(raw)
        if action is None:
            ok = p.action not in ("catalogue", "connector")
            check(f"preserve {raw!r}", ok, f"got action={p.action}")
            continue
        ok = p.action == action and p.target == target
        check(f"battery {raw!r} -> {action}/{target}", ok, f"got action={p.action} target={p.target}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    t0 = time.time()
    _ensure_seeded()
    test_query_understanding()
    test_extractor()
    test_detect_ops()
    test_planner()
    test_context_memory()
    test_engine_e2e()
    test_full_battery()
    print(f"\nTotal checks: {len(PASS) + len(FAIL)} | Passed: {len(PASS)} | Failed: {len(FAIL)} "
          f"| time: {time.time() - t0:.1f}s")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)


_ensure_seeded()  # pytest mode parity: tables + catalogue seed (idempotent)

if __name__ == "__main__":
    main()