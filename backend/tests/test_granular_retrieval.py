"""
backend/tests/test_granular_retrieval.py

Acceptance battery for the Precise, Granular Information Retrieval layer.

Covers (spec §24):

  * direct field answers      — fee / eligibility / duration / credits /
                                scheme / subjects / minors / VAC/SEC/AEC /
                                outcomes / documents, without menu detours
  * field extraction          — "how long", "documents", "how much", ...
  * compound questions        — "fee and eligibility of bca", "duration,
                                credits and subjects of bca"
  * context & switching       — BCA -> fee -> eligibility (context reuse)
  * typos                     — fee structure / eligibilty / subjcts /
                                crdits / curiculam / durration
  * fallback chain            — structured -> legacy -> (engine) RAG cascade
                                when a field is not published
  * regression               — single-field ops keep their existing routes

Run:  python tests/test_granular_retrieval.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register catalogue tables before any session)

from app.database import SessionLocal, create_all
from app.orchestrator.context import ConversationContext
from app.orchestrator.extractor import extract_entities

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  {detail}" if detail else ""))
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


def _bca_prog():
    from app.catalogue.service import resolve_programme, get_programme
    resolved = resolve_programme("bca")
    if not resolved:
        return None
    return get_programme(resolved["id"])


def _req(text, ctx=None, entities=None):
    from app.catalogue import detect
    ctx = ctx or ConversationContext()
    e = entities or extract_entities(text)
    return detect.detect_catalogue_request(text, ctx, e)


def test_detection():
    print("-- granular detection routing --")
    cases = {
        # regression: single-field dedicated ops keep their existing route
        "fee structure of bca": ("fee", None),
        "credits of bca": ("credits", None),
        "subjects of bca": ("subjects", None),
        "bca major subjects": ("subjects", None),
        "bca minor subjects": ("minors", None),
        "vac courses in bca": ("vac", None),
        "bca under nep": ("overview", None),
        # new granular fields (no dedicated op)
        "duration of bca": ("requested", ["duration"]),
        "how long is the bca course": ("requested", ["duration"]),
        "what documents are required for bca admission": ("requested", ["documents"]),
        "schemes of bca": ("requested", ["scheme"]),
        "which scheme does bca follow": ("requested", ["scheme"]),
        # compound questions
        "what is the fee and eligibility for bca": ("requested", ["fee", "eligibility"]),
        "duration, credits and subjects of bca": ("requested", ["duration", "credits", "subjects"]),
        "subjects and minors of bca": ("requested", ["subjects", "minor"]),
        "what are the fee, duration and total credits of bca": ("requested", ["fee", "duration", "credits"]),
    }
    for text, (expected_op, expected_fields) in cases.items():
        req = _req(text)
        op = req.get("op") if req else None
        ok_op = op == expected_op
        ok_fields = True
        if expected_fields is not None:
            ok_fields = req and req.get("fields") == expected_fields
        check(f"detect {text!r} -> {expected_op}/{expected_fields}",
              ok_op and ok_fields, f"op={op} fields={req and req.get('fields')}")

    # non-catalogue messages still pass through untouched
    for text in ("good morning", "pls share the timetable"):
        req = _req(text)
        check(f"unrelated {text!r} stays out", req is None, f"req={req}")


def test_extraction():
    print("-- knowledge.extract_requested_fields --")
    from app.catalogue.knowledge import extract_requested_fields

    cases = {
        "how much does bca cost": ["fee"],
        "bca fee structure": ["fee"],
        "eligibility for bca": ["eligibility"],
        "who can apply for bca": ["eligibility"],
        "how many years is bca": ["duration"],
        "how long is bca": ["duration"],
        "total credits in bca": ["credits"],
        "nep scheme of bca": ["scheme"],
        "subjects in bca": ["subjects"],
        "major subjects in bca": ["subjects"],
        "minor subjects in bca": ["minor"],
        "vac courses": ["vac"],
        "what documents are required": ["documents"],
        "bca learning outcomes": ["outcomes"],
        "curriculum of bca": ["curriculum"],
        "fee and eligibility": ["fee", "eligibility"],
        "duration and credits": ["duration", "credits"],
        "subjects and minors": ["subjects", "minor"],
        "fee and subjects": ["fee", "subjects"],
        "tell me about bca": [],
    }
    for text, expected in cases.items():
        got = extract_requested_fields(text)
        check(f"extract {text!r}", got == expected, f"got={got}")


def test_resolution():
    print("-- knowledge.resolve_information_request (structured tier) --")
    from app.catalogue.knowledge import resolve_information_request

    prog = _bca_prog()
    check("BCA resolvable in catalogue", prog is not None)
    if not prog:
        return

    found, missing = resolve_information_request(None, prog, ["fee", "eligibility"])
    check("fee resolved from catalogue", "fee" in found and bool(found["fee"].get("content")), f"found={found}")
    check("fee source is the catalogue", found.get("fee", {}).get("source") == "Academic Catalogue")
    check("eligibility resolved", "eligibility" in found and bool(found["eligibility"].get("content")))
    check("nothing missing", missing == [], f"missing={missing}")

    found2, missing2 = resolve_information_request(None, prog, ["duration", "credits", "scheme"])
    check("duration resolved", found2.get("duration", {}).get("content") and "years" in str(found2["duration"].get("content")), f"{found2.get('duration')}")
    check("credits resolved", bool(found2.get("credits", {}).get("content")), f"{found2.get('credits')}")
    check("scheme resolved", bool(found2.get("scheme", {}).get("content")), f"{found2.get('scheme')}")

    found3, missing3 = resolve_information_request(None, prog, ["subjects", "minor", "outcomes"])
    check("subjects rows", bool(found3.get("subjects", {}).get("rows")), f"{found3.get('subjects', {}).get('rows', [])[:1]}")
    check("minors resolved", bool(found3.get("minor", {}).get("content")), f"{found3.get('minor')}")
    check("outcomes resolved", bool(found3.get("outcomes", {}).get("content")), f"{found3.get('outcomes')}")

    # documents are normally not published in the catalogue -> missing flag
    found4, missing4 = resolve_information_request(None, prog, ["documents"])
    check("documents flagged missing (or answered)", bool(found4.get("documents")) or "documents" in missing4,
          f"found={found4} missing={missing4}")


def test_response_builder():
    print("-- responses.requested_response --")
    from app.catalogue import responses
    from app.catalogue.knowledge import resolve_information_request

    prog = _bca_prog()
    if not prog:
        return
    found, missing = resolve_information_request(None, prog, ["fee", "eligibility", "documents"])
    payload = responses.requested_response(prog, ["fee", "eligibility", "documents"], found, missing)
    labels = {f.get("label") for f in payload.get("fields", [])}
    values = " ".join(str(f.get("value")) for f in payload.get("fields", []))
    check("requested card is a detail card", payload.get("type") == "detail")
    check("fee row present", any("Fee" in lb for lb in labels), f"labels={labels}")
    check("eligibility row present", any("Eligibility" in lb for lb in labels))
    check("card carries actual values (not a pointer)", bool(values) and "maintained in the academic catalogue" not in values.lower(),
          f"values={values[:120]}")
    check("missing flagged for cascade", payload.get("missing_fields") is not None, f"payload={payload}")


def test_engine_direct():
    print("-- engine e2e: direct granular answer (no cascade) --")
    from app.orchestrator.engine import process

    db = SessionLocal()
    try:
        user_id = str(uuid.uuid4())
        chat = str(uuid.uuid4())

        async def ask(msg):
            events = []
            async for ev in process(db, user_id, msg, chat):
                events.append(ev)
            return events

        evs = asyncio.run(ask("how long is the bca course"))
        details = [e for e in evs if e.get("type") == "detail"]
        dones = [e for e in evs if e.get("type") == "done"]
        check("duration ask -> detail card", any("Duration" in (e.get("title") or "") for e in details), f"titles={[e.get('title') for e in details]}")
        check("duration card carries years", any("years" in str(e.get("fields", [])).lower() for e in details), f"{[e.get('fields') for e in details]}")
        check("exactly one done (no RAG detour)", len(dones) == 1, f"n={len(dones)}")

        evs = asyncio.run(ask("what is the fee and eligibility for bca"))
        details = [e for e in evs if e.get("type") == "detail"]
        dones = [e for e in evs if e.get("type") == "done"]
        row_text = " ".join(str(f) for e in details for f in e.get("fields", []))
        check("compound ask -> single card", len(details) == 1, f"n={len(details)}")
        check("compound card has fee + eligibility", "Fee" in row_text and "Eligibility" in row_text, f"rows={row_text[:200]}")
        check("compound: one done, no RAG stream", len(dones) == 1 and not any(e.get("type") == "token" for e in evs),
              f"dones={len(dones)} tokens={[e.get('type') for e in evs]}")
    finally:
        db.close()


def test_engine_cascade():
    print("-- engine e2e: missing field cascades into RAG stream --")
    import app.catalogue.knowledge as knowledge
    import app.orchestrator.engine as engine_mod
    from app.orchestrator.engine import process

    db = SessionLocal()
    try:
        user_id = str(uuid.uuid4())
        chat = str(uuid.uuid4())

        # Pretend no legacy/upload records exist so "documents" is genuinely
        # missing and the cascade path runs (structured data still answers fee).
        orig_legacy, orig_upload = knowledge._lookup_legacy, knowledge._from_upload
        knowledge._lookup_legacy = lambda prog, topic: None
        knowledge._from_upload = lambda db, prog, *keys: None

        async def fake_run_chat(db, user_id, query, chat_id, context=None):
            yield {"type": "token", "content": f"[knowledge-base] {query}", "cited_chunks": []}
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}

        orig_run_chat = engine_mod.run_chat
        engine_mod.run_chat = fake_run_chat
        try:
            async def ask(msg):
                events = []
                async for ev in process(db, user_id, msg, chat):
                    events.append(ev)
                return events

            evs = asyncio.run(ask("what documents are required for bca admission"))
            details = [e for e in evs if e.get("type") == "detail"]
            tokens = [e for e in evs if e.get("type") == "token"]
            dones = [e for e in evs if e.get("type") == "done"]
            check("cascade: detail card first", len(details) >= 1, f"n={len(details)}")
            check("cascade: RAG stream followed", any("knowledge-base" in (t.get("content") or "") for t in tokens), f"tokens={[t.get('content') for t in tokens]}")
            check("cascade: stream ends with done", dones and evs[-1].get("type") == "done", f"last={evs[-1].get('type')}")
        finally:
            engine_mod.run_chat = orig_run_chat
    finally:
        knowledge._lookup_legacy, knowledge._from_upload = orig_legacy, orig_upload
        db.close()


def test_context_followup():
    print("-- context reuse: BCA -> fee -> eligibility -> duration --")
    from app.orchestrator.engine import process

    db = SessionLocal()
    try:
        user_id = str(uuid.uuid4())
        chat = str(uuid.uuid4())

        async def ask(msg):
            events = []
            async for ev in process(db, user_id, msg, chat):
                events.append(ev)
            return events

        evs = asyncio.run(ask("what is the fee for bca"))
        check("context: first ask -> fee card", any("Fee" in (e.get("title") or "") for e in evs if e.get("type") == "detail"))

        evs = asyncio.run(ask("what about eligibility?"))
        details = [e for e in evs if e.get("type") == "detail"]
        check("context: follow-up -> eligibility from context", any("Eligibility" in (e.get("title") or "") for e in details),
              f"titles={[e.get('title') for e in details]}")

        evs = asyncio.run(ask("and its duration?"))
        details = [e for e in evs if e.get("type") == "detail"]
        check("context: 'its' -> BCA duration", any("Duration" in (e.get("title") or "") for e in details),
              f"titles={[e.get('title') for e in details]}")
    finally:
        db.close()


def test_typos():
    print("-- spelling support for field words --")
    from app.orchestrator.query_understanding import process_query

    cases = {
        "feee structure of bca": "fee",
        "fee strcture of bca": "fee",
        "eligibilty of bca": "eligibility",
        "durration of bca": "duration",
        "crdits of bca": "credits",
        "subjcts of bca": "subjects",
        "curiculam of bca": "curriculum",
        "doccuments for bca admission": "documents",
        "semsters of bca": "semesters",
    }
    for raw, expected in cases.items():
        out = process_query(raw)
        corrected = getattr(out, "corrected_text", None) or out
        ok = expected in str(corrected).lower()
        check(f"typo {raw!r} -> {expected}", ok, f"corrected={corrected}")


def main():
    _ensure_seeded()
    print("-- Running granular information retrieval tests --")
    test_detection()
    test_extraction()
    test_resolution()
    test_response_builder()
    test_engine_direct()
    test_engine_cascade()
    test_context_followup()
    test_typos()

    print(f"\n{PASS.__len__()} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)


_ensure_seeded()  # pytest mode parity: tables + catalogue seed (idempotent)

if __name__ == "__main__":
    main()
