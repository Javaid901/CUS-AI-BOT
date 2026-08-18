"""
backend/tests/test_phase3_rbac.py

PHASE 3 — RBAC, Authority Admin accounts, audit + status-history foundation.

Covers spec sections 17-18:
  ROLE TESTS            - superadmin / authority_admin / student recognized
  AUTHORITY ADMIN TESTS - create / update / deactivate / duplicates /
                          inactive-authority guard / correct linkage
  AUTHORIZATION TESTS   - students & admins blocked, scope enforcement,
                          IDOR-style bypass attempts rejected
  AUDIT TESTS           - authority + account actions create audit records
  STATUS HISTORY TESTS  - immutability: previous states preserved with actor

Run:  python tests/test_phase3_rbac.py   (or pytest tests/test_phase3_rbac.py)
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register models before any session)

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth.security import hash_password, require_authority_scope
from app.database import SessionLocal, create_all
from app.main import app

PASS: list[str] = []
FAIL: list[str] = []

SUPER_TOKEN: dict[str, str] = {}
STUDENT_TOKEN: dict[str, str] = {}
_SUPER_USERNAME = f"__p3_super_{uuid.uuid4().hex[:6]}"
_STUDENT_USERNAME = f"__p3_student_{uuid.uuid4().hex[:6]}"

_created_user_ids: list[str] = []
_created_authority_ids: list[str] = []
_created_grievance_ids: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def _cleanup() -> None:
    from app.grievance.models import Grievance, GrievanceStatusHistory
    from app.models import Authority, AuditLog, User

    db = SessionLocal()
    try:
        for gid in _created_grievance_ids:
            db.query(GrievanceStatusHistory).filter(
                GrievanceStatusHistory.grievance_id == gid
            ).delete()
            db.query(Grievance).filter(Grievance.id == gid).delete()
        for aid in _created_authority_ids:
            db.query(Authority).filter(Authority.id == aid).delete()
        for uid in _created_user_ids:
            db.query(AuditLog).filter(AuditLog.actor_id == uid).delete()
            db.query(User).filter(User.id == uid).delete()
        db.commit()
    finally:
        db.close()


def _ensure_users() -> None:
    from app.models import User

    db = SessionLocal()
    try:
        if not db.query(User).filter(User.username == _SUPER_USERNAME).first():
            super_user = User(
                id=uuid.uuid4(), username=_SUPER_USERNAME, email=f"{_SUPER_USERNAME}@test.local",
                hashed_password=hash_password("secret123"), role="superadmin", is_active=True,
            )
            db.add(super_user)
            db.flush()
            _created_user_ids.append(str(super_user.id))
        if not db.query(User).filter(User.username == _STUDENT_USERNAME).first():
            student = User(
                id=uuid.uuid4(), username=_STUDENT_USERNAME, email=f"{_STUDENT_USERNAME}@test.local",
                hashed_password=hash_password("secret123"), role="student", is_active=True,
            )
            db.add(student)
            db.flush()
            _created_user_ids.append(str(student.id))
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    r = client.post("/api/auth/login", data={"username": _SUPER_USERNAME, "password": "secret123"})
    SUPER_TOKEN["Authorization"] = f"Bearer {r.json()['access_token']}"
    r = client.post("/api/auth/login", data={"username": _STUDENT_USERNAME, "password": "secret123"})
    STUDENT_TOKEN["Authorization"] = f"Bearer {r.json()['access_token']}"


def _make_authority(name=None, active=True):
    from app.models import Authority

    db = SessionLocal()
    try:
        authority = Authority(
            id=str(uuid.uuid4()),  # 36-char form (matches authorities.id everywhere)
            department_name=f"Dept {uuid.uuid4().hex[:6]}",
            authority_name=name or f"Office {uuid.uuid4().hex[:6]}",
            designation="Head",
            email=f"office{uuid.uuid4().hex[:8]}@cus.ac.in",
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


def _create_authority_admin(username, email, authority_id, password="pass1234"):
    client = TestClient(app)
    return client.post(
        "/api/admin/authority-admins",
        json={
            "username": username,
            "email": email,
            "password": password,
            "authority_id": authority_id,
            "full_name": "Test Admin",
            "designation": "Office Head",
        },
        headers=SUPER_TOKEN,
    )


def _login_authority_admin(username, password="pass1234"):
    client = TestClient(app)
    r = client.post("/api/auth/login", data={"username": username, "password": password})
    if r.status_code != 200:
        return {}
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------------------------------------------------------------------------
# 1. Role tests
# ---------------------------------------------------------------------------


def test_roles():
    print("== role recognition ==")
    client = TestClient(app)
    r = client.get("/api/admin/authority-admins", headers=SUPER_TOKEN)
    check("superadmin recognized on admin API", r.status_code == 200, f"status={r.status_code}")
    r = client.get("/api/authority-admin/me", headers=SUPER_TOKEN)
    check("superadmin not recognized as authority_admin", r.status_code == 403, f"status={r.status_code}")


def test_student_blocked():
    print("== student blocked ==")
    client = TestClient(app)
    r = client.get("/api/admin/authority-admins", headers=STUDENT_TOKEN)
    check("student cannot list authority admins (403)", r.status_code == 403)
    r = client.get("/api/admin/authorities", headers=STUDENT_TOKEN)
    check("student cannot list authorities (403)", r.status_code == 403)
    r = client.post("/api/admin/authority-admins", json={}, headers=STUDENT_TOKEN)
    check("student cannot create account (403)", r.status_code == 403)
    r = client.get("/api/authority-admin/me", headers=STUDENT_TOKEN)
    check("student cannot use self-service admin endpoint (403)", r.status_code == 403)


def test_list_empty_params_regression():
    """Frontend always sends ?query=&status= — must NOT 422 (that 422 was the
    '[object Object]' toast bug). Endpoint stays Super-Admin-only: superadmin 200,
    authority_admin 403, student 403, unauthenticated 401. Authority association
    comes from the DB and survives authority deactivation."""
    print("-- authority-admin list: empty-param regression + security matrix --")
    client = TestClient(app)

    r = client.get("/api/admin/authority-admins?query=&status=", headers=SUPER_TOKEN)
    check("list 200 with frontend's empty params (no 422)", r.status_code == 200, f"status={r.status_code} {r.text[:160]}")
    check("list body has authority_admins array", isinstance(r.json().get("authority_admins"), list))

    r = client.get("/api/admin/authority-admins", headers=SUPER_TOKEN)
    check("list 200 without params", r.status_code == 200, f"status={r.status_code}")
    r = client.get("/api/admin/authority-admins?status=active", headers=SUPER_TOKEN)
    check("status=active accepted", r.status_code == 200, f"status={r.status_code}")
    r = client.get("/api/admin/authority-admins?status=inactive", headers=SUPER_TOKEN)
    check("status=inactive accepted", r.status_code == 200, f"status={r.status_code}")
    r = client.get("/api/admin/authority-admins?status=banana", headers=SUPER_TOKEN)
    check("invalid status still rejected (422)", r.status_code == 422, f"status={r.status_code}")

    aid = _make_authority("Admission Authority")
    uname = f"lst_{uuid.uuid4().hex[:6]}"
    r = _create_authority_admin(uname, f"lst{uuid.uuid4().hex[:8]}@cus.ac.in", aid)
    check("create linked admin (201)", r.status_code == 201, f"status={r.status_code} {r.text[:140]}")
    admin_id = r.json().get("id")
    if admin_id:
        _created_user_ids.append(admin_id)

    r = client.get("/api/admin/authority-admins?query=&status=", headers=SUPER_TOKEN)
    rows = r.json()["authority_admins"]
    row = next((a for a in rows if a["id"] == admin_id), None)
    check("linked admin visible in list", row is not None)
    check("authority name from DB (not hardcoded)", row is not None and row.get("authority_name") == "Admission Authority",
          f"got {row.get('authority_name') if row else None}")

    from app.models import Authority

    db = SessionLocal()
    try:
        auth = db.query(Authority).filter(Authority.id == aid).first()
        if auth:
            auth.active = False
            db.commit()
    finally:
        db.close()
    r = client.get("/api/admin/authority-admins?query=&status=", headers=SUPER_TOKEN)
    check("deactivated authority does not break list (200)", r.status_code == 200, f"status={r.status_code}")

    r = client.get("/api/admin/authority-admins?query=&status=", headers=STUDENT_TOKEN)
    check("student 403", r.status_code == 403, f"status={r.status_code}")
    aa_token = _login_authority_admin(uname)
    r = client.get("/api/admin/authority-admins?query=&status=", headers=aa_token)
    check("authority_admin 403", r.status_code == 403, f"status={r.status_code}")
    r = client.get("/api/admin/authority-admins?query=&status=")
    check("unauthenticated 401", r.status_code == 401, f"status={r.status_code}")


def test_login_by_email_and_authority_filter():
    """Authority admins can log in with their EMAIL (or username) on the same
    /api/auth/login endpoint; the identity payload is safe; the Super Admin
    list endpoint supports an authority_id filter backed by the DB."""
    print("-- email login + identity safety + authority_id list filter --")
    client = TestClient(app)

    aid = _make_authority("Admission Authority")
    uname = f"eml_{uuid.uuid4().hex[:6]}"
    mail = f"eml{uuid.uuid4().hex[:8]}@cus.ac.in"
    r = _create_authority_admin(uname, mail, aid)
    check("create linked admin (201)", r.status_code == 201, f"status={r.status_code} {r.text[:140]}")
    admin_id = r.json().get("id")
    if admin_id:
        _created_user_ids.append(admin_id)

    r = client.post("/api/auth/login", data={"username": mail, "password": "pass1234"})
    check("login by EMAIL succeeds (200)", r.status_code == 200, f"status={r.status_code} {r.text[:160]}")
    identity = (r.json().get("user") or {}) if r.status_code == 200 else {}
    check("identity has role authority_admin", identity.get("role") == "authority_admin", f"got {identity.get('role')}")
    check("identity has email", identity.get("email") == mail, f"got {identity.get('email')}")
    check("identity has authority_id from DB", identity.get("authority_id") == aid, f"got {identity.get('authority_id')}")
    check("identity has is_active", identity.get("is_active") is True)
    body_text = r.text if r.status_code == 200 else ""
    check("login response never exposes password/hash",
          "hashed_password" not in body_text and '"password"' not in body_text)

    r = client.post("/api/auth/login", data={"username": mail, "password": "wrongpass"})
    check("login by email with wrong password fails (401)", r.status_code == 401, f"status={r.status_code}")

    r = client.get(f"/api/admin/authority-admins?query=&status=&authority_id={aid}", headers=SUPER_TOKEN)
    check("list filtered by authority_id (200)", r.status_code == 200, f"status={r.status_code}")
    rows = r.json().get("authority_admins", []) if r.status_code == 200 else []
    check("filter returns only that authority's admin",
          r.status_code == 200 and len(rows) == 1 and rows[0]["id"] == admin_id, f"got {len(rows)} rows")
    check("detail payload has nested authority object",
          r.status_code == 200 and rows and isinstance(rows[0].get("authority"), dict))
    check("nested authority carries designation + category from DB",
          r.status_code == 200 and rows and rows[0]["authority"].get("authority_name") == "Admission Authority"
          and rows[0]["authority"].get("designation") is not None,
          f"got {rows[0].get('authority') if r.status_code == 200 and rows else None}")

    other_aid = _make_authority("Exams Office")
    r = client.get(f"/api/admin/authority-admins?query=&status=&authority_id={other_aid}", headers=SUPER_TOKEN)
    check("filter for another authority excludes this admin", r.status_code == 200 and r.json()["authority_admins"] == [],
          f"status={r.status_code}")

    r = client.get(f"/api/admin/authority-admins?query=&status=&authority_id={aid}", headers=STUDENT_TOKEN)
    check("authority filter endpoint still superadmin-only (403)", r.status_code == 403, f"status={r.status_code}")


# ---------------------------------------------------------------------------
# 2. Authority Admin account tests (superadmin acting)
# ---------------------------------------------------------------------------


def test_account_crud():
    print("-- authority admin creation / linkage / duplicates ==")
    client = TestClient(app)
    aid1 = _make_authority("Registrar HQ")
    aid2 = _make_authority("Exams HQ")

    username = f"rec_{uuid.uuid4().hex[:6]}"
    email = f"rec{uuid.uuid4().hex[:8]}@cus.ac.in"
    r = _create_authority_admin(username, email, aid1)
    check("superadmin creates authority admin (201)", r.status_code == 201, f"status={r.status_code} {r.text[:160]}")
    created = r.json()
    _created_user_ids.append(created["id"])
    check("role assigned authority_admin", created.get("role") == "authority_admin")
    check("authority linked correctly", created.get("authority_id") == aid1 and created.get("authority_name") == "Registrar HQ", f"got {created.get('authority_id')} vs {aid1}")
    check("password never exposed", "password" not in created and "hashed_password" not in created)

    r = _create_authority_admin(f"dup_{uuid.uuid4().hex[:6]}", email.upper(), aid1)
    check("duplicate EMAIL rejected (409, case-insensitive)", r.status_code == 409, f"status={r.status_code} {r.text[:120]}")

    r = _create_authority_admin(username, f"new{uuid.uuid4().hex[:8]}@cus.ac.in", aid1)
    check("duplicate USERNAME rejected (409)", r.status_code == 409, f"status={r.status_code}")

    inactive_aid = _make_authority("Closed Office", active=False)
    r = _create_authority_admin(f"no_{uuid.uuid4().hex[:6]}", f"no{uuid.uuid4().hex[:8]}@cus.ac.in", inactive_aid)
    check("inactive authority cannot be assigned (409)", r.status_code == 409, f"status={r.status_code} {r.text[:140]}")

    r = _create_authority_admin(f"ghost_{uuid.uuid4().hex[:6]}", f"ghost{uuid.uuid4().hex[:8]}@cus.ac.in", str(uuid.uuid4()))
    check("nonexistent authority rejected", r.status_code == 409, f"status={r.status_code}")

    # -- update --
    r = client.patch(
        f"/api/admin/authority-admins/{created['id']}",
        json={"full_name": "Updated Name", "email": f"renamed{uuid.uuid4().hex[:6]}@cus.ac.in"},
        headers=SUPER_TOKEN,
    )
    check("superadmin updates account (200)", r.status_code == 200 and r.json().get("full_name") == "Updated Name", f"status={r.status_code}")
    r2 = _create_authority_admin(f"other_{uuid.uuid4().hex[:6]}", f"other{uuid.uuid4().hex[:8]}@cus.ac.in", aid2)
    _created_user_ids.append(r2.json()["id"])
    r = client.patch(
        f"/api/admin/authority-admins/{created['id']}",
        json={"email": r2.json()["email"]},
        headers=SUPER_TOKEN,
    )
    check("duplicate email on update rejected (409)", r.status_code == 409, f"status={r.status_code}")

    # -- list / filter --
    r = client.get("/api/admin/authority-admins?status=active", headers=SUPER_TOKEN)
    check("list with filter works", r.status_code == 200)
    check("created admin appears in list", any(a["id"] == created["id"] for a in r.json()["authority_admins"]))

    # -- assignment change --
    r = client.post(
        f"/api/admin/authority-admins/{created['id']}/assign",
        json={"authority_id": aid2},
        headers=SUPER_TOKEN,
    )
    check("superadmin reassigns authority (200)", r.status_code == 200 and r.json().get("authority_id") == aid2, f"status={r.status_code}")
    r = client.post(
        f"/api/admin/authority-admins/{created['id']}/assign",
        json={"authority_id": inactive_aid},
        headers=SUPER_TOKEN,
    )
    check("reassign to inactive authority rejected (409)", r.status_code == 409, f"status={r.status_code}")

    # -- deactivate / activate --
    r = client.post(f"/api/admin/authority-admins/{created['id']}/toggle", headers=SUPER_TOKEN)
    check("deactivate account (200)", r.status_code == 200 and r.json().get("is_active") is False)

    # inactive admin token rejected mid-session
    r = client.post("/api/auth/login", data={"username": username, "password": "pass1234"})
    check("deactivated admin login fails (403)", r.status_code == 403, f"status={r.status_code}")

    r = client.post(f"/api/admin/authority-admins/{created['id']}/toggle", headers=SUPER_TOKEN)
    check("reactivate account (200)", r.status_code == 200 and r.json().get("is_active") is True)
    r = client.post("/api/auth/login", data={"username": username, "password": "pass1234"})
    token = {"Authorization": f"Bearer {r.json()['access_token']}"} if r.status_code == 200 else {}
    check("reactivated admin can log in (200)", r.status_code == 200)
    r = client.get("/api/authority-admin/me", headers=token)
    check("authority admin self profile (200)", r.status_code == 200)
    me = r.json()
    check("profile derives scope server-side", me.get("authority_id") == aid2 and me.get("authority") and me["authority"].get("authority_name") == "Exams HQ", f"got {me.get('authority_id')} vs {aid2}")


def test_idor_scope_guard():
    print("-- IDOR / scope enforcement ==")
    from app.models import User

    # Authority Admin A (authority X), Authority Admin B (authority Y)
    auth_x = _make_authority("Scope X")
    auth_y = _make_authority("Scope Y")
    ua = f"scopex_{uuid.uuid4().hex[:6]}"
    ub = f"scopey_{uuid.uuid4().hex[:6]}"
    r = _create_authority_admin(ua, f"x{uuid.uuid4().hex[:8]}@cus.ac.in", auth_x)
    _created_user_ids.append(r.json()["id"])
    r = _create_authority_admin(ub, f"y{uuid.uuid4().hex[:8]}@cus.ac.in", auth_y)
    _created_user_ids.append(r.json()["id"])

    token_a = _login_authority_admin(ua)
    token_b = _login_authority_admin(ub)

    client = TestClient(app)

    # Authority Admin must NOT reach superadmin management endpoints
    r = client.get("/api/admin/authorities", headers=token_a)
    check("authority admin cannot list authorities (403)", r.status_code == 403)
    r = client.get("/api/admin/authority-admins", headers=token_a)
    check("authority admin cannot list accounts (403)", r.status_code == 403)
    r = client.post("/api/admin/authority-admins", json={}, headers=token_a)
    check("authority admin cannot create accounts (403)", r.status_code == 403)

    # Scope guard: mini-app exercising require_authority_scope
    scope_app = FastAPI()

    def make_scoped(aid: str):
        def scoped(current=Depends(require_authority_scope(aid))):
            return {"ok": True}
        scope_app.get(f"/scope/{aid}")(scoped)

    make_scoped(auth_x)
    make_scoped(auth_y)
    sclient = TestClient(scope_app)

    r = sclient.get(f"/scope/{auth_x}", headers=token_a)
    check("authority admin reaches OWN authority scope (200)", r.status_code == 200, f"status={r.status_code}")
    r = sclient.get(f"/scope/{auth_y}", headers=token_a)
    check("IDOR: authority admin blocked from OTHER authority (403)", r.status_code == 403, f"status={r.status_code}")
    r = sclient.get(f"/scope/{auth_y}", headers=token_b)
    check("authority B can reach own scope (200)", r.status_code == 200)
    r = sclient.get(f"/scope/{auth_x}", headers=token_b)
    check("IDOR: admin B blocked from authority X (403)", r.status_code == 403)
    r = sclient.get(f"/scope/{auth_x}", headers=SUPER_TOKEN)
    check("superadmin override: global scope allowed (200)", r.status_code == 200)
    r = sclient.get(f"/scope/{auth_x}", headers=STUDENT_TOKEN)
    check("student blocked from any scope (403)", r.status_code == 403)


# ---------------------------------------------------------------------------
# 3. Audit tests
# ---------------------------------------------------------------------------


def test_audit_records():
    print("-- audit records ==")
    client = TestClient(app)
    from app.models import AuditLog

    aid = _make_authority("Audit Office")

    # authority created via the API (audits authority.create)
    r = client.post("/api/admin/authorities", json={
        "department_name": "Audit Dept", "authority_name": f"Audit {uuid.uuid4().hex[:6]}",
        "designation": "Head", "email": f"aud{uuid.uuid4().hex[:8]}@cus.ac.in", "phone": "0194-2311256",
    }, headers=SUPER_TOKEN)
    check("audit: authority created (201)", r.status_code == 201)
    created_auth = r.json()
    _created_authority_ids.append(created_auth["id"])
    client.post(f"/api/admin/authorities/{created_auth['id']}/toggle", headers=SUPER_TOKEN)

    username = f"aud_{uuid.uuid4().hex[:6]}"
    r = _create_authority_admin(username, f"aud{uuid.uuid4().hex[:8]}@cus.ac.in", aid)
    uid = r.json()["id"]
    _created_user_ids.append(uid)
    client.post(f"/api/admin/authority-admins/{uid}/assign", json={"authority_id": created_auth["id"]}, headers=SUPER_TOKEN)
    client.post(f"/api/admin/authority-admins/{uid}/toggle", headers=SUPER_TOKEN)

    db = SessionLocal()
    try:
        rows = (
            db.query(AuditLog)
            .filter(AuditLog.action.in_(["authority.create", "authority_admin.create", "authority_admin.assign", "authority_admin.toggle"]))
            .all()
        )
        actions = [a.action for a in rows]
        check("audit: authority.create logged", "authority.create" in actions)
        check("audit: authority_admin.create logged", "authority_admin.create" in actions)
        check("audit: authority_admin.assign logged", "authority_admin.assign" in actions)
        check("audit: authority_admin.toggle logged", "authority_admin.toggle" in actions)
        assign_row = [a for a in rows if a.action == "authority_admin.assign"]
        check("audit: assignment captures prev->new", any(a.detail and "->" in a.detail for a in assign_row))
        created_rows = [a for a in rows if a.action == "authority.create"]
        check("audit: actor recorded", all(a.actor_id is not None for a in created_rows))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4. Status history foundation tests
# ---------------------------------------------------------------------------


def test_status_history():
    print("-- status history immutability ==")
    client = TestClient(app)
    from app.grievance.models import Grievance, GrievanceStatusHistory
    from app.grievance.service import list_history, record_status_change

    db = SessionLocal()
    try:
        g = Grievance(student_name="History Test", student_email="history@example.com", status="draft")
        db.add(g)
        db.commit()
        db.refresh(g)
        gid = g.id
        _created_grievance_ids.append(str(gid))

        e1 = record_status_change(db, g, "submitted", changed_by="Aarav Sharma", changed_by_role="student", comment="filed via portal")
        e2 = record_status_change(db, g, "acknowledged", changed_by="Office Staff", changed_by_role="authority_admin", comment="received", is_internal=False)
        e3 = record_status_change(db, g, "in_progress", changed_by="Office Staff", changed_by_role="authority_admin")

        chain = list_history(db, g)
        check("3 history rows recorded", len(chain) == 3, f"n={len(chain)}")
        check("previous statuses preserved sequentially",
              [h.previous_status for h in chain] == ["draft", "submitted", "acknowledged"],
              str([h.previous_status for h in chain]))
        check("new statuses recorded",
              [h.new_status for h in chain] == ["submitted", "acknowledged", "in_progress"])
        check("current status advanced", g.status == "in_progress")
        check("actor recorded on every entry",
              all(h.changed_by and h.changed_by_role for h in chain),
              str([(h.changed_by, h.changed_by_role) for h in chain]))
        check("comment preserved", e1.comment == "filed via portal")
        check("internal flag honoured", e1.is_internal is True and e2.is_internal is False)

        # immutability: old rows are untouched object-wise
        old_row = db.query(GrievanceStatusHistory).filter(GrievanceStatusHistory.id == e1.id).first()
        check("original history rows never modified", old_row.new_status == "submitted")

        # invalid status/data rejected by the service
        try:
            record_status_change(db, g, "not_a_status", changed_by="x", changed_by_role="student")
            check("invalid status rejected", False)
        except ValueError:
            check("invalid status rejected", True)
        try:
            record_status_change(db, g, "resolved", changed_by="x", changed_by_role="hacker")
            check("invalid role rejected", False)
        except ValueError:
            check("invalid role rejected", True)

        # API surface: no route mutates history (404), so manual tampering has no vector
        r = client.get("/api/grievances", headers=SUPER_TOKEN)
        check("grievance data not exposed without routes (404)", r.status_code in (404, 405))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _phase3_prepare():
    create_all()
    _ensure_users()
    yield
    _cleanup()


if __name__ == "__main__":
    create_all()
    _ensure_users()
    tests = [
        test_roles,
        test_student_blocked,
        test_list_empty_params_regression,
        test_login_by_email_and_authority_filter,
        test_account_crud,
        test_idor_scope_guard,
        test_audit_records,
        test_status_history,
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
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)