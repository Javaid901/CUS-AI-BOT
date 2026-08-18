"""
backend/tests/test_grievance_intake.py

PHASE 4 — Public grievance intake tests.

Verifies (spec §19–§27):
  * INTENT: complaint statements trigger the intake; pure information
    queries do not (including typos and process questions)
  * DRAFT: AI formalization endpoint shape; deterministic fallback when the
    LLM is unavailable (manual editing continues, §25–§26)
  * RECOMMEND: only ACTIVE authorities are suggested; best-fit routing
  * SUBMIT: pre-login submission returns reference + one-time token;
    plaintext token is never stored (only SHA-256 digest); original and
    finalized texts preserved; immutable history entry; no PII in responses
  * VERIFY: requires BOTH reference and token; wrong/missing token fails
    closed (403); status-only payload, zero student PII
  * RATE LIMITS: per-IP sliding window on public endpoints (429)
  * EMAIL: disabled by default → submission succeeds, email_confirmed=false,
    no crash; enabled path via monkeypatched sender

Runs against the app's real DB (TestClient), cleaning up every created row.

Run:  python tests/test_grievance_intake.py   (or pytest tests/)
"""

from __future__ import annotations

import hashlib
import re
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register models before any session)

from fastapi.testclient import TestClient

from app.authority.service import authority_service
from app.database import SessionLocal, create_all
from app.grievance.models import Grievance, GrievanceStatusHistory
from app.grievance.detect import detect_grievance, suggest_category
from app.grievance.intake import (
    generate_public_reference,
    hash_tracking_token,
    new_tracking_token,
)
from app.main import app
from app.models import Authority

# Mirror app startup so the new grievance columns exist on the real DB
# (create_all + _upgrade_schema run idempotently).
create_all()

PASS: list[str] = []
FAIL: list[str] = []

_created_refs: list[str] = []
_created_authority_ids: list[str] = []
client = TestClient(app)

REF_RE = re.compile(r"^CUS-GRV-\d{4}-[0-9A-F]{8}$")


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def _fake_ip(suffix: int) -> dict[str, str]:
    return {"X-Forwarded-For": f"203.0.113.{suffix}"}


def _record_submission(reference: str) -> None:
    _created_refs.append(reference)


def _cleanup() -> None:
    db = SessionLocal()
    try:
        for ref in _created_refs:
            db.query(Grievance).filter(Grievance.reference == ref).delete()
        for aid in _created_authority_ids:
            db.query(Authority).filter(Authority.id == aid).delete()
        db.commit()
    finally:
        db.close()
        authority_service.load_cache(db)


def _purge_leftovers() -> None:
    """Remove rows left behind by earlier interrupted/pytest runs.

    Test rows are identified by their deterministic test markers
    (fixed reference, test email, test authority email prefix).
    """
    db = SessionLocal()
    try:
        db.query(Grievance).filter(
            (Grievance.reference.like("CUS-GRV-2099-%"))
            | (Grievance.student_email == "student@example.com")
        ).delete(synchronize_session=False)
        db.query(Authority).filter(
            Authority.email.like("exam-%@cus.ac.in")
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
        authority_service.load_cache(db)


# Purge rows left behind by earlier interrupted/pytest runs before testing.
_purge_leftovers()


@pytest.fixture(autouse=True)
def _auto_cleanup():  # noqa: ANN001
    """pytest teardown: always remove test rows (mirrors _main() cleanup)."""
    yield
    _cleanup()


def _make_authority(name: str = "Controller of Examinations", active: bool = True) -> str:
    db = SessionLocal()
    try:
        a = Authority(
            id=str(uuid.uuid4()),
            department_name="Examinations",
            authority_name=name,
            email=f"exam-{uuid.uuid4().hex[:6]}@cus.ac.in",
            phone="+91 12345 00000",
            keywords='["results", "admit card", "marksheet", "examination"]',
            services_offered='["Result queries", "Admit card issuance"]',
            active=active,
        )
        db.add(a)
        db.commit()
        _created_authority_ids.append(a.id)
        authority_service.load_cache(db)
        return a.id
    finally:
        db.close()


def _submit_payload(**overrides) -> dict:
    payload = {
        "student": {
            "name": "Test Student",
            "email": "student@example.com",
            "roll_number": "ABC123",
            "semester": "4",
            "college": "Amar Singh College",
            "programme": "bca",
            "phone": "+91 9876543210",
        },
        "original_input": "my admit card is missing and my exam is next week",
        "final_text": "My admit card has not been issued and my examination is scheduled next week. Kindly look into the matter at the earliest.",
        "category": "Examination & Results",
        "authority_id": None,
    }
    for k, v in overrides.items():
        payload[k] = v
    return payload


# ---------------------------------------------------------------------------
# 1. INTENT detection
# ---------------------------------------------------------------------------


def test_intent_detection():
    print("-- intent: grievance vs information query --")
    grievance_cases = [
        ("my admit card is missing", "missing"),
        ("I haven't received my marksheet", "not received"),
        ("i did not receive my result", "not received"),
        ("the marks on my grade card are wrong", "wrong"),
        ("problem with my scholarship payment", "problem with"),
        ("facing issue while logging into the portal", "facing issue"),
        ("fee was deducted twice", "deducted"),
        ("where is my admit card, i have not received it", "not received"),
        ("my result is not reflecting", "not reflecting"),
        ("i want to file a complaint about my hostel", "complaint"),
        ("havent received my admit card", "misspelled"),   # typo path
        ("my marksheet is mising", "misspelled"),
    ]
    for text, why in grievance_cases:
        d = detect_grievance(text)
        check(f"grievance: {text[:42]}", d["is_grievance"] is True, f"det={d}")

    info_cases = [
        "where can I check my result",
        "when will the admit card be available",
        "how do I apply for a transcript",
        "what is the fee structure",
        "how to file a grievance",
        "what is the grievance redressal process",
        "ok",
        "hi",
    ]
    for text in info_cases:
        d = detect_grievance(text)
        check(f"not grievance: {text[:42]}", d["is_grievance"] is False, f"det={d}")

    check("category hint: fee", suggest_category("my fee was deducted twice") == "Fees & Payments")
    check("category hint: results", suggest_category("result is wrong") == "Examination & Results")
    check("category fallback", suggest_category("random text") == "Other")


# ---------------------------------------------------------------------------
# 2. Reference + tracking token material
# ---------------------------------------------------------------------------


def test_reference_and_token_material():
    print("-- reference & one-time token material --")
    refs = [generate_public_reference() for _ in range(20)]
    check("reference format", all(REF_RE.match(r) for r in refs), refs[0])
    check("references unique", len(set(refs)) == len(refs))

    token = new_tracking_token()
    digest = hash_tracking_token(token)
    check("token is opaque & long", len(token) >= 24 and token != digest)
    check("digest deterministic", hash_tracking_token(token) == digest)
    check("plaintext never recoverable from digest", digest != token and token not in digest)


# ---------------------------------------------------------------------------
# 3. Authority recommendation (active only)
# ---------------------------------------------------------------------------


def test_recommendation_active_only():
    print("-- recommend: active authorities only --")
    active_id = _make_authority("Controller of Examinations", active=True)
    inactive_id = _make_authority("Controller of Examinations (inactive)", active=False)
    active = None
    for m in authority_service.list_active():
        if m["id"] == active_id:
            active = m
    check("active authority cached", active is not None)
    check("inactive authority excluded from cache", all(m["id"] != inactive_id for m in authority_service.list_active()))

    r = client.post("/api/grievances/recommend", json={"input": "my results are wrong, please help"}, headers=_fake_ip(31))
    check("recommend endpoint 200", r.status_code == 200, str(r.text[:200]))
    body = r.json()
    top = body.get("authority") or {}
    # Any real seeded authority may outrank the test one — what matters is
    # that the recommendation is a LIVE authority and never the inactive one.
    active_ids = {m["id"] for m in authority_service.list_active()}
    check("recommendation is an active authority", top.get("authority_id") in active_ids, str(top))
    check("inactive authority never recommended",
          top.get("authority_id") != inactive_id and
          all(a.get("authority_id") != inactive_id for a in body.get("alternatives", [])),
          str(body.get("alternatives", [])))
    check("recommend payload has stable keys",
          {"authority_id", "authority_name", "department_name", "email", "match_score"} <= set(top.keys()))


# ---------------------------------------------------------------------------
# 4. Draft generation (mocked LLM) + fallback
# ---------------------------------------------------------------------------


def test_draft_generation_and_fallback():
    print("-- draft: AI formalization & offline fallback --")
    canned = {"generated": True, "subject": "Missing Admit Card",
              "text": "My admit card has not been issued. Kindly resolve this at the earliest.",
              "error": None, "manual": False}
    with patch("app.grievance.routes.formalize", return_value=canned):
        r = client.post("/api/grievances/draft/generate", json={"input": "my admit card is missing"}, headers=_fake_ip(32))
        check("draft 200", r.status_code == 200, r.text[:200])
        d = r.json()
        check("draft subject/text returned", d.get("generated") is True and d.get("subject") == "Missing Admit Card" and "not been issued" in d.get("text", ""))

    offline = {"generated": False, "subject": "my admit card is missing", "text": "my admit card is missing",
               "error": "ollama unreachable", "manual": True}
    with patch("app.grievance.routes.formalize", return_value=offline):
        r2 = client.post("/api/grievances/draft/generate", json={"input": "my admit card is missing"}, headers=_fake_ip(33))
        check("draft fallback 200 (manual continues)", r2.status_code == 200, r2.text[:200])
        check("draft fallback flagged manual", r2.json().get("generated") is False and r2.json().get("manual") is True)

    r3 = client.post("/api/grievances/draft/generate", json={"input": "x"}, headers=_fake_ip(34))
    check("draft rejects tiny input", r3.status_code == 422)

    rc = client.get("/api/grievances/categories", headers=_fake_ip(35))
    check("categories endpoint", rc.status_code == 200 and isinstance(rc.json().get("categories"), list))


# ---------------------------------------------------------------------------
# 5. Submission flow
# ---------------------------------------------------------------------------


def test_submission_flow():
    print("-- submit: pre-login grievance --")
    payload = _submit_payload()
    r = client.post("/api/grievances", json=payload, headers=_fake_ip(41))
    check("submit 201", r.status_code == 201, r.text[:300])
    d = r.json()
    check("receipt has reference", REF_RE.match(d.get("reference", "")))
    check("receipt has one-time tracking token", bool(d.get("tracking_token")) and d.get("tracking_token") != d.get("reference"))
    check("receipt status submitted", d.get("status") == "submitted")
    check("email disabled => not confirmed", d.get("email_confirmed") is False)
    check("receipt leaks no student PII", not any(k in d for k in ("student_email", "student_name", "roll_number", "phone", "email")))
    _record_submission(d["reference"])

    db = SessionLocal()
    try:
        g = db.query(Grievance).filter(Grievance.reference == d["reference"]).first()
        check("row stored with reference", g is not None)
        check("digest stored, not plaintext",
              g.tracking_token_hash == hashlib.sha256(d["tracking_token"].encode()).hexdigest() and
              g.tracking_token_hash != d["tracking_token"])
        check("original input preserved", g.original_student_input == payload["original_input"])
        check("final text preserved", g.final_grievance_text == payload["final_text"])
        check("category preserved", g.category == "Examination & Results")
        check("source_kind pre_login", g.source_kind == "pre_login")
        check("student details stored", g.student_name == "Test Student" and g.student_email == "student@example.com" and g.roll_number == "ABC123")
        check("programme & phone stored", g.programme == "bca" and g.phone == "+91 9876543210")
        hist = db.query(GrievanceStatusHistory).filter(GrievanceStatusHistory.grievance_id == g.id).order_by(GrievanceStatusHistory.created_at).all()
        check("immutable history entry (-> submitted)", len(hist) == 1 and hist[0].new_status == "submitted" and hist[0].previous_status == "draft")
        check("history actor = system", hist[0].changed_by_role == "system" and "system" in (hist[0].changed_by or ""))
        check("history comment internal", hist[0].is_internal is True)
    finally:
        db.close()

    # Duplicate reference impossible: generate a collision attempt via direct model insert
    db2 = SessionLocal()
    try:
        g2 = Grievance(reference="CUS-GRV-2099-ABCD1234", student_name="Dup")
        db2.add(g2)
        db2.commit()
        _record_submission(g2.reference)
        dup = Grievance(reference="CUS-GRV-2099-ABCD1234", student_name="Dup2")
        db2.add(dup)
        try:
            db2.commit()
            check("duplicate reference blocked", False, "unique constraint did not fire")
        except Exception:
            db2.rollback()
            check("duplicate reference blocked", True)
    finally:
        db2.close()


def test_submission_validation_and_security():
    print("-- submit: validation, inactive office, email --")
    bad_email = _submit_payload()
    bad_email["student"]["email"] = "not-an-email"
    r = client.post("/api/grievances", json=bad_email, headers=_fake_ip(42))
    check("invalid email rejected", r.status_code == 422, r.text[:200])

    inactive_id = _make_authority("Deactivated Office", active=False)
    bad_office = _submit_payload(authority_id=inactive_id)
    r2 = client.post("/api/grievances", json=bad_office, headers=_fake_ip(43))
    check("inactive office rejected (422)", r2.status_code == 422, r2.text[:200])

    short = _submit_payload(final_text="too short")
    r3 = client.post("/api/grievances", json=short, headers=_fake_ip(44))
    check("too-short final text rejected", r3.status_code == 422)

    active_id = _make_authority("Controller of Examinations")
    routed = _submit_payload(authority_id=active_id)
    r4 = client.post("/api/grievances", json=routed, headers=_fake_ip(45))
    check("active office accepted", r4.status_code == 201, r4.text[:200])
    db = SessionLocal()
    try:
        g = db.query(Grievance).filter(Grievance.reference == r4.json()["reference"]).first()
        _record_submission(g.reference)
        check("routed to active office", g.authority_id == active_id)
        check("email_status failed (email disabled)", g.email_status == "failed")
    finally:
        db.close()


def test_verification_token_gated():
    print("-- verify: token-gated, PII-free --")
    r = client.post("/api/grievances", json=_submit_payload(), headers=_fake_ip(46))
    d = r.json()
    ref, token = d["reference"], d["tracking_token"]
    _record_submission(ref)

    ok = client.get(f"/api/grievances/{ref}/verify", params={"token": token}, headers=_fake_ip(47))
    check("verify ok 200", ok.status_code == 200, ok.text[:200])
    v = ok.json()
    check("verify payload status+ref", v.get("reference") == ref and v.get("status") == "submitted")
    check("verify leaks no PII", not any(k in v for k in ("student_email", "student_name", "roll_number", "phone", "tracking_token", "email", "token")))
    check("verify has authority fields", "authority_name" in v and "submitted_at" in v)

    bad_token = client.get(f"/api/grievances/{ref}/verify", params={"token": "wrong-token-value"}, headers=_fake_ip(48))
    check("wrong token -> 403", bad_token.status_code == 403, str(bad_token.status_code))

    no_token = client.get(f"/api/grievances/{ref}/verify", headers=_fake_ip(49))
    check("missing token -> 4xx", no_token.status_code in (403, 422), str(no_token.status_code))

    ghost = client.get("/api/grievances/CUS-GRV-2099-FFFFFFFF/verify", params={"token": "some-token-value-xyz"}, headers=_fake_ip(50))
    check("unknown reference -> 403 (indistinguishable)", ghost.status_code == 403, str(ghost.status_code))


def test_rate_limits():
    print("-- rate limits on public intake --")
    payload = _submit_payload()
    statuses = []
    for i in range(7):
        resp = client.post("/api/grievances", json=payload, headers=_fake_ip(60))
        statuses.append(resp.status_code)
        if resp.status_code == 201:
            _record_submission(resp.json()["reference"])
    check("6 allowed then 7th rate-limited", statuses.count(201) == 6 and statuses.count(429) == 1, str(statuses))
    check("429 returned once limit crossed", 429 in statuses, str(statuses))

    gen_statuses = []
    with patch("app.grievance.routes.formalize", return_value={"generated": True, "subject": "s", "text": "t" * 40, "error": None, "manual": False}):
        for i in range(7):
            resp = client.post("/api/grievances/draft/generate", json={"input": "my results are not showing"}, headers=_fake_ip(61))
            gen_statuses.append(resp.status_code)
    check("generate: 5 allowed then rate-limited", gen_statuses.count(200) == 5, str(gen_statuses))
    check("generate 429", 429 in gen_statuses, str(gen_statuses))


def test_idempotent_retry():
    print("-- idempotency: retries never duplicate --")
    from app.grievance.intake import token_for_request_id
    from app.grievance.models import Grievance
    from app.database import SessionLocal

    active_id = _make_authority("Controller of Examinations")
    payload = _submit_payload(authority_id=active_id, final_text="Admit card has not been generated after form submission and fee payment.")
    key = "test-retry-" + uuid.uuid4().hex[:10]

    r1 = client.post("/api/grievances", json={**payload, "idempotency_key": key}, headers=_fake_ip(70))
    check("first submit 201", r1.status_code == 201, r1.text[:200])
    d1 = r1.json()
    _record_submission(d1["reference"])
    check("first receipt has token", bool(d1.get("tracking_token")), str(d1)[:120])

    r2 = client.post("/api/grievances", json={**payload, "idempotency_key": key}, headers=_fake_ip(70))
    check("retry also 201 (replayed)", r2.status_code == 201, str(r2.status_code))
    d2 = r2.json()
    check("retry returns SAME reference", d2["reference"] == d1["reference"])
    check("retry re-delivers SAME token", d2["tracking_token"] == d1["tracking_token"])
    check("retry reports deduplicated", d2.get("deduplicated") is True, str(d2))

    db = SessionLocal()
    try:
        n = db.query(Grievance).filter(Grievance.client_request_id == key).count()
        check("exactly ONE row for the key", n == 1, f"n={n}")
    finally:
        db.close()

    # A different key is a genuinely new submission (new reference, new row).
    r3 = client.post("/api/grievances", json={**payload, "idempotency_key": "other-" + uuid.uuid4().hex[:10]}, headers=_fake_ip(71))
    check("different key creates new grievance", r3.status_code == 201 and r3.json()["reference"] != d1["reference"], r3.text[:200])
    _record_submission(r3.json()["reference"])

    # The re-delivered token still verifies (deterministic derivation matches).
    v = client.get(f"/api/grievances/{d1['reference']}/verify", params={"token": d1["tracking_token"]}, headers=_fake_ip(72))
    check("re-delivered token verifies status", v.status_code == 200 and v.json()["status"] == "submitted", str(v.status_code))

    # Token is deterministic for a given key (never random on retry).
    check("token derived deterministically",
          token_for_request_id(key) == d1["tracking_token"])

    # Missing idempotency key keeps legacy behavior: two posts, two records.
    r4 = client.post("/api/grievances", json=payload, headers=_fake_ip(73))
    r5 = client.post("/api/grievances", json=payload, headers=_fake_ip(74))
    check("no key -> second POST creates second grievance (documented)",
          r4.status_code == 201 and r5.status_code == 201
          and r4.json()["reference"] != r5.json()["reference"], f"{r4.status_code}/{r5.status_code}")
    _record_submission(r4.json()["reference"])
    _record_submission(r5.json()["reference"])

    bad_key = client.post("/api/grievances", json={**payload, "idempotency_key": "short"}, headers=_fake_ip(75))
    check("malformed idempotency key rejected", bad_key.status_code == 422, str(bad_key.status_code))


# ---------------------------------------------------------------------------
# 6. Planner integration (engine rule)
# ---------------------------------------------------------------------------


def test_planner_grievance_rule():
    print("-- planner: grievance routed before service keyword --")
    from app.orchestrator.context import ConversationContext
    from app.orchestrator.extractor import extract_entities
    from app.orchestrator.planner import plan

    ctx = ConversationContext()

    p1 = plan("I haven't received my admit card", ctx, "test-chat", extract_entities("I haven't received my admit card"))
    check("complaint routes to grievance action", p1.action == "grievance", str(p1))

    p2 = plan("where can I check my results", ctx, "test-chat", extract_entities("where can I check my results"))
    check("info query NOT grievance", p2.action != "grievance", str(p2))

    p3 = plan("my marksheet is missing", ctx, "test-chat", extract_entities("my marksheet is missing"))
    check("typo complaint still routes", p3.action == "grievance", str(p3))


def _main():
    tests = [
        test_intent_detection,
        test_reference_and_token_material,
        test_recommendation_active_only,
        test_draft_generation_and_fallback,
        test_submission_flow,
        test_submission_validation_and_security,
        test_verification_token_gated,
        test_idempotent_retry,
        test_rate_limits,
        test_planner_grievance_rule,
    ]
    try:
        for fn in tests:
            try:
                print(f"-- {fn.__name__} --")
                fn()
            except Exception as exc:  # noqa: BLE001
                FAIL.append(fn.__name__)
                print(f"  ERROR  {fn.__name__}: {exc}")
    finally:
        _cleanup()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    _main()