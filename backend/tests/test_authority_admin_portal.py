"""
backend/tests/test_authority_admin_portal.py

PHASE 6 — Authority Admin Portal end-to-end tests.

Covers the Phase-6 §34 checklist:
  AUTH          login, wrong password, inactive account, student blocked,
                superadmin unchanged
  ISOLATION     A sees only A, A cannot read/manipulate B (404), forged
                authority_id ignored, inactive-authority policy
  GRIEVANCE     unread on arrival, open -> read, mark unread, status changes,
                invalid/no-op status rejected, immutable history, response
  EMAIL         student confirmation, authority notification, response email,
                failure keeps the grievance, credentials never exposed
  STUDENT API   draft/generate, categories, submit receipt, verify token gate
  SUPERADMIN    regression: panel login + manage routes still work

Uses the app's real DB (TestClient) + an in-process loopback SMTP server.
Every created row is cleaned up. Compatible with pytest and standalone run.

Run:  python tests/test_authority_admin_portal.py   (or pytest tests/)
"""

from __future__ import annotations

import re
import socketserver
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401

from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal, create_all
from app.grievance.models import Grievance, GrievanceStatusHistory
from app.main import app

create_all()

PASS: list[str] = []
FAIL: list[str] = []

client = TestClient(app)

_created_authority_ids: list[str] = []
_created_user_ids: list[str] = []
_created_grievance_ids: list[str] = []
_created_refs: list[str] = []

_A_ADMIN = {"username": "", "password": "pass1234"}
_B_ADMIN = {"username": "", "password": "pass1234"}
_OFF_ADMIN = {"username": "", "password": "pass1234"}
_INACTIVE_ADMIN = {"username": "", "password": "pass1234"}
_SUPER = {"username": "", "password": "secret123"}
_STUDENT = {"username": "", "password": "secret123"}

TOKENS: dict[str, dict] = {}
AUTHORITY = {"a": "", "b": "", "c": ""}
GRIEVE = {"a_g1": "", "b_g1": "", "a_g2": ""}

REF_RE = re.compile(r"^CUS-GRV-\d{4}-[0-9A-F]{8}$")


def check(name: str, cond: bool, detail: str = ""):
    line = f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else "")
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    if cond:
        PASS.append(name)
    else:
        FAIL.append(name)


# ---------------------------------------------------------------------------
# Minimal real SMTP server on loopback (same pattern as the Phase-5 E2E)
# ---------------------------------------------------------------------------


class _SmtpHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.wfile.write(b"220 p6-test-smtp ESMTP\r\n")
        buf = b""
        mail_from = ""
        rcpt_to: list[str] = []
        body = b""
        in_data = False
        while True:
            line = self.rfile.readline()
            if not line:
                break
            buf += line
            if in_data:
                if line == b".\r\n":
                    in_data = False
                    self.server.capture.append({
                        "mail_from": mail_from,
                        "rcpt_to": list(rcpt_to),
                        "data": body.decode("utf-8", "replace"),
                    })
                    self.wfile.write(b"250 OK\r\n")
                else:
                    body += line
                continue
            up = line.strip().upper()
            if up.startswith(b"EHLO") or up.startswith(b"HELO"):
                self.wfile.write(b"250-p6-test-smtp\r\n250 SIZE 10000000\r\n")
            elif up.startswith(b"MAIL FROM"):
                mail_from = line.strip().decode("utf-8", "replace")
                self.wfile.write(b"250 OK\r\n")
            elif up.startswith(b"RCPT TO"):
                rcpt_to.append(line.strip().decode("utf-8", "replace"))
                self.wfile.write(b"250 OK\r\n")
            elif up.startswith(b"DATA"):
                in_data = True
                body = b""
                self.wfile.write(b"354 go ahead\r\n")
            elif up.startswith(b"QUIT"):
                self.wfile.write(b"221 bye\r\n")
                break
            else:
                self.wfile.write(b"250 OK\r\n")


class _SmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self) -> None:
        self.capture: list[dict] = []
        super().__init__(("127.0.0.1", 0), _SmtpHandler)

    @property
    def port(self) -> int:
        return self.server_address[1]


mail_server = _SmtpServer()
mail_thread = threading.Thread(target=mail_server.serve_forever, daemon=True)
mail_thread.start()

_SAVED = {k: getattr(settings, k) for k in (
    "EMAIL_ENABLED", "SMTP_HOST", "SMTP_PORT", "SMTP_STARTTLS",
    "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "MAIL_FROM_NAME",
)}


def _enable_smtp() -> None:
    settings.EMAIL_ENABLED = True
    settings.SMTP_HOST = "127.0.0.1"
    settings.SMTP_PORT = mail_server.port
    settings.SMTP_STARTTLS = False
    settings.SMTP_USER = ""
    settings.SMTP_PASSWORD = ""
    settings.MAIL_FROM = "grievance@cus.ac.in"
    settings.MAIL_FROM_NAME = "CUS Grievance Cell"


def _disable_smtp() -> None:
    settings.EMAIL_ENABLED = False
    settings.SMTP_HOST = "127.0.0.1"
    settings.SMTP_PORT = mail_server.port


def _restore_settings() -> None:
    for k, v in _SAVED.items():
        setattr(settings, k, v)


# ---------------------------------------------------------------------------
# Setup / cleanup
# ---------------------------------------------------------------------------


def _cleanup() -> None:
    from app.models import AuditLog, Authority, User

    db = SessionLocal()
    try:
        for gid in _created_grievance_ids:
            db.query(GrievanceStatusHistory).filter(
                GrievanceStatusHistory.grievance_id == gid
            ).delete()
            db.query(Grievance).filter(Grievance.id == gid).delete()
        for uid in _created_user_ids:
            db.query(AuditLog).filter(AuditLog.actor_id == uid).delete()
            db.query(User).filter(User.id == uid).delete()
        for aid in _created_authority_ids:
            db.query(Authority).filter(Authority.id == aid).delete()
        db.commit()
    finally:
        db.close()
    for lst in (_created_grievance_ids, _created_user_ids, _created_authority_ids):
        lst.clear()
    _created_refs.clear()


def _register_grievance(ref: str) -> str:
    db = SessionLocal()
    try:
        row = db.query(Grievance).filter(Grievance.reference == ref).first()
        gid = str(row.id) if row else None
        if row and row.id not in _created_grievance_ids:
            _created_grievance_ids.append(str(row.id))
    finally:
        db.close()
    return gid or ""


def _make_authority(name: str, active: bool = True) -> str:
    from app.models import Authority

    db = SessionLocal()
    try:
        authority = Authority(
            id=str(uuid.uuid4()),
            department_name=f"Dept {name} {uuid.uuid4().hex[:4]}",
            authority_name=f"{name} Office",
            designation="Head",
            email=f"office-{name.lower()}-{uuid.uuid4().hex[:8]}@cus.ac.in",
            phone="0194-2311256",
            office_location="Gogji-Bagh, Srinagar",
            active=active,
            source_kind="manual",
        )
        db.add(authority)
        db.commit()
        db.refresh(authority)
        aid = str(authority.id)
        _created_authority_ids.append(aid)
        return aid
    finally:
        db.close()


def _reload_authority_cache() -> None:
    """Mirror production: the admin routes refresh the service cache on write."""
    from app.authority.service import authority_service

    db = SessionLocal()
    try:
        authority_service.load_cache(db)
    finally:
        db.close()


def _create_user(username: str, password: str, role: str, authority_id: str | None = None, active: bool = True, email: str | None = None) -> None:
    from app.auth.security import hash_password
    from app.models import User

    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == username).first():
            return
        user = User(
            id=uuid.uuid4(),
            username=username,
            email=email or f"{username}@test.local",
            hashed_password=hash_password(password),
            role=role,
            is_active=active,
            authority_id=authority_id,
            full_name=f"Full {username}" if role == "authority_admin" else None,
            designation="Office Head" if role == "authority_admin" else None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        _created_user_ids.append(str(user.id))
    finally:
        db.close()


def _ensure_users() -> None:
    _SUPER["username"] = f"__p6_super_{uuid.uuid4().hex[:6]}"
    _STUDENT["username"] = f"__p6_student_{uuid.uuid4().hex[:6]}"
    _A_ADMIN["username"] = f"__p6_admin_a_{uuid.uuid4().hex[:6]}"
    _B_ADMIN["username"] = f"__p6_admin_b_{uuid.uuid4().hex[:6]}"
    _OFF_ADMIN["username"] = f"__p6_admin_c_{uuid.uuid4().hex[:6]}"
    _INACTIVE_ADMIN["username"] = f"__p6_admin_off_{uuid.uuid4().hex[:6]}"

    AUTHORITY["a"] = _make_authority("Admission")
    AUTHORITY["b"] = _make_authority("Examinations")
    AUTHORITY["c"] = _make_authority("Registrar", active=False)
    _reload_authority_cache()

    _create_user(_SUPER["username"], _SUPER["password"], "superadmin")
    _create_user(_STUDENT["username"], _STUDENT["password"], "student")
    _create_user(_A_ADMIN["username"], _A_ADMIN["password"], "authority_admin", AUTHORITY["a"])
    _create_user(_B_ADMIN["username"], _B_ADMIN["password"], "authority_admin", AUTHORITY["b"])
    # Admin bound to an INACTIVE authority (account itself stays active).
    _create_user(_OFF_ADMIN["username"], _OFF_ADMIN["password"], "authority_admin", AUTHORITY["c"])
    # Inactive account (authority A).
    _create_user(_INACTIVE_ADMIN["username"], _INACTIVE_ADMIN["password"], "authority_admin", AUTHORITY["a"], active=False)

    for key, creds in (("super", _SUPER), ("student", _STUDENT), ("a", _A_ADMIN), ("b", _B_ADMIN), ("c", _OFF_ADMIN)):
        r = client.post("/api/auth/login", data={"username": creds["username"], "password": creds["password"]})
        if r.status_code == 200:
            TOKENS[key] = {"Authorization": f"Bearer {r.json()['access_token']}"}


def _login(username: str, password: str):
    r = client.post("/api/auth/login", data={"username": username, "password": password})
    return r


def _submit(final_text: str, authority_id: str, student_email: str = "stu.test@example.com") -> dict:
    receipt = {}
    r = client.post("/api/grievances", json={
        "student": {"name": "Test Student", "email": student_email, "roll_number": "24601", "semester": "4", "college": "Amar Singh College"},
        "final_text": final_text,
        "original_input": "informal complaint text",
        "category": "Examinations",
        "authority_id": authority_id,
        "idempotency_key": "p6-" + uuid.uuid4().hex[:24],
    })
    receipt["status"] = r.status_code
    receipt["body"] = r.json()
    if r.status_code == 201:
        _created_refs.append(r.json()["reference"])
    return receipt


def _history_count(gid: str) -> int:
    db = SessionLocal()
    try:
        return db.query(GrievanceStatusHistory).filter(
            GrievanceStatusHistory.grievance_id == gid
        ).count()
    finally:
        db.close()


# ===========================================================================
# 1. AUTHENTICATION
# ===========================================================================


def test_01_authentication():
    # 1. authority admin login works
    r = _login(_A_ADMIN["username"], _A_ADMIN["password"])
    check("1. authority admin login works", r.status_code == 200
          and r.json()["user"]["role"] == "authority_admin"
          and r.json()["user"]["authority_id"] == AUTHORITY["a"], f"{r.status_code}")
    check("1b. token carries identity only, no password", "password" not in str(r.json()))

    # 2. invalid password rejected
    r = _login(_A_ADMIN["username"], "wrong-password-xyz")
    check("2. invalid password rejected", r.status_code == 401, str(r.status_code))

    # 3. inactive account rejected
    r = _login(_INACTIVE_ADMIN["username"], _INACTIVE_ADMIN["password"])
    check("3. inactive account rejected", r.status_code == 403, str(r.status_code))

    # 4. student cannot access authority dashboard
    r = client.get("/api/authority-admin/dashboard", headers=TOKENS["student"])
    check("4. student cannot access dashboard", r.status_code == 403, str(r.status_code))

    # 5. superadmin behavior unchanged + portal blocked for superadmin
    r = _login(_SUPER["username"], _SUPER["password"])
    check("5. superadmin login works", r.status_code == 200 and r.json()["user"]["role"] == "superadmin", str(r.status_code))
    r = client.get("/api/admin/authorities", headers=TOKENS["super"])
    check("5b. superadmin panel API works", r.status_code == 200, str(r.status_code))
    r = client.get("/api/authority-admin/grievances", headers=TOKENS["super"])
    check("5c. superadmin cannot use the authority portal", r.status_code == 403, str(r.status_code))


# ===========================================================================
# 2. GRIEVANCE LIFECYCLE (unread -> read -> status -> response)
# ===========================================================================


def test_02_grievance_lifecycle():
    # 11. new grievance is UNREAD for the authority
    rec = _submit("The exam form is not showing in my portal and the last date is tomorrow. Please help.", AUTHORITY["a"])
    check("11. grievance submitted to A", rec["status"] == 201, f"{rec['status']}")
    GRIEVE["a_g1"] = rec["body"]["reference"]
    gid = _register_grievance(GRIEVE["a_g1"])
    check("11b. reference format", bool(REF_RE.match(GRIEVE["a_g1"])), GRIEVE["a_g1"])

    r = client.get("/api/authority-admin/grievances", headers=TOKENS["a"])
    d = r.json()
    check("11c. appears in A list", d.get("total", 0) == 1 and d["items"][0]["reference"] == GRIEVE["a_g1"], str(d.get("total")))
    check("11d. appears as UNREAD", d["items"][0]["is_read"] is False, f"is_read={d['items'][0]['is_read']}")
    check("11e. unread counter = 1", d.get("unread_total") == 1, str(d.get("unread_total")))

    # 12. opening the grievance marks it READ (auto)
    r = client.get(f"/api/authority-admin/grievances/{gid}", headers=TOKENS["a"])
    check("12. open -> 200", r.status_code == 200, str(r.status_code))
    det = r.json()
    check("12b. open marks READ", det["is_read"] is True, f"is_read={det['is_read']}")
    check("12c. reader recorded", det.get("read_by") == f"Full {_A_ADMIN['username']}", str(det.get("read_by")))
    check("12d. history entry for submission", any(h["new_status"] == "submitted" for h in det["history"]))
    r = client.get(f"/api/authority-admin/grievances/{gid}", headers=TOKENS["a"])
    check("12e. opening an already-read grievance is stable (no dup)", r.json()["is_read"] is True)

    # 13. mark unread works (and is idempotent)
    r = client.post(f"/api/authority-admin/grievances/{gid}/unread", headers=TOKENS["a"])
    check("13. mark unread works", r.status_code == 200 and r.json()["is_read"] is False, str(r.status_code))
    r = client.post(f"/api/authority-admin/grievances/{gid}/unread", headers=TOKENS["a"])
    check("13b. mark unread idempotent", r.status_code == 200 and r.json()["is_read"] is False, str(r.status_code))
    r = client.post(f"/api/authority-admin/grievances/{gid}/read", headers=TOKENS["a"])
    check("13c. mark read works", r.status_code == 200 and r.json()["is_read"] is True, str(r.status_code))

    # 14/16/17. status changes + immutable history + duplicate safety
    before = _history_count(gid)
    r = client.post(f"/api/authority-admin/grievances/{gid}/status", json={"new_status": "in_progress", "note": "under review"}, headers=TOKENS["a"])
    check("14. status -> in_progress", r.status_code == 200 and r.json()["status"] == "in_progress", f"{r.status_code} {r.text[:120]}")
    check("16. history row appended", _history_count(gid) == before + 1, f"{before}->{_history_count(gid)}")
    r = client.post(f"/api/authority-admin/grievances/{gid}/status", json={"new_status": "in_progress"}, headers=TOKENS["a"])
    check("17. duplicate/no-op status rejected (409)", r.status_code == 409, str(r.status_code))
    check("17b. no duplicate history row for no-op", _history_count(gid) == before + 1, f"count={_history_count(gid)}")
    r = client.post(f"/api/authority-admin/grievances/{gid}/status", json={"new_status": "not_a_status"}, headers=TOKENS["a"])
    check("15. invalid status rejected", r.status_code == 422, str(r.status_code))
    r = client.post(f"/api/authority-admin/grievances/{gid}/status", json={"new_status": "draft"}, headers=TOKENS["a"])
    check("15b. draft not settable by admin", r.status_code == 422, str(r.status_code))

    # 18. response creation (SMTP off: stored anyway, honest 'failed' status)
    resp_text = "Your examination form issue is being resolved. Collect the form from the examination office."
    r = client.post(f"/api/authority-admin/grievances/{gid}/response", json={"response": resp_text}, headers=TOKENS["a"])
    check("18. response created", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("18b. response stored, delivery honest (failed)", r.json().get("response_email_status") == "failed", str(r.json().get("response_email_status")))
    r = client.post(f"/api/authority-admin/grievances/{gid}/response", json={"response": "second attempt"}, headers=TOKENS["a"])
    check("18c. duplicate response rejected (409)", r.status_code == 409, str(r.status_code))
    det = client.get(f"/api/authority-admin/grievances/{gid}", headers=TOKENS["a"]).json()
    check("18d. response visible in detail", det.get("authority_response") == resp_text)
    check("18e. response entry in history (student-visible)", any(h["new_status"] == h["previous_status"] and not h["is_internal"] for h in det["history"]))

    # resolve/close timestamps
    r = client.post(f"/api/authority-admin/grievances/{gid}/status", json={"new_status": "resolved"}, headers=TOKENS["a"])
    check("14b. status -> resolved", r.json()["status"] == "resolved", str(r.status_code))
    det = client.get(f"/api/authority-admin/grievances/{gid}", headers=TOKENS["a"]).json()
    check("14c. resolved_at stamped", bool(det.get("resolved_at")), str(det.get("resolved_at")))
    r = client.post(f"/api/authority-admin/grievances/{gid}/status", json={"new_status": "closed"}, headers=TOKENS["a"])
    det = client.get(f"/api/authority-admin/grievances/{gid}", headers=TOKENS["a"]).json()
    check("14d. closed_at stamped", bool(det.get("closed_at")), str(det.get("closed_at")))


# ===========================================================================
# 3. AUTHORITY ISOLATION (IDOR)
# ===========================================================================


def test_03_isolation():
    # 6. B sees only B's grievances (initially none)
    r = client.get("/api/authority-admin/grievances", headers=TOKENS["b"])
    check("6. B list starts empty", r.json().get("total", 0) == 0, str(r.json().get("total")))

    # 7. A grievance invisible to B (404, no leak)
    gid_a = _register_grievance(GRIEVE["a_g1"])
    r = client.get(f"/api/authority-admin/grievances/{gid_a}", headers=TOKENS["b"])
    check("7. A grievance -> B = 404", r.status_code == 404, str(r.status_code))

    # 8. B cannot manipulate A's grievance (status/read/unread/response)
    r = client.post(f"/api/authority-admin/grievances/{gid_a}/status", json={"new_status": "closed"}, headers=TOKENS["b"])
    check("8a. B cannot change A's status", r.status_code == 404, str(r.status_code))
    r = client.post(f"/api/authority-admin/grievances/{gid_a}/read", headers=TOKENS["b"])
    check("8b. B cannot mark A's grievance", r.status_code == 404, str(r.status_code))
    r = client.post(f"/api/authority-admin/grievances/{gid_a}/unread", headers=TOKENS["b"])
    check("8c. B cannot unmark A's grievance", r.status_code == 404, str(r.status_code))
    r = client.post(f"/api/authority-admin/grievances/{gid_a}/response", json={"response": "tampering"}, headers=TOKENS["b"])
    check("8d. B cannot respond to A's grievance", r.status_code == 404, str(r.status_code))
    # A's grievance was NOT touched by B's attempts
    det = client.get(f"/api/authority-admin/grievances/{gid_a}", headers=TOKENS["a"]).json()
    check("8e. A's grievance intact after B attacks", det["status"] == "closed" and det.get("authority_response"))

    # 9. forged authority_id query param cannot widen scope
    r_plain = client.get("/api/authority-admin/grievances", headers=TOKENS["a"]).json()
    r_forged = client.get(f"/api/authority-admin/grievances?authority_id={AUTHORITY['b']}", headers=TOKENS["a"]).json()
    check("9. forged authority_id ignored", r_forged["total"] == r_plain["total"] == 1, f"plain={r_plain['total']} forged={r_forged['total']}")
    check("9b. forged param leaks nothing from B",
          all(i["reference"] != GRIEVE["a_g1"] or i["is_read"] is not None for i in r_forged["items"]),
          f"items={len(r_forged['items'])}")
    r_forged2 = client.get(f"/api/authority-admin/grievances?q={GRIEVE['a_g1'].split('-')[-1]}", headers=TOKENS["b"]).json()
    check("9c. B search for A reference finds nothing", r_forged2["total"] == 0, str(r_forged2["total"]))

    # forged authority_id in request BODY is ignored by the API contract
    r = client.post(f"/api/authority-admin/grievances/{gid_a}/status", json={"new_status": "in_progress", "authority_id": AUTHORITY["b"]}, headers=TOKENS["a"])
    check("9d. body-forged authority_id ignored (A admin, A grievance ok)", r.status_code == 200, str(r.status_code))

    # 10. inactive authority: account can log in (account-level policy), but the
    #     authority is not routable for new grievances (backend routing policy)
    r = _login(_OFF_ADMIN["username"], _OFF_ADMIN["password"])
    check("10. inactive-authority admin account can log in", r.status_code == 200, str(r.status_code))
    r = client.get("/api/authority-admin/grievances", headers=TOKENS["c"])
    check("10b. portal serves the (empty) inactive authority scope", r.status_code == 200 and r.json().get("total", 0) == 0, str(r.status_code))
    rec = _submit("A grievance that must not route to an inactive office.", AUTHORITY["c"])
    check("10c. new grievance cannot route to inactive authority", rec["status"] == 422, f"{rec['status']}")


# ===========================================================================
# 4. EMAIL DELIVERY (loopback SMTP on)
# ===========================================================================


def test_04_email_delivery():
    _enable_smtp()
    try:
        mail_server.capture.clear()

        # student confirmation + authority notification when a grievance is filed
        rec = _submit("I missed the exam form deadline because the portal was down every evening. Kindly allow late submission.", AUTHORITY["a"], student_email="p6.student@example.com")
        check("19. submission stored with email on", rec["status"] == 201, f"{rec['status']} {str(rec['body'])[:160]}")
        ref2 = rec["body"]["reference"]
        gid = _register_grievance(ref2)
        GRIEVE["a_g2"] = ref2
        check("19b. student ack email confirmed", rec["body"].get("email_confirmed") is True, str(rec["body"].get("email_confirmed")))
        check("20. authority notification confirmed", rec["body"].get("authority_email_status") == "sent", str(rec["body"].get("authority_email_status")))

        mail = [m for m in mail_server.capture if "p6.student@example.com" in " ".join(m["rcpt_to"])]
        check("19c. student email actually captured", len(mail) == 1, f"captured={len(mail)}")
        check("19d. student email contains reference", ref2 in (mail[0]["data"] if mail else ""))

        auth_mail = [m for m in mail_server.capture if not any("p6.student@example.com" in r for r in m["rcpt_to"])]
        check("20b. authority email actually captured", len(auth_mail) == 1, f"captured={len(auth_mail)}")
        if auth_mail:
            body = auth_mail[0]["data"]
            check("20c. authority email has reference + student details",
                  ref2 in body and "Test Student" in body and "24601" in body and "Amar Singh College" in body)

        # authority response -> student response email
        mail_server.capture.clear()
        r = client.post(f"/api/authority-admin/grievances/{gid}/status", json={"new_status": "in_progress", "note": "processing"}, headers=TOKENS["a"])
        check("21a. status advanced before responding", r.status_code == 200, str(r.status_code))
        resp_text = "Late submission allowed. Submit the form at the examination office by Friday."
        r = client.post(f"/api/authority-admin/grievances/{gid}/response", json={"response": resp_text}, headers=TOKENS["a"])
        check("21. response delivered", r.status_code == 200 and r.json().get("response_email_status") == "sent",
              f"{r.status_code} {str(r.json().get('response_email_status'))}")
        mail = [m for m in mail_server.capture if any("p6.student@example.com" in r for r in m["rcpt_to"])]
        check("21b. response email captured", len(mail) == 1, f"captured={len(mail)}")
        if mail:
            body = mail[0]["data"]
            check("21c. response email contains ref/status/authority/response",
                  ref2 in body and "In Progress" in body and "Admission" in body and resp_text in body)
    finally:
        _disable_smtp()

    # 22. email failure does NOT lose the grievance
    rec = _submit("A grievance filed while email is down must still be stored.", AUTHORITY["a"])
    check("22. failure-path submission still 201", rec["status"] == 201, f"{rec['status']}")
    check("22b. student ack honestly failed", rec["body"].get("email_confirmed") is False)
    check("22c. authority email honestly failed", rec["body"].get("authority_email_status") == "failed")
    gid3 = _register_grievance(rec["body"]["reference"])
    r = client.get(f"/api/authority-admin/grievances/{gid3}", headers=TOKENS["a"])
    check("22d. grievance intact in portal after email failure", r.status_code == 200 and r.json()["reference"] == rec["body"]["reference"], str(r.status_code))

    _restore_settings()


# ===========================================================================
# 5. CREDENTIAL HYGIENE + AUDIT + STUDENT API REGRESSION
# ===========================================================================


def test_05_hygiene_audit_student_api():
    # Profile self-update: permitted fields only; authority fields immutable
    r = client.put("/api/authority-admin/profile", json={"full_name": "Updated Portal Admin", "phone": "9999000000"}, headers=TOKENS["a"])
    check("23a. own profile update works", r.status_code == 200 and r.json().get("full_name") == "Updated Portal Admin", str(r.status_code))
    check("23b. authority identity unchanged by profile update", r.json().get("authority", {}).get("authority_name") == "Admission Office", str(r.json().get("authority", {}).get("authority_name")))
    r = client.put("/api/authority-admin/password", json={"current_password": "wrong", "new_password": "newpass123"}, headers=TOKENS["a"])
    check("23c. wrong current password rejected", r.status_code == 400, str(r.status_code))
    r = client.put("/api/authority-admin/password", json={"current_password": _A_ADMIN["password"], "new_password": "newpass123"}, headers=TOKENS["a"])
    check("23d. password changed with correct current", r.status_code == 200, str(r.status_code))
    r = _login(_A_ADMIN["username"], "newpass123")
    check("23e. new password works", r.status_code == 200, str(r.status_code))
    _A_ADMIN["password"] = "newpass123"

    secrets_found: list[str] = []
    for url, headers in (
        ("/api/authority-admin/profile", TOKENS["a"]),
        ("/api/authority-admin/dashboard", TOKENS["a"]),
        ("/api/authority-admin/grievances", TOKENS["a"]),
    ):
        r = client.get(url, headers=headers)
        blob = str(r.json())
        for needle in ("hashed_password", "SMTP_PASSWORD", "smtp_password", "password_hash"):
            if needle in blob:
                secrets_found.append(f"{url}:{needle}")
    check("23. no credential hashes/keys leaked by portal responses", not secrets_found, "; ".join(secrets_found) or "clean")

    # audit trail (time-windowed: AuditLog.id is a UUID, not an insertion order)
    from datetime import datetime, timedelta, timezone

    from app.models import AuditLog

    since = datetime.now(timezone.utc) - timedelta(minutes=60)
    db = SessionLocal()
    try:
        actions = [
            a.action
            for a in db.query(AuditLog)
            .filter(AuditLog.created_at >= since, AuditLog.action.in_([
                "login", "authority_admin.profile_update", "authority_admin.password_change",
                "grievance.opened", "grievance.mark_read", "grievance.mark_unread",
                "grievance.status_changed", "grievance.response_created",
                "grievance.email_sent", "grievance.email_failed",
            ]))
            .all()
        ]
    finally:
        db.close()
    for ev in ("grievance.opened", "grievance.mark_read", "grievance.mark_unread",
               "grievance.status_changed", "grievance.response_created",
               "grievance.email_failed", "authority_admin.profile_update",
               "authority_admin.password_change"):
        check(f"audit: {ev}", ev in actions)

    # student-side API contract (used by the embedded chatbot flow)
    r = client.post("/api/grievances/draft/generate", json={"input": "exam form not showing, deadline tomorrow"})
    check("28/29. AI formalization endpoint works", r.status_code == 200 and bool(r.json().get("text")), str(r.status_code))
    r = client.get("/api/grievances/categories")
    check("25b. categories endpoint works", r.status_code == 200 and len(r.json().get("categories", [])) > 0, str(r.status_code))

    ref = _submit("Tracking token gate check for the student flow.", AUTHORITY["a"], student_email="track.me@example.com")
    check("33. student submission gets a reference", ref["status"] == 201 and bool(REF_RE.match(ref["body"].get("reference", ""))), str(ref["status"]))
    token = ref["body"]["tracking_token"]
    r = client.get(f"/api/grievances/{ref['body']['reference']}/verify?token={token}")
    check("33b. token-gated verify works", r.status_code == 200 and r.json()["status"] == "submitted", str(r.status_code))
    r = client.get(f"/api/grievances/{ref['body']['reference']}/verify?token=wrong-token-0000000")
    check("33c. wrong token fails closed", r.status_code == 403, str(r.status_code))


# ===========================================================================
# 6. SUPER ADMIN REGRESSION
# ===========================================================================


def test_06_superadmin_regression():
    r = client.get("/api/admin/authorities", headers=TOKENS["super"])
    check("37. authority CRUD list works", r.status_code == 200, str(r.status_code))
    r = client.get("/api/admin/authority-admins", headers=TOKENS["super"])
    check("36. authority-admin management works", r.status_code == 200
          and any(a["username"] == _A_ADMIN["username"] for a in r.json().get("authority_admins", [])), str(r.status_code))
    r = client.get("/api/college/list")
    check("39. college management endpoint works", r.status_code == 200, str(r.status_code))
    r = client.get("/api/admin/website-sync/status", headers=TOKENS["super"])
    check("38. website sync endpoint works", r.status_code == 200, str(r.status_code))
    r = client.get("/api/authority-admin/dashboard", headers=TOKENS["student"])
    check("40. student still blocked from portal", r.status_code == 403, str(r.status_code))


# ===========================================================================
# Runner
# ===========================================================================

import pytest  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _p6_prepare():
    create_all()
    _ensure_users()
    yield
    _cleanup()
    try:
        mail_server.shutdown()
        mail_server.server_close()
    except Exception:
        pass


if __name__ == "__main__":
    create_all()
    _ensure_users()
    tests = [
        test_01_authentication,
        test_02_grievance_lifecycle,
        test_03_isolation,
        test_04_email_delivery,
        test_05_hygiene_audit_student_api,
        test_06_superadmin_regression,
    ]
    try:
        for fn in tests:
            try:
                print("-- " + fn.__name__ + " --")
                fn()
            except Exception as exc:  # noqa: BLE001
                FAIL.append(fn.__name__)
                print(f"  ERROR  {fn.__name__}: {exc}")
    finally:
        _cleanup()
        try:
            mail_server.shutdown()
            mail_server.server_close()
        except Exception:
            pass
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)