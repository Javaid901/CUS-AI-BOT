"""
backend/tests/test_intent_matrix.py

Universal intent matrix for the CUS AI Assistant:

  1. Greeting / courtesy routing (planner Rule 2c)
  2. Grievance detection (EN + Hinglish + misspellings, detect + planner)
  3. Authority service queries (planner Rule 3c + alias routing)
  4. Useful fallback builder (chat/fallback.py) — no dead-ends, real cards only
  5. Grievance authority matching (service.match_for_grievance incl. alias
     fallback, unavailable/inactive guard, gibberish)
  6. Typo / Hinglish / multi-intent sanity — nothing crashes, nothing routes
     to a dead-end.

Run:  python tests/test_intent_matrix.py   (or via pytest)
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register tables before any session)

from app.database import SessionLocal, create_all
from app.orchestrator.context import ConversationContext
from app.orchestrator.extractor import extract_entities

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
    _ensure_authority_seeds()


def _ensure_authority_seeds():
    """Insert the offices the authority/fallback tests rely on.

    Idempotent; mirrors the pattern of test_smart_orchestrator.py. One record
    (Student Welfare Office) is deliberately INACTIVE so the unavailable path
    is exercised with a real record.
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
            email="coe@example.edu",
            phone="0194-0000001",
            keywords='["exams", "examination", "exam", "coe"]',
            services_offered='["results", "datesheet", "hall tickets"]',
            description="The Controller of Examinations handles examinations, results and datesheets.",
        ),
        Authority(
            department_name="Dean Science",
            authority_name="Dean Science",
            designation="Dean",
            email="dean.science@example.edu",
            phone="0194-0000002",
            keywords='["science", "faculty of science"]',
            services_offered='["academic matters"]',
            description="Office of the Dean, Faculty of Science.",
        ),
        Authority(
            department_name="Admissions",
            authority_name="Admissions Office",
            designation="Admissions Officer",
            email="admissions@example.edu",
            phone="0194-0000003",
            keywords='["admission", "admissions", "eligibility"]',
            services_offered='["admission counselling", "eligibility"]',
            description="Central admissions cell for UG, PG and PhD programmes.",
        ),
        Authority(
            department_name="Finance",
            authority_name="Finance Officer",
            designation="Finance Officer",
            email="finance@example.edu",
            phone="0194-0000004",
            keywords='["fee", "fees", "payment"]',
            services_offered='["fee payment", "refund"]',
            description="Handles fee payments, receipts and refunds.",
        ),
        Authority(
            department_name="Student Welfare",
            authority_name="Student Welfare Office",
            designation="Welfare Officer",
            email="welfare@example.edu",
            phone="0194-0000005",
            active=False,
            keywords='["welfare", "scholarship"]',
            services_offered='["scholarships", "student support"]',
            description="Student welfare and scholarship support (currently inactive).",
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
    return plan(raw, ctx, f"im-{uuid.uuid4()}", e)


# ---------------------------------------------------------------------------
# 1. Greeting / courtesy
# ---------------------------------------------------------------------------

_GREETING_CASES = [
    "Hello",
    "hi",
    "Hey there",
    "Hi!",
    "Assalamualaikum",
    "assalamu alaikum",
    "Salam",
    "good morning",
    "good afternoon",
    "evening",
    "namaste",
    "hello ji",
    "how are you",
    "kaise ho",
    "kya haal hai",
]

_COURTESY_CASES = ["thank you", "Thanks!", "thank u", "shukriya"]

_NOT_GREETING_CASES = [
    "hi what is the mca fee",          # greeting + real question -> normal flow
    "hello, tell me about bca",        # greeting + request -> normal flow
    "assalamualaikum sir result kab aayega",  # informative intent wins
    "good morning everyone",           # > 4 tokens, not pure greeting
]


def test_greeting_routing():
    print("-- 1. greeting / courtesy routing --")
    for raw in _GREETING_CASES:
        p = _plan(raw)
        ok = p.action == "greeting" and (p.extra or {}).get("kind") in ("greeting", "courtesy")
        check(f"greeting {raw!r} -> greeting", ok, f"got action={p.action} extra={p.extra}")
    for raw in _COURTESY_CASES:
        p = _plan(raw)
        ok = p.action == "greeting" and (p.extra or {}).get("kind") == "courtesy"
        check(f"courtesy {raw!r} -> courtesy", ok, f"got action={p.action} extra={p.extra}")
    for raw in _NOT_GREETING_CASES:
        p = _plan(raw)
        check(f"not greeting {raw!r} (flows normally)", p.action not in ("greeting", "welcome"),
              f"got action={p.action} extra={p.extra}")


# ---------------------------------------------------------------------------
# 2. Grievance detection (English + Hinglish + misspellings)
# ---------------------------------------------------------------------------

_GRIEVANCE_DETECT_CASES = [
    "I want to file a complaint",
    "I have a grievance about my result",
    "my result is not showing",
    "I haven't received my admit card",
    "marks are wrong on my transcript",
    "meri complaint hai",
    "mujhe shikayat hai about the fees",
    "complaint file karni hai admission ke liye",
    "result nahi aa raha",
    "mera fee refund nahi mila",
    "charge zyada lag gaya",
    "my portal login nahi ho raha",
]

_NON_GRIEVANCE_CASES = [
    "how do I file a grievance",
    "where can I check my result",
    "when will the examination start",
    "what is the admission process",
]


def test_grievance_detection():
    print("-- 2. grievance detection (planner + detector) --")
    from app.grievance.detect import detect_grievance

    for raw in _GRIEVANCE_DETECT_CASES:
        det = detect_grievance(raw)
        check(f"detector {raw!r}", det["is_grievance"], f"reason={det['reason']} marker={det['marker']}")
        p = _plan(raw)
        check(f"plan {raw!r} -> grievance", p.action == "grievance", f"got action={p.action} reason={p.reason}")
    for raw in _NON_GRIEVANCE_CASES:
        det = detect_grievance(raw)
        check(f"not grievance {raw!r}", not det["is_grievance"], f"reason={det['reason']}")
    # misspelled marker still detected on the raw message
    det = detect_grievance("i have a grivance about my fees")
    check("misspelled 'grivance' detected", det["is_grievance"], f"reason={det['reason']}")
    det = detect_grievance("result not showing")
    check("short-but-real complaint (result not showing)", det["is_grievance"], f"reason={det['reason']}")


# ---------------------------------------------------------------------------
# 3. Authority service queries
# ---------------------------------------------------------------------------

_AUTHORITY_PLAN_CASES = [
    "who is the registrar",
    "who handles examinations",
    "who should I contact about my result",
    "contact the controller of examinations",
    "who is the dean of science",
]


def test_authority_planning():
    print("-- 3. authority service queries --")
    for raw in _AUTHORITY_PLAN_CASES:
        p = _plan(raw)
        check(f"authority {raw!r}", p.action == "authority", f"got action={p.action} reason={p.reason}")


# ---------------------------------------------------------------------------
# 4. Useful fallback builder
# ---------------------------------------------------------------------------

def test_fallback_builder():
    print("-- 4. useful fallback builder --")

    def _fb(msg, base_text=None):
        from app.chat.fallback import build_fallback_response
        return build_fallback_response(msg, base_text=base_text)

    r = _fb("xyzzy qwerty nonsense")
    check("gibberish: text present", bool(r.get("text") and r["text"].strip()), r.get("text"))
    check("gibberish: options present", r.get("options", {}).get("type") == "options", str(r))
    check("gibberish: no invented card", r.get("card") is None, str(r))
    check("gibberish: no_back set", r["options"].get("no_back") is True, str(r["options"]))

    r = _fb("I want to speak to someone about my result please")
    card = r.get("card") or {}
    check("escalation+alias -> real card", r.get("card") is not None, str(r))
    check("card is the Controller of Examinations", card.get("title") == "Controller of Examinations", str(card))
    check("card has real email", "email" in str(card.get("fields") or []), str(card))
    check("card supplies grievance action", any(("grievance_" in str(a.get("id", ""))) for a in (card.get("actions") or [])), str(card))

    r = _fb("base text", base_text="Canned base text.")
    check("base_text respected", r["text"].startswith("Canned base text."), r["text"])


# ---------------------------------------------------------------------------
# 5. Grievance authority matching (service level)
# ---------------------------------------------------------------------------

def test_match_for_grievance():
    print("-- 5. grievance authority matching --")
    from app.authority.service import authority_service

    db = SessionLocal()
    try:
        r = authority_service.match_for_grievance(db, "I want to complain about my result")
        check("result complaint -> CoE (alias)", r.get("status") == "matched"
              and r["authority"].get("authority_name") == "Controller of Examinations", str(r))

        r = authority_service.match_for_grievance(db, "I have a grievance with the examination branch")
        check("examination branch -> CoE (alias)", r.get("status") == "matched"
              and r["authority"].get("authority_name") == "Controller of Examinations", str(r))

        r = authority_service.match_for_grievance(db, "i have a fee complaint")
        check("fee complaint -> Finance (alias)", r.get("status") == "matched"
              and r["authority"].get("authority_name") == "Finance Officer", str(r))

        r = authority_service.match_for_grievance(db, "complaint about my admission process")
        check("admission complaint -> Admissions (alias)", r.get("status") == "matched"
              and r["authority"].get("authority_name") == "Admissions Office", str(r))

        r = authority_service.match_for_grievance(db, "I want to complain to the Dean of Science")
        check("Dean of Science named -> matched", r.get("status") == "matched"
              and r["authority"].get("authority_name") == "Dean Science", str(r))

        r = authority_service.match_for_grievance(db, "I have a complaint about student welfare")
        check("inactive record -> unavailable (never auto-matched)", r.get("status") == "unavailable"
              and "Student Welfare Office" in r.get("names", []), str(r))

        r = authority_service.match_for_grievance(db, "zzz qqq ww")
        check("gibberish -> none", r.get("status") == "none", str(r))

        r = authority_service.match_for_grievance(db, "hi")
        check("too short -> none", r.get("status") == "none", str(r))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 6. Typo / Hinglish / multi-intent sanity
# ---------------------------------------------------------------------------

def test_multi_intent_and_typos():
    print("-- 6. typo / Hinglish / multi-intent sanity --")

    from app.grievance.detect import detect_grievance

    p = _plan("grivance about my fee")
    check("typo 'grivance' -> grievance", p.action == "grievance", f"got action={p.action} reason={p.reason}")

    p = _plan("mujhe admission kaise milega")
    check("hinglish admission query -> normal flow", p.action not in ("greeting", "welcome"),
          f"got action={p.action}")

    p = _plan("fees kitni hai bca ki")
    check("hinglish fee query -> catalogue/slot_fill/rag", p.action in ("catalogue", "slot_fill", "rag", "connector"),
          f"got action={p.action} reason={p.reason}")

    p = _plan("meri complaint hai aur mujhe mca ke baare mein bhi jaanna hai")
    check("multi-intent complaint -> grievance flow", p.action == "grievance",
          f"got action={p.action} reason={p.reason}")

    det = detect_grievance("i want to know the mca fee but my admission is stuck")
    check("multi-intent (problem with admission) -> grievance", det["is_grievance"], f"reason={det['reason']}")
    e = extract_entities("i want to know the mca fee but my admission is stuck")
    check("multi-intent keeps entities (mca)", getattr(e, "programme", None) == "mca", str(getattr(e, "programme", None)))

    p = _plan("tell me about your university")
    check("generic question -> no dead-end", p.action not in ("greeting", "welcome", "grievance"),
          f"got action={p.action} target={p.target}")


# ---------------------------------------------------------------------------
# 7. Engine-level end-to-end (greeting / courtesy / grievance via engine.process)
# ---------------------------------------------------------------------------

def test_engine_smart_flows():
    print("-- 7. engine e2e: greeting / courtesy / grievance --")
    import asyncio

    from app.orchestrator.engine import process

    db = SessionLocal()
    try:
        def run(msg):
            events = []
            async def go():
                async for ev in process(db, "u-matrix", msg, f"em-{uuid.uuid4()}"):
                    events.append(ev)
            asyncio.run(go())
            return events

        evs = run("Hi")
        types = [e.get("type") for e in evs]
        check("greeting: token -> options -> done", types == ["token", "options", "done"], str(types))
        txt = "".join(e.get("text", "") for e in evs if e.get("type") == "token")
        check("greeting: warm welcome text", "CUS AI Assistant" in txt, txt[:120])
        opts = next(e for e in evs if e.get("type") == "options")
        check("greeting: welcome chips offered", len(opts.get("options", [])) > 0, str(opts)[:200])

        evs = run("thank you")
        txt = "".join(e.get("text", "") for e in evs if e.get("type") == "token")
        check("courtesy: polite reply, not fallback", "welcome" in txt.lower(), txt[:120])

        evs = run("meri complaint hai")
        types = [e.get("type") for e in evs]
        check("grievance: token -> grievance -> done", types == ["token", "grievance", "done"], str(types))
        g = next(e for e in evs if e.get("type") == "grievance")
        check("grievance: prefill carries user words", "meri complaint hai" in (g.get("payload", {}).get("prefill") or ""), str(g))
        check("grievance: category suggested", bool(g.get("payload", {}).get("category")), str(g))

        evs = run("xyzzy qwerty nonsense")
        types = [e.get("type") for e in evs]
        txt = "".join(e.get("text", "") for e in evs if e.get("type") == "token")
        check("unknown: no empty answer", bool(txt.strip()), txt[:120])
        check("unknown: fallback offers options", "options" in types, str(types))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    t0 = time.time()
    _ensure_seeded()
    test_greeting_routing()
    test_grievance_detection()
    test_authority_planning()
    test_fallback_builder()
    test_match_for_grievance()
    test_multi_intent_and_typos()
    test_engine_smart_flows()
    print(f"\nTotal checks: {len(PASS) + len(FAIL)} | Passed: {len(PASS)} | "
          f"Failed: {len(FAIL)} | time: {time.time() - t0:.1f}s")
    if FAIL:
        print("FAILED:", FAIL[:10])
        sys.exit(1)


_ensure_seeded()  # pytest-mode parity (idempotent)

if __name__ == "__main__":
    main()