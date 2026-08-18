"""
backend/tests/test_catalogue.py

Tests for the NEP Academic Catalogue module:

  * detection            (catalogue.detect)         — routing decisions
  * programme resolution (catalogue.service)        — inline code/alias match
  * response builders    (catalogue.responses)      — options/detail shapes
  * backend ops          (catalogue.backend)        — picker + continuation
  * engine end-to-end    (orchestrator.engine)      — full chat pipeline

Run:  python tests/test_catalogue.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register catalogue tables before any session)

from app.database import SessionLocal
from app.orchestrator.context import ConversationContext
from app.orchestrator.extractor import extract_entities
from app.orchestrator.state import ConversationState

from app.catalogue import detect, responses, service

PASS = []
FAIL = []


def check(name, cond, detail=""):
    """Record + print a single assertion."""
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def _ensure_seeded():
    from app.database import create_all
    create_all()  # create missing tables + apply ALTER-table upgrades (scheme_id, eligibility, fee_structure)
    from app.catalogue.seed import seed_catalogue
    db = SessionLocal()
    try:
        # seed_catalogue always runs ensure_schemes() first (idempotent) so
        # existing databases also get the academic-scheme hierarchy.
        return seed_catalogue(db)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detection():
    print("-- catalogue.detect --")
    cases = {
        "list of ug courses": "schemes",
        "show pg programmes": "schemes",
        "nep courses": "list",
        "tell me about bca": "overview",
        "bca major subjects": "subjects",
        "semester subjects of bba": "semesters",
        "bca semester 2 subjects": "semester_subjects",
        "bca minor subjects": "minors",
        "credits for bca": "credits",
        "bca learning outcomes": "outcomes",
        "what are the vac courses": "vac",
        "show sec courses": "sec",
        "list aec courses": "aec",
        "pls share the timetable": None,
        "good morning": None,
        "what is the fee for bca": "fee",
    }
    for text, expected in cases.items():
        e = extract_entities(text)
        req = detect.detect_catalogue_request(text, ConversationContext(), e)
        op = req.get("op") if req else None
        check(f"detect {text!r} -> {op!r}", op == expected, f"expected {expected!r}")

    req = detect.detect_catalogue_request("tell me about bca", ConversationContext(), extract_entities("tell me about bca"))
    check("overview carries programme id", bool(req and req.get("programme")), f"req={req}")


def test_programme_resolution():
    print("-- catalogue.service.resolve_programme (inline refs) --")
    r = service.resolve_programme("show bca semester subjects")
    check("inline code resolves", bool(r and r.get("code") == "BCA"), f"got={r and r.get('code')}")
    r2 = service.resolve_programme("bachelor of business administration")
    check("full name resolves", bool(r2 and r2.get("code") == "BBA"), f"got={r2 and r2.get('code')}")


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


def test_response_shapes():
    print("-- catalogue.responses --")
    prog = service.list_programmes()[0]
    opts = responses.programme_list_response([prog], "ug")
    check("list is options payload", opts["type"] == "options" and len(opts["options"]) >= 1)
    ov = responses.overview_response(prog)
    check("overview is detail payload", ov["type"] == "detail" and len(ov.get("fields", [])) >= 2)
    sem_opt = responses.semester_options_response("BCA", [1, 2, 3])
    check("semester options ids", all(o["id"].startswith("semester:") for o in sem_opt["options"]))


# ---------------------------------------------------------------------------
# Backend ops (async, session-level)
# ---------------------------------------------------------------------------


async def _handle(db, state, request):
    from app.catalogue.backend import handle_catalogue
    return await handle_catalogue(db, None, "", "test-chat", state, request)


def _summarise(events):
    return [(e.get("type"), e.get("title", "")) for e in events]


def test_backend_ops():
    print("-- catalogue.backend --")
    db = SessionLocal()
    try:
        state = ConversationState(chat_id="sample-chat")
        prog = service.list_programmes()[0]

        out = asyncio.run(_handle(db, state, {"op": "list", "level": "ug"}))
        check("list op -> options", any(t == "options" for t, _ in _summarise(out)), f"{_summarise(out)}")

        out = asyncio.run(_handle(db, state, {"op": "overview", "programme": prog["id"]}))
        check("overview op -> detail", any(t == "detail" for t, _ in _summarise(out)), f"{_summarise(out)}")

        out = asyncio.run(_handle(db, state, {"op": "semesters", "programme": prog["id"]}))
        check("semesters sets continuation", state.catalogue_pending and state.catalogue_pending.get("op") == "semester_subjects")

        out = asyncio.run(_handle(db, state, {"op": "credits", "programme": prog["id"]}))
        check("credits op -> detail", any(t == "detail" for t, _ in _summarise(out)), f"{_summarise(out)}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Engine end-to-end
# ---------------------------------------------------------------------------


def test_engine_flows():
    print("-- engine end-to-end --")
    db = SessionLocal()
    try:
        user_id = str(uuid.uuid4())

        async def _ask(msg, chat=None):
            from app.orchestrator.engine import process
            events = []
            async for ev in process(db, user_id, msg, chat or str(uuid.uuid4())):
                events.append(ev)
            return events

        def ask(msg, chat=None):
            return asyncio.run(_ask(msg, chat))

        for msg in ["BCA major subjects", "credits for BCA", "BCA learning outcomes"]:
            evs = [e for e in ask(msg) if e.get("type") in ("options", "detail", "error")]
            check(f"engine {msg!r}", any(e.get("type") == "detail" for e in evs), f"types={[e.get('type') for e in evs]}")

        evs = [e for e in ask("list of ug courses") if e.get("type") in ("options", "detail", "error")]
        check("engine list -> options", any(e.get("type") == "options" for e in evs))

        # semester picker + continuation inside one conversation
        chat = str(uuid.uuid4())
        evs = ask("semester subjects of BCA", chat)
        sem_ids = [o["id"] for e in evs if e.get("type") == "options" for o in e.get("options", [])]
        check("semester picker produced options", any(oid.startswith("semester:") for oid in sem_ids), f"ids={sem_ids[:3]}")
        if any(oid.startswith("semester:") for oid in sem_ids):
            evs = [e for e in ask(sem_ids[0], chat) if e.get("type") in ("options", "detail", "error")]
            check("semester continuation -> detail", any(e.get("type") == "detail" for e in evs), f"types={[e.get('type') for e in evs]}")

        # minors picker + continuation
        chat2 = str(uuid.uuid4())
        evs = ask("BCA minor subjects", chat2)
        minor_ids = [o["id"] for e in evs if e.get("type") == "options" for o in e.get("options", [])]
        check("minors picker produced options", len(minor_ids) >= 1, f"ids={minor_ids[:2]}")
        if minor_ids:
            evs = [e for e in ask(minor_ids[0], chat2) if e.get("type") in ("options", "detail", "error")]
            check("minor continuation -> detail", any(e.get("type") == "detail" for e in evs), f"types={[e.get('type') for e in evs]}")
    finally:
        db.close()


def test_scheme_flows():
    print("-- academic scheme hierarchy --")
    schemes = service.list_academic_schemes()
    check("schemes seeded", len(schemes) >= 2, f"n={len(schemes)}")
    nep = next((s for s in schemes if s["code"] == "nep2020"), None)
    trad = next((s for s in schemes if s["code"] == "traditional"), None)
    check("NEP + Traditional schemes exist", bool(nep and trad))

    # detection: generic -> schemes picker, explicit -> list
    req = detect.detect_catalogue_request("courses", ConversationContext(), extract_entities("courses"))
    check("generic 'courses' -> schemes op", bool(req and req.get("op") == "schemes"), f"req={req}")
    req = detect.detect_catalogue_request("show nep courses", ConversationContext(), extract_entities("show nep courses"))
    check("explicit NEP -> list with scheme", bool(req and req.get("op") == "list" and req.get("scheme") == nep["id"]), f"req={req}")

    # scheme resolution aliases
    check("resolve 'cbcs' -> traditional", bool(service.resolve_academic_scheme("cbcs") and service.resolve_academic_scheme("cbcs")["code"] == "traditional"))
    check("resolve 'nep' -> nep2020", bool(service.resolve_academic_scheme("nep") and service.resolve_academic_scheme("nep")["code"] == "nep2020"))
    check("resolve by id", bool(service.academic_scheme_by_id(nep["id"])))

    # scheme-filtered listing
    nep_progs = service.list_programmes(scheme=nep["id"])
    trad_progs = service.list_programmes(scheme=trad["id"])
    check("NEP scheme filters programmes", len(nep_progs) >= 5, f"n={len(nep_progs)}")
    check("Traditional scheme filters programmes", len(trad_progs) >= 1, f"n={len(trad_progs)}")
    check("all NEP progs carry scheme fields", all(p.get("scheme_id") == nep["id"] and p.get("scheme_code") == "nep2020" for p in nep_progs))

    # levels derive from programmes per scheme
    trad_levels = sorted({(p.get("level") or "") for p in trad_progs} - {""})
    check("traditional levels", bool(trad_levels), f"levels={trad_levels}")

    # eligibility / fee data reachable
    prog = nep_progs[0]
    check("eligibility present", bool(prog.get("eligibility")), f"prog={prog.get('code')}")
    check("fee structure entries", bool(prog.get("fee_structure") and len(prog["fee_structure"]) >= 2), f"fees={prog.get('fee_structure')}")


def test_scheme_admin_crud():
    print("-- admin scheme CRUD (service) --")
    db = SessionLocal()
    try:
        created = service.create_academic_scheme(db, {"name": "Test Scheme", "code": "testscheme", "description": "temp", "sort_order": 99})
        check("create scheme", bool(created and created.get("id")), f"got={created}")
        check("create scheme code/name", created.get("code") == "testscheme" and created.get("name") == "Test Scheme")
        fetched = service.academic_scheme_by_id(created["id"], db=db)
        check("fetch scheme by id", bool(fetched and fetched["id"] == created["id"]))
        updated = service.update_academic_scheme(db, created["id"], {"name": "Test Scheme V2", "code": "testscheme2", "description": None, "sort_order": 99, "is_active": False})
        check("update scheme", bool(updated and updated["name"] == "Test Scheme V2" and updated["is_active"] is False), f"got={updated}")
        check("delete scheme (no links)", service.delete_academic_scheme(db, created["id"]) is True)
        check("gone after delete", service.academic_scheme_by_id(created["id"], db=db) is None)
    finally:
        db.close()


def _scheme_backend_chain():
    """Engine e2e: scheme picker -> level picker -> programme list -> menu continuation."""
    db = SessionLocal()
    try:
        schemes = service.list_academic_schemes()
        nep = next(s for s in schemes if s["code"] == "nep2020")
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

        def option_ids(evs):
            return [o["id"] for e in evs if e.get("type") == "options" for o in e.get("options", [])]

        def title_types(evs):
            return [(e.get("type"), e.get("title")) for e in evs if e.get("type") in ("options", "detail", "error")]

        # 1) generic course query -> scheme picker
        evs = ask("courses")
        scheme_ids = option_ids(evs)
        check("courses -> scheme picker", any(oid == nep["id"] for oid in scheme_ids), f"ids={scheme_ids[:4]}")

        # 2) pick NEP scheme -> level picker
        evs = ask(nep["id"])
        level_ids = option_ids(evs)
        check("scheme choice -> level picker", any(oid == "level:ug" for oid in level_ids), f"ids={level_ids[:4]}")

        # 3) pick UG level -> programme list
        evs = ask("level:ug")
        prog_ids = option_ids(evs)
        check("level choice -> programme list", len(prog_ids) >= 3, f"n={len(prog_ids)}")

        # 4) pick a programme -> programme menu (options card)
        evs = ask(prog_ids[0])
        menu_ids = option_ids(evs)
        check("programme -> menu options", any(mid.startswith("menu:") for mid in menu_ids), f"ids={menu_ids[:6]}")

        # 5) fee via menu
        evs = ask("menu:fee")
        fee_ok = any(e.get("type") == "detail" and "Fee" in (e.get("title") or "") for e in evs)
        check("menu fee -> detail", fee_ok, f"{title_types(evs)}")

        # 6) eligibility via menu
        evs = ask("menu:eligibility")
        elig_ok = any(e.get("type") == "detail" and "Eligibility" in (e.get("title") or "") for e in evs)
        check("menu eligibility -> detail", elig_ok, f"{title_types(evs)}")

        # 7) fresh chat: explicit scheme skips the picker
        chat2 = str(uuid.uuid4())

        async def _ask2(msg):
            from app.orchestrator.engine import process
            events = []
            async for ev in process(db, user_id, msg, chat2):
                events.append(ev)
            return events

        evs = asyncio.run(_ask2("show NEP courses"))
        check("explicit scheme -> list skip picker", any(e.get("type") == "options" for e in evs), f"{title_types(evs)}")
    finally:
        db.close()


def main():
    _ensure_seeded()
    print("-- Running academic catalogue tests --")
    test_detection()
    test_programme_resolution()
    test_response_shapes()
    test_backend_ops()
    _scheme_backend_chain()
    test_engine_flows()
    test_scheme_flows()
    test_scheme_admin_crud()

    print(f"\n{PASS.__len__()} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)


_ensure_seeded()  # pytest mode parity: tables + catalogue seed (idempotent)

if __name__ == "__main__":
    main()