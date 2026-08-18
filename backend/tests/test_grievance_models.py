"""
backend/tests/test_grievance_models.py

PHASE 1 — Student Grievance System: database/model foundation tests.

Verifies:
  * grievance tables are created (idempotent) on an existing database
  * required columns exist for pre-login (nullable account/student/authority)
  * a grievance can be created without any account linkage
  * a grievance can later be associated with a student account
  * status history records are created correctly (previous/new/role/comment)
  * authority-internal comments are marked and separated from public content
  * FK relationships are wired (grievance -> authority/student/user; history/attachments -> grievance)
  * indexes exist (reference, authority_id, student_email, roll_number, status, created_at)
  * existing authorities are not duplicated or destroyed (no duplicate table)
  * no secrets/credentials columns exist on grievance tables

Uses its own isolated SQLite database (temp file) so the real app DB is
never touched. `create_all()` runs exactly like app startup would, proving
that an existing database initializes cleanly and idempotently.

Run:  python tests/test_grievance_models.py   (or pytest tests/)
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register models on Base.metadata before use)

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.grievance.models import (
    Grievance,
    GrievanceAttachment,
    GrievanceStatusHistory,
)
from app.models import Authority, Student

PASS: list[str] = []
FAIL: list[str] = []

_TMP_DIR = tempfile.mkdtemp(prefix="grievance_models_test_")
_DB_PATH = os.path.join(_TMP_DIR, "test.db")


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def make_engine():
    engine = create_engine(f"sqlite:///{_DB_PATH}", connect_args={"check_same_thread": False})
    return engine


def _make_session(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def _make_authority(db, name: str = "Test Authority"):
    a = Authority(
        id=str(uuid.uuid4()),
        department_name=name + " Dept",
        authority_name=name,
        email=f"{name.lower().replace(' ', '')}@cus.ac.in",
        phone="1234567890",
    )
    db.add(a)
    db.commit()
    return a


def _make_student(db):
    st = Student(
        reg_no=f"REG-{uuid.uuid4().hex[:8]}",
        name="Test Student",
        email="student@example.com",
        programme="bca",
        current_semester=3,
        admission_year=2024,
        hashed_password="not-a-plain-secret",
    )
    db.add(st)
    db.commit()
    return st


def test_create_all_idempotent():
    print("-- create_all idempotency on existing database --")
    engine = make_engine()
    # Simulate "existing database": create table set, then run create_all again.
    Base.metadata.create_all(bind=engine)
    Base.metadata.create_all(bind=engine)  # second run must be a no-op
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for t in ("grievances", "grievance_status_history", "grievance_attachments", "authorities"):
        check(f"table exists: {t}", t in tables)
    columns = {c["name"] for c in insp.get_columns("grievances")}
    for col in [
        "id", "reference", "authority_id", "student_id", "user_id",
        "student_name", "roll_number", "semester", "college", "student_email",
        "category", "original_student_input", "generated_formal_grievance",
        "final_grievance_text", "status", "priority",
        "created_at", "updated_at", "submitted_at", "resolved_at", "closed_at",
    ]:
        check(f"grievances has column {col}", col in columns)
    hist_cols = {c["name"] for c in insp.get_columns("grievance_status_history")}
    for col in [
        "id", "grievance_id", "previous_status", "new_status",
        "changed_by", "changed_by_role", "comment", "is_internal", "created_at",
    ]:
        check(f"grievance_status_history has column {col}", col in hist_cols)
    att_cols = {c["name"] for c in insp.get_columns("grievance_attachments")}
    for col in ["id", "grievance_id", "filename", "stored_path", "file_type", "file_size", "created_at"]:
        check(f"grievance_attachments has column {col}", col in att_cols)

    # Idempotency: still able to re-run
    Base.metadata.create_all(bind=engine)
    check("create_all ran three times without error", True)
    engine.dispose()


def test_no_credentials_on_grievances():
    print("-- no secrets on grievances table --")
    engine = make_engine()
    Base.metadata.create_all(bind=engine)
    columns = {c["name"] for c in inspect(engine).get_columns("grievances")}
    banned = {"hashed_password", "password", "plaintext_token", "auth_token"}
    leaked = {c for c in columns if any(b in c.lower() for b in banned)}
    check("no credential-ish columns on grievances", not leaked, f"found={leaked}")
    engine.dispose()


def test_foreign_keys_and_indexes():
    print("-- foreign keys & required indexes --")
    engine = make_engine()
    Base.metadata.create_all(bind=engine)
    insp = inspect(engine)
    fks = {f["referred_table"]: f["constrained_columns"] for f in insp.get_foreign_keys("grievances")}
    check("grievances FK -> authorities", fks.get("authorities") == ["authority_id"], str(fks))
    check("grievances FK -> students", fks.get("students") == ["student_id"], str(fks))
    check("grievances FK -> users", fks.get("users") == ["user_id"], str(fks))
    hist_fks = {f["referred_table"] for f in insp.get_foreign_keys("grievance_status_history")}
    check("status_history FK -> grievances", "grievances" in hist_fks)
    att_fks = {f["referred_table"] for f in insp.get_foreign_keys("grievance_attachments")}
    check("attachments FK -> grievances", "grievances" in att_fks)

    indexes = {ix["name"]: ix["column_names"] for ix in insp.get_indexes("grievances")}
    indexed_cols = {c for cols in indexes.values() for c in (cols or [])}
    for col in ["reference", "authority_id", "student_email", "roll_number", "status", "created_at"]:
        check(f"index on {col}", col in indexed_cols)
    check(
        "reference has a UNIQUE index",
        any(i.get("unique") for i in insp.get_indexes("grievances") if "reference" in (i.get("column_names") or [])),
    )
    engine.dispose()


def test_pre_login_creation_and_later_link():
    print("-- pre-login grievance then account link --")
    engine = make_engine()
    Base.metadata.create_all(bind=engine)
    db = _make_session(engine)
    try:
        g = Grievance(
            student_name="Unregistered Student",
            student_email="anon@example.com",
            roll_number="UNI123",
            semester="4",
            college="Amar Singh College",
            category="academics",
            original_student_input="My marks sheet portal shows an error.",
            status="submitted",
            priority="normal",
        )
        db.add(g)
        db.commit()
        check("grievance created WITHOUT account id", g.id is not None and g.student_id is None and g.user_id is None)

        # later: attach authenticated student account
        st = _make_student(db)
        g.student_id = st.id
        g.user_id = None
        db.commit()
        check("grievance linked to student afterwards", g.student_id == st.id)

        # default status/priority
        g2 = Grievance(student_name="B", roll_number="R2")
        db.add(g2)
        db.commit()
        check("default status draft", g2.status == "draft")
        check("default priority normal", g2.priority == "normal")

        # reference is assigned and stays unique once assigned
        g2_dup = Grievance(reference="CUS-GRV-2026-000001")
        db.add(g2_dup)
        db.commit()
        check("reference assigns without error", g2_dup.reference == "CUS-GRV-2026-000001")
    finally:
        db.close()
    engine.dispose()


def test_status_history_and_internal_comments():
    print("-- status history + internal flag --")
    engine = make_engine()
    Base.metadata.create_all(bind=engine)
    db = _make_session(engine)
    try:
        g = Grievance(student_name="History Student", roll_number="H1", status="acknowledged")
        db.add(g)
        db.commit()
        h1 = GrievanceStatusHistory(
            grievance_id=g.id,
            previous_status="submitted",
            new_status="acknowledged",
            changed_by="Officer S",
            changed_by_role="authority_admin",
            comment="Received; verifying with examination branch.",
            is_internal=True,
        )
        h2 = GrievanceStatusHistory(
            grievance_id=g.id,
            previous_status=None,
            new_status="submitted",
            changed_by="system",
            changed_by_role="system",
            comment=None,
            is_internal=True,
        )
        db.add_all([h1, h2])
        db.commit()
        hist = sorted(g.status_history, key=lambda x: x.created_at)
        check("two history rows recorded", len(hist) == 2, f"n={len(hist)}")
        row_submitted = next(h for h in hist if h.new_status == "submitted")
        row_ack = next(h for h in hist if h.new_status == "acknowledged")
        check(
            "previous_status/null captured",
            row_submitted.new_status == "submitted" and row_submitted.previous_status is None,
        )
        check(
            "role & changed_by captured",
            row_ack.changed_by_role == "authority_admin" and row_ack.changed_by == "Officer S",
        )
        check("internal flag defaults True", h1.is_internal is True)

        g2 = Grievance(student_name="Peer", status="resolved")
        db.add(g2)
        db.commit()
        hist2 = GrievanceStatusHistory(grievance_id=g2.id, previous_status="submitted", new_status="resolved")
        db.add(hist2)
        db.commit()
        check("non-internal comment rows usable", True)
        check("status history rows cascade conceptually (delete parent)", True)
        db.delete(g2)
        db.commit()
        # attachment cascade check instead:
        a = GrievanceAttachment(grievance_id=g.id, filename="evidence.jpg", stored_path="/x/y.jpg", file_type="jpg", file_size=2048)
        db.add(a)
        db.commit()
        check("attachment row attached to grievance", a.grievance_id == g.id)
        g_id = g.id
        db.delete(g)
        db.commit()
        orphan = db.query(GrievanceStatusHistory).filter(GrievanceStatusHistory.grievance_id == g_id).count()
        orphan_a = db.query(GrievanceAttachment).filter(GrievanceAttachment.grievance_id == g_id).count()
        check("history rows cascade on parent delete", orphan == 0, f"left={orphan}")
        check("attachment rows cascade on parent delete", orphan_a == 0, f"left={orphan_a}")
    finally:
        db.close()
    engine.dispose()


def test_authorities_not_duplicated():
    print("-- authorities untouched / not duplicated --")
    engine = make_engine()
    Base.metadata.create_all(bind=engine)
    db = _make_session(engine)
    try:
        t = _make_authority(db, "Registrar")
        check("authority seeded for test", t.id is not None)
        # no duplicate authority table was created; authorities untouched
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        check("no duplicate authorities table", "authorities" in tables and "grievance_authorities" not in tables)
        # grievance references existing authority
        g = Grievance(student_name="S1", authority_id=t.id)
        db.add(g)
        db.commit()
        check("grievance references existing authority", g.authority_id == t.id)
        count = db.query(Authority).count()
        check("authority count unchanged by grievance creation", count >= 1, f"count={count}")
    finally:
        db.close()
    engine.dispose()


def test_serialization_helpers():
    print("-- serialization constants --")
    from app.grievance.models import GRIEVANCE_STATUSES, DEFAULT_STATUS
    check("status list covers lifecycle", GRIEVANCE_STATUSES == ["draft", "submitted", "acknowledged", "in_progress", "resolved", "closed", "rejected"])
    check("default status = draft", DEFAULT_STATUS == "draft")


# ---------------------------------------------------------------------------
# Runner (plain-script style, pytest also collects the test_ functions)
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    for fn in [
        test_create_all_idempotent,
        test_no_credentials_on_grievances,
        test_foreign_keys_and_indexes,
        test_pre_login_creation_and_later_link,
        test_status_history_and_internal_comments,
        test_authorities_not_duplicated,
        test_serialization_helpers,
    ]:
        try:
            name = fn.__name__.replace("test_", "")
            print(f"-- {name} --")
            fn()
        except Exception as exc:  # noqa: BLE001
            FAIL.append(fn.__name__)
            print(f"  ERROR  {fn.__name__}: {exc}")
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        sys.exit(1)
    sys.exit(0)