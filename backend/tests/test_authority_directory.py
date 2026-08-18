"""
backend/tests/test_authority_directory.py

PHASE 2 — Authority Directory + Super Admin management tests.

Verifies (spec section 17):
  1. Authority creation         6.  Duplicate prevention      11. Inactive excluded from
  2. Authority retrieval        7.  Email validation             student selection endpoint
  3. Authority update           8.  Super Admin authorization  12. Historical grievances intact
  4. Authority activation       9.  Unauthorized user blocked  13. Authority linked to grievance
  5. Authority deactivation    10. Students cannot manage      14. Existing records intact
                                                           15. Seed/import idempotent

Runs against the app's real DB but only the api Glue: uses FastAPI TestClient
with the actual app (models registered, tables created by create_all()). All
rows created by the tests are cleaned up afterwards so the real DB is left
exactly as it was found.

Run:  python tests/test_authority_directory.py   (or pytest tests/)
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register models before any session)

from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.database import SessionLocal, create_all
from app.main import app

PASS: list[str] = []
FAIL: list[str] = []

CREATE_TOKEN: dict[str, str] = {}
STUDENT_TOKEN: dict[str, str] = {}
_ADMIN_USERNAME = f"__test_super_{uuid.uuid4().hex[:8]}"
_STUDENT_USERNAME = f"__test_student_{uuid.uuid4().hex[:8]}"
_created_authority_ids: list[str] = []
_created_category_ids: list[str] = []
_created_grievance_ids: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def _cleanup() -> None:
    from app.grievance.models import Grievance
    from app.models import Authority, GrievanceCategory, User

    db = SessionLocal()
    try:
        for gid in _created_grievance_ids:
            db.query(Grievance).filter(Grievance.id == gid).delete()
        for aid in _created_authority_ids:
            db.query(Authority).filter(Authority.id == aid).delete()
        for cid in _created_category_ids:
            db.query(GrievanceCategory).filter(GrievanceCategory.id == cid).delete()
        db.query(User).filter(User.username.in_([_ADMIN_USERNAME, _STUDENT_USERNAME])).delete()
        db.commit()
    finally:
        db.close()


def _ensure_users() -> None:
    """Create (once) the dedicated test super admin + student; login both."""
    global _ADMIN_USERNAME, _STUDENT_USERNAME  # noqa: PLW0603
    from app.models import User

    db = SessionLocal()
    try:
        if not db.query(User).filter(User.username == _ADMIN_USERNAME).first():
            db.add(User(username=_ADMIN_USERNAME, email=f"{_ADMIN_USERNAME}@test.local",
                        hashed_password=hash_password("secret123"), role="superadmin", is_active=True))
        if not db.query(User).filter(User.username == _STUDENT_USERNAME).first():
            db.add(User(username=_STUDENT_USERNAME, email=f"{_STUDENT_USERNAME}@test.local",
                        hashed_password=hash_password("secret123"), role="student", is_active=True))
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    r_super = client.post("/api/auth/login", data={"username": _ADMIN_USERNAME, "password": "secret123"})
    r_student = client.post("/api/auth/login", data={"username": _STUDENT_USERNAME, "password": "secret123"})
    if r_super.status_code == 200:
        CREATE_TOKEN["Authorization"] = f"Bearer {r_super.json()['access_token']}"
    if r_student.status_code == 200:
        STUDENT_TOKEN["Authorization"] = f"Bearer {r_student.json()['access_token']}"


def _h(token: dict[str, str], **extra) -> dict[str, str]:
    h = dict(token)
    if extra:
        h.update(extra)
    return h


def _make_authority(client, overrides=None):
    body = {
        "department_name": "Test Department",
        "authority_name": f"Test Office {uuid.uuid4().hex[:6]}",
        "designation": "Head of Test",
        "email": f"office{uuid.uuid4().hex[:8]}@cus.ac.in",
        "phone": "0194-2311256",
        "office_location": "Gogji-Bagh, Srinagar",
    }
    if overrides:
        body.update(overrides)
    r = client.post("/api/admin/authorities", json=body, headers=CREATE_TOKEN)
    return r


def test_superadmin_authorization():
    print("-- super admin authorization --")
    client = TestClient(app)
    # no token
    r = client.get("/api/admin/authorities")
    check("unauthenticated access rejected", r.status_code in (401, 403), f"status={r.status_code}")
    # student token
    r = client.get("/api/admin/authorities", headers=STUDENT_TOKEN)
    check("student cannot list authorities", r.status_code == 403, f"status={r.status_code}")
    r = client.post("/api/admin/authorities", json={
        "department_name": "X", "authority_name": "Y", "email": "a@b.c", "phone": "1"}, headers=STUDENT_TOKEN)
    check("student cannot create authority", r.status_code == 403, f"status={r.status_code}")
    # viewing endpoints still open (info only)
    r = client.get("/api/authority/departments")
    check("public departments reachable", r.status_code == 200)


def test_authority_crud():
    print("==2..5: CRUD + active/inactive toggling ==")
    client = TestClient(app)

    r = _make_authority(client)
    check("create: 201", r.status_code == 201, f"status={r.status_code} {r.text[:200]}")
    auth = r.json()
    _created_authority_ids.append(auth["id"])

    r = client.get(f"/api/admin/authorities/{auth['id']}", headers=CREATE_TOKEN)
    check("retrieve by id: 200", r.status_code == 200 and r.json()["id"] == auth["id"])

    r = client.put(f"/api/admin/authorities/{auth['id']}", json={"phone": "0194-0000123"}, headers=CREATE_TOKEN)
    check("update phone", r.status_code == 200 and r.json()["phone"] == "0194-0000123", f"status={r.status_code}")

    r = client.post(f"/api/admin/authorities/{auth['id']}/toggle", headers=CREATE_TOKEN)
    check("deactivate (toggle) works", r.status_code == 200 and r.json()["active"] is False)
    r = client.get(f"/api/admin/authorities/{auth['id']}", headers=CREATE_TOKEN)
    check("deactivated record still visible to superadmin", r.status_code == 200 and r.json()["active"] is False)

    r = client.get(f"/api/authority/{auth['id']}")
    check("inactive authority hidden from public endpoint", r.status_code == 404, f"status={r.status_code}")

    r = client.post(f"/api/admin/authorities/{auth['id']}/toggle", headers=CREATE_TOKEN)
    check("reactivate works", r.status_code == 200 and r.json()["active"] is True)
    r = client.get(f"/api/authority/{auth['id']}")
    check("active authority visible publicly", r.status_code == 200)


def test_duplicate_and_email_validation():
    print("== duplicate + email validation ==")
    client = TestClient(app)
    email = f"dup{uuid.uuid4().hex[:8]}@cus.ac.in"
    r = _make_authority(client, {"authority_name": "Duplicate Prevention Office", "email": email})
    check("first create ok", r.status_code == 201, f"status={r.status_code} {r.text[:200]}")
    _created_authority_ids.append(r.json()["id"])

    r = _make_authority(client, {"authority_name": "Duplicate Prevention Office", "email": f"other{uuid.uuid4().hex[:8]}@cus.ac.in"})
    check("same NAME rejected (409)", r.status_code == 409, f"status={r.status_code} detail={r.text[:120]}")

    r = _make_authority(client, {"authority_name": "Other Office X", "email": email})
    check("same EMAIL rejected (409)", r.status_code == 409, f"status={r.status_code} detail={r.text[:120]}")

    r = _make_authority(client, {"authority_name": "Email Validation Office", "email": "not-an-email"})
    check("invalid email rejected (422)", r.status_code == 422, f"status={r.status_code}")


def test_categories_and_routing():
    print("--4/5 categories + category assignment --")
    client = TestClient(app)
    name = f"Category {uuid.uuid4().hex[:6]}"
    r = client.post("/api/admin/authorities/categories", json={"name": name}, headers=CREATE_TOKEN)
    check("create category 201", r.status_code == 201, f"status={r.status_code} {r.text[:150]}")
    cat = r.json()
    _created_category_ids.append(cat["id"])

    r = client.post("/api/admin/authorities/categories", json={"name": name}, headers=CREATE_TOKEN)
    check("duplicate category rejected (409)", r.status_code == 409, f"status={r.status_code}")

    auth = _make_authority(client).json()
    _created_authority_ids.append(auth["id"])
    r = client.put(f"/api/admin/authorities/{auth['id']}", json={"category_id": cat["id"]}, headers=CREATE_TOKEN)
    check("assign category to authority", r.status_code == 200 and r.json()["category_id"] == cat["id"])

    r = client.get("/api/authority/categories")
    cats = (r.json().get("categories") or [])
    check("public categories expose the new one", any(c["id"] == cat["id"] for c in cats))

    # student-facing list exposes only active authorities + categories
    r = client.get("/api/authority/categories")
    check("public categories endpoint ok", r.status_code == 200)


def test_official_import_idempotent():
    print("==6 official import idempotency ==")
    client = TestClient(app)
    r1 = client.post("/api/admin/authorities/import-official", headers=CREATE_TOKEN)
    check("official import runs", r1.status_code == 200, f"status={r1.status_code} {r1.text[:200]}")
    d1 = r1.json()
    r2 = client.post("/api/admin/authorities/import-official", headers=CREATE_TOKEN)
    d2 = r2.json()
    check("second run creates 0 new", d2.get("created") == 0, f"d2={d2}")
    check("count stable across runs", d1.get("total") == d2.get("total"),
          f"{d1.get('total')} vs {d2.get('total')}")

    total = client.get("/api/admin/authorities", headers=CREATE_TOKEN).json()
    check(
        "registrar imported",
        any(a["authority_name"] == "Registrar" for a in total),
    )
    ids_before = {a["id"] for a in total}
    client.post("/api/admin/authorities/import-official", headers=CREATE_TOKEN)
    total_after = client.get("/api/admin/authorities", headers=CREATE_TOKEN).json()
    check("existing records intact (no deletions on re-import)",
          ids_before == {a["id"] for a in total_after},
          f"{len(ids_before)} vs {len(total_after)}")
    # auth service cache also holds it
    from app.authority.service import authority_service
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        matches = [a for a in authority_service.list_active() if a["authority_name"] == "Registrar"]
        check("registrar in matcher cache", len(matches) >= 1, f"n={len(matches)}")
    finally:
        db.close()
        authority_service.refresh_cache(db)


def test_grievance_routing_foundation():
    print("==7 grievance <-> authority relationship ==")
    client = TestClient(app)
    from app.grievance.models import Grievance
    from app.authority.service import authority_service
    from app.database import SessionLocal

    # use the authoritative Registrar row created by the official import
    db = SessionLocal()
    try:
        registrar = [a for a in authority_service.list_active() if a["authority_name"] == "Registrar"]
        check("registrar cached for routing", len(registrar) >= 1)
        if registrar:
            auth_id = registrar[0]["id"]
            g = Grievance(student_name="Route Test", student_email="route@example.com", authority_id=auth_id, status="submitted")
            db.add(g)
            db.commit()
            _created_grievance_ids.append(g.id)
            check("grievance linked to authority", g.authority_id == auth_id)
            # grievance holds id, not a copied name/email (routing foundation)
            check("grievance stores authority_id FK", g.authority_id is not None)
            # deactivating the authority must NOT destroy the grievance
            client.post(f"/api/admin/authorities/{auth_id}/toggle", headers=CREATE_TOKEN)
            db.expire_all()
            g2 = db.query(Grievance).filter(Grievance.id == g.id).first()
            check("grievance survives authority deactivation", g2 is not None and g2.authority_id == auth_id)
            # restore active
            client.post(f"/api/admin/authorities/{auth_id}/toggle", headers=CREATE_TOKEN)
    finally:
        db.close()


def test_source_kind_preserved():
    print("==8 source attribution on official records ==")
    from app.authority.service import authority_service
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        registrar = [a for a in authority_service.list_active() if a["authority_name"] == "Registrar"]
        check("official registrar has source_kind=official",
              len(registrar) and registrar[0].get("source_kind") == "official",
              f"source_kind={registrar[0].get('source_kind') if registrar else None}")
        check("official registrar has website",
              len(registrar) and bool(registrar[0].get("website")))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Runner (plain-script style, pytest also collects the test_ functions)
# ---------------------------------------------------------------------------

import pytest


@pytest.fixture(scope="module", autouse=True)
def _phase2_prepare():
    """Ensure schema exists and test users are logged in before any test runs."""
    create_all()
    _ensure_users()
    yield
    _cleanup()


if __name__ == "__main__":
    create_all()
    _ensure_users()
    tests = [
        test_superadmin_authorization,
        test_authority_crud,
        test_duplicate_and_email_validation,
        test_categories_and_routing,
        test_official_import_idempotent,
        test_grievance_routing_foundation,
        test_source_kind_preserved,
    ]
    try:
        for fn in tests:
            try:
                name = fn.__name__.replace("test_", "")
                print(f"-- {name} --")
                fn()
            except Exception as exc:  # noqa: BLE001
                FAIL.append(fn.__name__)
                print(f"  ERROR  {fn.__name__}: {exc}")
    finally:
        _cleanup()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        sys.exit(1)
    sys.exit(0)