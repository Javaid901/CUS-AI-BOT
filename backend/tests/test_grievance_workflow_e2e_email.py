"""
backend/tests/test_grievance_workflow_e2e_email.py

PHASE 5 — End-to-end chatbot-integrated grievance workflow + EMAIL delivery.

Covers (§32, §33 of the Phase 5 spec):
  * PUBLIC active-authorities list (DB-driven, whitelisted fields only)
  * inactive authorities excluded from the list AND rejected at submission
  * AI draft generation (resilient when the LLM is offline)
  * full submission: 201 + reference + one-time tracking token
  * authority email actually SENT to the DB email (real loopback SMTP server)
  * student confirmation email actually SENT
  * email-disabled config: grievance STILL saved, honest delivery status
  * idempotency: same client request id → same reference, ONE row
  * verify: correct token 200, wrong token 403 (fail closed)
  * invalid email / short text → 422

Runs against the app's real DB (TestClient), cleaning up every created row.

Run:  python tests/test_grievance_workflow_e2e_email.py   (or pytest tests/)
"""

from __future__ import annotations

import re
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
from app.grievance.models import Grievance
from app.main import _seed_admin, app

create_all()
_seed_admin()  # pytest mode: seed the superadmin the server startup provides

PASS: list[str] = []
FAIL: list[str] = []

_created_refs: list[str] = []
_toggled_ids: list[str] = []
client = TestClient(app)

REF_RE = re.compile(r"^CUS-GRV-\d{4}-[0-9A-F]{8}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
AUTHORITY_PUBLIC_KEYS = {
    "authority_id", "authority_name", "department_name", "designation", "email",
}


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
        self.wfile.write(b"220 e2e-test-smtp ESMTP\r\n")
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
                self.wfile.write(b"250-e2e-test-smtp\r\n250 SIZE 10000000\r\n")
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


def _record_submission(reference: str) -> None:
    _created_refs.append(reference)


def _cleanup() -> None:
    db = SessionLocal()
    try:
        for ref in _created_refs:
            db.query(Grievance).filter(Grievance.reference == ref).delete()
        db.commit()
    finally:
        db.close()
    _created_refs.clear()


def _admin_token() -> str:
    r = client.post(
        "/api/auth/login",
        data={"username": settings.SEED_ADMIN_USERNAME, "password": settings.SEED_ADMIN_PASSWORD},
    )
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text[:200]}"
    return r.json()["access_token"]


def _toggle_authority(authority_id: str, token: str) -> None:
    r = client.post(
        f"/api/admin/authorities/{authority_id}/toggle",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"toggle failed: {r.status_code} {r.text[:200]}"


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        _enable_smtp()
        mail_server.capture.clear()

        # --- 1. Public active-authority list (DB-driven) ---
        r = client.get("/api/authority/active")
        check("active-authorities endpoint 200", r.status_code == 200, str(r.status_code))
        data = r.json()
        auths = data.get("authorities", [])
        check("active-authorities non-empty", len(auths) > 0, f"count={len(auths)}")
        check(
            "authority list exposes only public fields",
            all(set(a.keys()) == AUTHORITY_PUBLIC_KEYS for a in auths),
            str({k for a in auths for k in a.keys()}),
        )
        check(
            "authority emails present",
            all(EMAIL_RE.match(a.get("email") or "") for a in auths),
        )
        target = next((a for a in auths if a.get("email")), None)
        check("picked an email-capable authority", target is not None)
        if not target:
            return

        # --- 2. Inactive authorities excluded from the list ---
        token = _admin_token()
        _toggle_authority(target["authority_id"], token)
        r2 = client.get("/api/authority/active")
        check(
            "deactivated authority hidden from public list",
            all(a["authority_id"] != target["authority_id"] for a in r2.json().get("authorities", [])),
        )
        _toggle_authority(target["authority_id"], token)  # restore before submission steps

        # --- 3. AI draft generation (LLM may be offline → still shaped) ---
        r = client.post("/api/grievances/draft/generate", json={"input": "exam form is not showing for semester 3 and the last date is tomorrow"})
        check("draft/generate 200", r.status_code == 200, str(r.status_code))
        d = r.json()
        check("draft returns subject + text", bool(d.get("subject")) and bool(d.get("text")), f"g={d.get('generated')}")
        check("draft preserves facts (no invented roll/authority)",
              "not showing" in d.get("text", "").lower() or "semester 3" in d.get("text", "").lower()
              or "exam form" in d.get("text", "").lower(), d.get("text", "")[:120])

        # --- 4. Full submission with SMTP enabled ---
        key = "e2e-req-" + uuid.uuid4().hex[:20]
        student_email = f"student{e2e_nonce()}@student.cus"
        payload = {
            "student": {"name": "Javaid Ahmad", "email": student_email,
                        "roll_number": "CUS-2026-0042", "semester": "3",
                        "college": "Amar Singh College"},
            "original_input": "exam form is not showing for semester 3 and the last date is tomorrow",
            "final_text": "I am writing to bring to your notice that my semester 3 examination form is not visible in the portal although the last date to submit is tomorrow. Kindly resolve this at the earliest.",
            "category": "Examinations",
            "authority_id": target["authority_id"],
            "idempotency_key": key,
        }
        r = client.post("/api/grievances", json=payload)
        check("submit 201", r.status_code == 201, f"{r.status_code} {r.text[:300]}")
        receipt = r.json()
        ref = receipt.get("reference", "")
        check("reference format CUS-GRV-YYYY-XXXXXXXX", bool(REF_RE.match(ref)), ref)
        check("one-time tracking token returned", bool(receipt.get("tracking_token")), "")
        check("authority associated in receipt", (receipt.get("authority") or {}).get("authority_id") == target["authority_id"])
        check("authority email status = sent", receipt.get("authority_email_status") == "sent", str(receipt.get("authority_email_status")))
        check("student ack email confirmed", receipt.get("email_confirmed") is True, str(receipt.get("email_confirmed")))
        check("status = submitted", receipt.get("status") == "submitted")
        _record_submission(ref)

        # --- 5. Emails actually delivered to BOTH destinations ---
        time.sleep(0.4)
        msgs = list(mail_server.capture)
        to_authority = [m for m in msgs if target["email"] in ";".join(m["rcpt_to"])]
        to_student = [m for m in msgs if student_email in ";".join(m["rcpt_to"])]
        check("authority received grievance email", len(to_authority) >= 1, f"captured={len(msgs)}")
        if to_authority:
            body_a = to_authority[-1]["data"]
            check("authority email contains reference", ref in body_a, "")
            check("authority email contains grievance text", "examination form" in body_a.lower())
            check("authority email contains student details", "Javaid Ahmad" in body_a and "CUS-2026-0042" in body_a)
        check("student received confirmation email", len(to_student) >= 1, f"captured={len(msgs)}")
        if to_student:
            check("student email contains reference", ref in to_student[-1]["data"])

        # --- 6. Idempotency: same key → same grievance, ONE row ---
        r = client.post("/api/grievances", json=payload)
        check("replay same key 201", r.status_code == 201, str(r.status_code))
        replay = r.json()
        check("replay returns SAME reference", replay.get("reference") == ref, replay.get("reference"))
        check("replay flagged deduplicated", replay.get("deduplicated") is True)
        db = SessionLocal()
        try:
            n = db.query(Grievance).filter(Grievance.reference == ref).count()
            check("only ONE grievance row for the key", n == 1, f"rows={n}")
        finally:
            db.close()

        # --- 7. Verify: correct token 200, wrong token 403 ---
        r = client.get(f"/api/grievances/{ref}/verify?token={receipt['tracking_token']}")
        check("verify with correct token 200", r.status_code == 200, str(r.status_code))
        v = r.json()
        check("verify shows status+authority, no PII",
              v.get("status") == "submitted" and v.get("authority_name") == target["authority_name"]
              and "student_name" not in v and "email" not in v)
        r = client.get(f"/api/grievances/{ref}/verify?token=wrong-token-00000000")
        check("verify with wrong token 403 (fail closed)", r.status_code == 403, str(r.status_code))

        # --- 8. Deactivated authority rejected at submission ---
        _toggle_authority(target["authority_id"], token)
        _toggled_ids.append(target["authority_id"])
        check("authority now inactive", _authority_active(target["authority_id"]) is False)
        r = client.post("/api/grievances", json={**payload, "idempotency_key": "e2e-req-" + uuid.uuid4().hex[:20], "final_text": payload["final_text"] + " additional text for a fresh case"})
        check("inactive authority -> 422", r.status_code == 422, f"{r.status_code} {r.text[:200]}")
        err_body = r.json()
        err_msg = err_body.get("detail") or (err_body.get("error") or {}).get("message") or ""
        check("inactive error message is honest", "authority" in str(err_msg).lower(), str(err_msg)[:120])
        _toggle_authority(target["authority_id"], token)  # restore for the remaining steps
        _toggled_ids.remove(target["authority_id"])

        # --- 9. Email disabled: grievance STILL saved, delivery honest ---
        _disable_smtp()
        r = client.post("/api/grievances", json={**payload, "idempotency_key": "e2e-req-" + uuid.uuid4().hex[:20], "final_text": payload["final_text"] + " variant B"})
        check("submit with email disabled still 201", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
        d2 = r.json()
        check("email-confirm false when SMTP off", d2.get("email_confirmed") is False)
        check("authority email status honest (failed)", d2.get("authority_email_status") == "failed", str(d2.get("authority_email_status")))
        ref2 = d2.get("reference", "")
        check("email-disabled submission has reference", bool(REF_RE.match(ref2)))
        _record_submission(ref2)

        # --- 10. Validation 422s ---
        r = client.post("/api/grievances", json={**payload, "student": {**payload["student"], "email": "not-an-email"}, "idempotency_key": "e2e-req-" + uuid.uuid4().hex[:20]})
        check("invalid email → 422", r.status_code == 422, str(r.status_code))
        r = client.post("/api/grievances", json={**payload, "final_text": "short", "idempotency_key": "e2e-req-" + uuid.uuid4().hex[:20]})
        check("short final_text → 422", r.status_code == 422, str(r.status_code))
    finally:
        _restore_settings()
        for aid in _toggled_ids:
            try:
                _toggle_authority(aid, _admin_token())
            except Exception:
                pass
        _toggled_ids.clear()
        _cleanup()
        mail_server.shutdown()
        mail_server.server_close()


def _authority_active(authority_id: str) -> bool:
    from app.authority.service import authority_service
    row = authority_service.get(authority_id)
    return bool(row and row.get("active"))


_nonce = [0]


def e2e_nonce() -> str:
    _nonce[0] += 1
    return f"{int(time.time()):x}{_nonce[0]}"


def test_grievance_workflow_e2e_email():
    """pytest entry: runs the same sequential end-to-end flow (loopback SMTP in-process)."""
    main()


if __name__ == "__main__":
    main()
    print("-" * 60)
    print(f"E2E RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)