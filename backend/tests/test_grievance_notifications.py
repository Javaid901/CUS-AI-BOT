"""
backend/tests/test_grievance_notifications.py

Automatic grievance email notifications (additive enhancement, Phase 7).

Covers:
  1. acknowledgement notification  -> student email on status=acknowledged
  2. resolution notification       -> student email on status=resolved
  3. non-notifying transitions     -> no email, no ledger row
  4. response notification logging -> existing response email is recorded
  5. notification delivery log     -> grievance_id / event_type / recipient /
                                       status / attempts / timestamps
  6. retry / failure handling      -> SMTP down: operation succeeds honestly,
                                       row marked failed, retried later
  7. duplicate protection          -> already-sent event never re-emails
  8. correct recipient             -> student from grievance, authority from DB
  9. authority isolation           -> admin A affects only A's grievances
 10. student isolation             -> emails only reach the grievance student

Design guarantees exercised:
  * email failure NEVER rolls back a status change / response / submission
  * no secrets in the notification log
  * idempotent notification events (unique grievance+event+role key)

Runs against the app's real DB (TestClient), cleaning up every created row.

Run:  python tests/test_grievance_notifications.py   (or pytest tests/)
"""

from __future__ import annotations

import json
import socketserver
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register models before any session)

from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal, create_all
from app.grievance.models import Grievance, GrievanceNotification, GrievanceStatusHistory
from app.grievance.notifications import notify_status_change
from app.main import app
from app.models import Authority, User

create_all()

PASS: list[str] = []
FAIL: list[str] = []

_created_authority_ids: list[str] = []
_created_user_ids: list[str] = []
_created_grievance_ids: list[str] = []
client = TestClient(app)

_ADMIN_A = {"username": "", "password": "notifpass1"}
_ADMIN_B = {"username": "", "password": "notifpass1"}
TOKENS: dict[str, dict[str, str]] = {}
AUTHORITY: dict[str, str] = {}


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
# Minimal real SMTP server on loopback (in-process test mail server)
# ---------------------------------------------------------------------------

class _SmtpHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.wfile.write(b"220 notif-test-smtp ESMTP\r\n")
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
                self.wfile.write(b"250-notif-test-smtp\r\n250 SIZE 10000000\r\n")
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


# ---------------------------------------------------------------------------
# Settings helpers (saved/restored around the run)
# ---------------------------------------------------------------------------

_SAVED = {k: getattr(settings, k) for k in (
    "EMAIL_ENABLED", "SMTP_HOST", "SMTP_PORT", "SMTP_STARTTLS",
    "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "MAIL_FROM_NAME",
    "GRIEVANCE_CREATE_LIMIT",
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
# Seeds (mirror the Phase 6 portal test harness)
# ---------------------------------------------------------------------------

def _make_authority(name: str) -> str:
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
            active=True,
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
    from app.authority.service import authority_service

    db = SessionLocal()
    try:
        authority_service.load_cache(db)
    finally:
        db.close()


def _create_user(username: str, password: str, role: str, authority_id: str | None = None) -> None:
    from app.auth.security import hash_password
    from app.models import User

    db = SessionLocal()
    try:
        user = User(
            id=uuid.uuid4(),
            username=username,
            email=f"{username}@test.local",
            hashed_password=hash_password(password),
            role=role,
            is_active=True,
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
    _ADMIN_A["username"] = f"__nt_adm_a_{uuid.uuid4().hex[:6]}"
    _ADMIN_B["username"] = f"__nt_adm_b_{uuid.uuid4().hex[:6]}"
    AUTHORITY["a"] = _make_authority("NotifAdmission")
    AUTHORITY["b"] = _make_authority("NotifExams")
    _reload_authority_cache()
    _create_user(_ADMIN_A["username"], _ADMIN_A["password"], "authority_admin", AUTHORITY["a"])
    _create_user(_ADMIN_B["username"], _ADMIN_B["password"], "authority_admin", AUTHORITY["b"])
    for key, creds in (("a", _ADMIN_A), ("b", _ADMIN_B)):
        r = client.post("/api/auth/login", data={"username": creds["username"], "password": creds["password"]})
        if r.status_code == 200:
            TOKENS[key] = {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_grievance(authority_id: str, student_email: str | None, status: str = "submitted") -> str:
    """Create a grievance row directly (intake emails are covered elsewhere)."""
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        g = Grievance(
            reference=f"CUS-GRV-{datetime.now(timezone.utc).year}-{uuid.uuid4().hex[:8].upper()}",
            authority_id=authority_id,
            student_name="Notif Test Student",
            student_email=student_email,
            roll_number="24601",
            semester="4",
            college="Amar Singh College",
            category="Examinations",
            final_grievance_text="Test grievance text for the notification suite.",
            status=status,
            source_kind="pre_login",
            submitted_at=datetime.now(timezone.utc),
        )
        db.add(g)
        db.commit()
        db.refresh(g)
        gid = str(g.id)
        _created_grievance_ids.append(gid)
        return gid
    finally:
        db.close()


def _load_grievance(gid: str) -> Grievance:
    db = SessionLocal()
    try:
        return db.query(Grievance).filter(Grievance.id == gid).first()
    finally:
        db.close()


def _ledger_rows(gid: str) -> list[GrievanceNotification]:
    db = SessionLocal()
    try:
        return (
            db.query(GrievanceNotification)
            .filter(GrievanceNotification.grievance_id == gid)
            .order_by(GrievanceNotification.created_at)
            .all()
        )
    finally:
        db.close()


def _msgs_to(email: str) -> list[dict]:
    return [m for m in mail_server.capture if email in ";".join(m["rcpt_to"])]


def _authority_email(authority_id: str) -> str:
    from app.grievance.intake import authority_summary

    return (authority_summary(authority_id) or {}).get("email") or ""


def _cleanup() -> None:
    db = SessionLocal()
    try:
        for gid in _created_grievance_ids:
            db.query(Grievance).filter(Grievance.id == gid).delete()
        for uid in _created_user_ids:
            db.query(User).filter(User.id == uid).delete()
        for aid in _created_authority_ids:
            db.query(Authority).filter(Authority.id == aid).delete()
        db.commit()
    finally:
        db.close()
    for lst in (_created_grievance_ids, _created_user_ids, _created_authority_ids):
        lst.clear()


def _status_body(gid: str, new_status: str):
    return client.post(
        f"/api/authority-admin/grievances/{gid}/status",
        json={"new_status": new_status},
        headers=TOKENS["a"],
    )


def _response_body(gid: str, text: str):
    return client.post(
        f"/api/authority-admin/grievances/{gid}/response",
        json={"response": text},
        headers=TOKENS["a"],
    )


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        # This suite shares the dev DB + the "testclient" IP with sibling
        # suites; disable the per-minute submission limiter for its duration
        # so it never contributes to cross-suite 429 flakiness (restored below).
        settings.GRIEVANCE_CREATE_LIMIT = 0
        _enable_smtp()
        mail_server.capture.clear()
        _ensure_users()
        if "a" not in TOKENS:
            check("admin logins", False, "could not obtain portal tokens")
            return

        stu_a = f"student_a{int(time.time()):x}@student.cus"
        stu_b = f"student_b{int(time.time()):x}@student.cus"
        auth_email_a = _authority_email(AUTHORITY["a"])
        auth_email_b = _authority_email(AUTHORITY["b"])
        check("authority emails present in DB", bool(auth_email_a and auth_email_b))

        # =================================================================
        # 1. ACKNOWLEDGEMENT notification (status -> acknowledged)
        # =================================================================
        g_ack = _make_grievance(AUTHORITY["a"], stu_a)
        before = len(mail_server.capture)
        r = _status_body(g_ack, "acknowledged")
        check("acknowledge via portal 200", r.status_code == 200, str(r.status_code))
        n = r.json().get("notification") or {}
        check("ack notification event reported", n.get("event_type") == "grievance_acknowledged", str(n))
        check("ack notification delivered", n.get("status") == "sent", str(n.get("status")))
        check("ack notification not deduplicated", n.get("deduplicated") is False)

        rows = _ledger_rows(g_ack)
        row = next((x for x in rows if x.event_type == "grievance_acknowledged"), None)
        check("ack ledger row exists", row is not None)
        if row:
            check("ack ledger status sent", row.status == "sent", row.status)
            check("ack ledger recipient = student from grievance", row.recipient_email == stu_a, row.recipient_email)
            check("ack ledger role student", row.recipient_role == "student", row.recipient_role)
            check("ack ledger attempt count 1", row.retry_count == 1, str(row.retry_count))
            check("ack ledger attempted+sent timestamps", row.attempted_at is not None and row.sent_at is not None)
        msgs_a = _msgs_to(stu_a)
        check("ack email delivered to student", len(msgs_a) >= 1, f"sent={len(msgs_a)}")
        if msgs_a:
            body = msgs_a[-1]["data"]
            check("ack email mentions reference", _created_ref(g_ack) in body)
            check("ack email acknowledges status", "Acknowledged" in body or "acknowledged" in body.lower())
            check("ack email recipient is the student (not authority)", stu_a in ";".join(msgs_a[-1]["rcpt_to"]))
        new_emails = len(mail_server.capture) - before
        check("ack only ONE new email", new_emails == 1, f"new={new_emails}")

        # =================================================================
        # 2. Non-notifying transition -> no email, no ledger row
        # =================================================================
        g_prog = _make_grievance(AUTHORITY["a"], stu_a)
        before = len(mail_server.capture)
        r = _status_body(g_prog, "in_progress")
        check("in_progress via portal 200", r.status_code == 200)
        check("no notification for in_progress", r.json().get("notification") is None)
        check("no email for in_progress", len(mail_server.capture) == before)
        check("no ledger row for in_progress", len(_ledger_rows(g_prog)) == 0)

        # =================================================================
        # 3. RESOLUTION notification (status -> resolved)
        # =================================================================
        g_res = _make_grievance(AUTHORITY["a"], stu_a)
        r = _status_body(g_res, "resolved")
        check("resolve via portal 200", r.status_code == 200)
        n = r.json().get("notification") or {}
        check("resolution notification delivered", n.get("status") == "sent" and n.get("event_type") == "grievance_resolved", str(n))
        row = next((x for x in _ledger_rows(g_res) if x.event_type == "grievance_resolved"), None)
        check("resolution ledger row sent", row is not None and row.status == "sent" and row.recipient_email == stu_a)
        msgs_a = _msgs_to(stu_a)
        check("resolution email delivered", len(msgs_a) >= 1)
        check("student got NO authority-side copies",
              all(auth_email_a not in ";".join(m["rcpt_to"]) for m in msgs_a))
        # source of truth: status chain recorded resolved
        db = SessionLocal()
        try:
            hist = (db.query(GrievanceStatusHistory)
                    .filter(GrievanceStatusHistory.grievance_id == g_res)
                    .order_by(GrievanceStatusHistory.created_at.desc()).first())
            check("status history records resolved", bool(hist) and hist.new_status == "resolved", str(hist))
        finally:
            db.close()

        # =================================================================
        # 4. Response email logged (existing sender + ledger entry)
        # =================================================================
        g_rsp = _make_grievance(AUTHORITY["a"], stu_a)
        before = len(mail_server.capture)
        r = _response_body(g_rsp, "Your examination form issue has been fixed. Please check the portal now.")
        check("response via portal 200", r.status_code == 200, str(r.status_code))
        check("response_email_status sent", r.json().get("response_email_status") == "sent", str(r.json()))
        row = next((x for x in _ledger_rows(g_rsp) if x.event_type == "grievance_response"), None)
        check("response ledger row sent", row is not None and row.status == "sent" and row.recipient_email == stu_a, str(row))
        check("response email delivered (exactly one new)", len(mail_server.capture) == before + 1, f"count={len(mail_server.capture)-before}")
        if len(mail_server.capture) == before + 1:
            msgs = mail_server.capture[before:]
            check("response email carries reference", _created_ref(g_rsp) in msgs[-1]["data"])
            check("response email only to student", stu_a in ";".join(msgs[-1]["rcpt_to"]) and auth_email_a not in ";".join(msgs[-1]["rcpt_to"]))

        # =================================================================
        # 5. Authority isolation: admin B only affects B's grievance
        # =================================================================
        g_b = _make_grievance(AUTHORITY["b"], stu_b)
        r = client.post(
            f"/api/authority-admin/grievances/{g_b}/status",
            json={"new_status": "acknowledged"},
            headers=TOKENS["b"],
        )
        check("admin B acknowledges B grievance 200", r.status_code == 200, str(r.status_code))
        rows_b = _ledger_rows(g_b)
        check("B grievance has ack ledger", any(x.event_type == "grievance_acknowledged" for x in rows_b))
        # A's admin must NOT reach B's grievance (identity-scoped portal)
        r = client.get(f"/api/authority-admin/grievances/{g_b}", headers=TOKENS["a"])
        check("admin A cannot open B grievance (404)", r.status_code == 404, str(r.status_code))
        # No email to A's student from B's ack
        check("B ack did not email A's student", len(_msgs_to(stu_a)) == 3, str(len(_msgs_to(stu_a))))
        # B's student got exactly the ack email
        check("A ack emails did not reach B's student", len(_msgs_to(stu_b)) == 1, str(len(_msgs_to(stu_b))))

        # =================================================================
        # 6. Duplicate protection: already-sent event never re-emails
        # =================================================================
        before = len(mail_server.capture)
        s6 = SessionLocal()
        try:
            g = s6.query(Grievance).filter(Grievance.id == g_ack).first()
            dup = notify_status_change(s6, g, "acknowledged")
        finally:
            s6.close()
        check("re-invoked ack flagged deduplicated", dup and dup.get("deduplicated") is True, str(dup))
        check("no duplicate email on retry", len(mail_server.capture) == before, f"before={before}")
        row = next((x for x in _ledger_rows(g_ack) if x.event_type == "grievance_acknowledged"), None)
        check("no duplicate ledger row (still one, attempts=1)",
              row is not None and row.retry_count == 1, str(row.retry_count))
        # API level: second acknowledge is a no-op 409 — nothing re-sent
        r = _status_body(g_ack, "acknowledged")
        check("API second acknowledge -> 409", r.status_code == 409, str(r.status_code))
        check("no email after API 409", len(mail_server.capture) == before)

        # =================================================================
        # 7. SMTP OFF: operation succeeds honestly, notification failed+logged
        # =================================================================
        g_off = _make_grievance(AUTHORITY["a"], stu_a)
        _disable_smtp()
        r = _status_body(g_off, "resolved")
        check("resolve with SMTP down still 200", r.status_code == 200, str(r.status_code))
        n = r.json().get("notification") or {}
        check("resolution honestly failed", n.get("status") == "failed", str(n.get("status")))
        db = SessionLocal()
        try:
            g_row = db.query(Grievance).filter(Grievance.id == g_off).first()
            check("grievance STILL resolved (no rollback)", g_row is not None and g_row.status == "resolved", str(g_row.status if g_row else None))
        finally:
            db.close()
        row = next((x for x in _ledger_rows(g_off) if x.event_type == "grievance_resolved"), None)
        check("failed resolution logged", row is not None and row.status == "failed")
        if row:
            check("failed row has attempted_at, no sent_at",
                  row.attempted_at is not None and row.sent_at is None)
            check("failed row records error detail", bool(row.error_message), str(row.error_message))

        # Retry semantics: retry while SMTP down -> attempts bump, no dup row
        before = len(mail_server.capture)
        s7 = SessionLocal()
        try:
            g7 = s7.query(Grievance).filter(Grievance.id == g_off).first()
            r2 = notify_status_change(s7, g7, "resolved")
            check("retry while SMTP down still failed", r2 and r2.get("status") == "failed", str(r2))
            check("retry while down sends nothing", len(mail_server.capture) == before)
            after_retry = (
                s7.query(GrievanceNotification)
                .filter(
                    GrievanceNotification.grievance_id == g7.id,
                    GrievanceNotification.event_type == "grievance_resolved",
                    GrievanceNotification.recipient_role == "student",
                )
                .first()
            )
            check("retry bumps attempt count on SAME row",
                  after_retry is not None and after_retry.retry_count == 2,
                  str(after_retry.retry_count if after_retry else None))

            # SMTP back up: retrying the same event succeeds on the same row
            _enable_smtp()
            before = len(mail_server.capture)
            r3 = notify_status_change(s7, g7, "resolved")
            s7.commit()
            check("retry after SMTP recovery succeeds", r3 and r3.get("status") == "sent", str(r3))
            check("recovery email delivered (one)", len(mail_server.capture) == before + 1, f"count={len(mail_server.capture)-before}")
        finally:
            s7.close()
        row = next((x for x in _ledger_rows(g_off) if x.event_type == "grievance_resolved"), None)
        check("recovery row now sent with attempts=3", row is not None and row.status == "sent" and row.retry_count == 3, str(row))

        # =================================================================
        # 8. No student email -> skipped, no send, operation still succeeds
        # =================================================================
        g_none = _make_grievance(AUTHORITY["a"], None)
        before = len(mail_server.capture)
        r = _status_body(g_none, "acknowledged")
        check("acknowledge w/o student email 200", r.status_code == 200)
        n = r.json().get("notification") or {}
        check("no-email notification skipped", n.get("status") == "skipped", str(n))
        check("no email sent for skipped", len(mail_server.capture) == before)
        row = next((x for x in _ledger_rows(g_none) if x.event_type == "grievance_acknowledged"), None)
        check("skipped row logged", row is not None and row.status == "skipped", str(row))

        # =================================================================
        # 9. Submission ledger + recipient discipline (authority from DB)
        # =================================================================
        g_sub = client.post("/api/grievances", json={
            "student": {"name": "Ledger Student", "email": stu_a, "roll_number": "99999", "semester": "5", "college": "Amar Singh College"},
            "final_text": "My ledger submission grievance about examination forms.",
            "original_input": "exam form issue",
            "category": "Examinations",
            "authority_id": AUTHORITY["a"],
            "idempotency_key": "nt-sub-" + uuid.uuid4().hex[:20],
        })
        check("submission 201", g_sub.status_code == 201, str(g_sub.status_code))
        sub_ref = g_sub.json().get("reference", "")
        db9 = SessionLocal()
        try:
            gid = str(db9.query(Grievance).filter(Grievance.reference == sub_ref).first().id)
        finally:
            db9.close()
        _created_grievance_ids.append(gid)
        rows = _ledger_rows(gid)
        sub_stu = next((x for x in rows if x.event_type == "grievance_submitted" and x.recipient_role == "student"), None)
        sub_auth = next((x for x in rows if x.event_type == "grievance_submitted" and x.recipient_role == "authority"), None)
        check("submission student ledger row", sub_stu is not None and sub_stu.status == "sent" and sub_stu.recipient_email == stu_a, str(sub_stu))
        check("submission authority ledger row uses DB email", sub_auth is not None and sub_auth.recipient_email == auth_email_a, str(sub_auth))
        # Wait for delivery, then confirm both captured recipients
        time.sleep(0.3)
        check("submission emails captured", len(_msgs_to(stu_a)) >= 4 and len(_msgs_to(auth_email_a)) >= 1)

        # =================================================================
        # 10. Ledger hygiene: no secrets anywhere in notification rows
        # =================================================================
        db = SessionLocal()
        try:
            sextets = ("password", "secret", "token", "authorization", "smtp_", "_key")
            dirty = []
            for gid in _created_grievance_ids:
                for x in _ledger_rows(gid):
                    blob = json.dumps({
                        "event_type": x.event_type, "recipient_email": x.recipient_email,
                        "status": x.status, "error_message": x.error_message,
                    }).lower()
                    if any(s in blob for s in sextets):
                        dirty.append(str(x.id))
            check("no secrets in notification log", not dirty, ",".join(dirty[:3]))
        finally:
            db.close()
    finally:
        _restore_settings()
        _cleanup()
        mail_server.shutdown()
        mail_server.server_close()


def _created_ref(gid: str) -> str:
    _g = _load_grievance(gid)
    return _g.reference if _g else "???"


def test_grievance_notifications():
    """pytest entry: runs the full sequential notification suite."""
    main()


if __name__ == "__main__":
    main()
    print("-" * 60)
    print(f"NOTIFICATIONS RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)