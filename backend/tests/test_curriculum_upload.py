"""
backend/tests/test_curriculum_upload.py

Tests for the curriculum-document upload lifecycle (extension of the
academic catalogue, independent of Knowledge Sync):

  * format readers      (catalogue.parser.readers)      — pdf/docx/csv/xlsx/xls
  * extraction          (catalogue.parser.extract)      — payload shape
  * detection           (catalogue.parser.detect)       — scheme/programme/level
  * upload service      (catalogue.service)             — save/dedup/publish lifecycle
  * publish gate        (only one Active per programme)

Run:  python tests/test_curriculum_upload.py
"""

from __future__ import annotations

import asyncio
import csv
import io
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register catalogue tables before any session)

from app.database import SessionLocal
from app.catalogue import service
from app.catalogue.models import CurriculumUpload

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
    from app.database import create_all
    create_all()
    from app.catalogue.seed import seed_catalogue
    db = SessionLocal()
    try:
        return seed_catalogue(db)
    finally:
        db.close()


def _csv_bytes(title: str, rows: list[list]) -> bytes:
    buf = io.StringIO()
    if title:
        buf.write(title + "\n")
    writer = csv.writer(buf)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


_HEADER = ["Course Code", "Course Name", "Category", "Credits", "Semester"]
_BCA_ROWS = [
    ["C101", "Programming in C", "Major", 4, 1],
    ["SEC101", "Soft Skills", "SEC", 2, 1],
    ["C201", "Data Structures", "Major", 4, 2],
]

SAMPLE_CSV = _csv_bytes("Bachelor of Computer Applications (BCA) - NEP 2020", [_HEADER] + _BCA_ROWS)


# ---------------------------------------------------------------------------
# Parser (no DB needed)
# ---------------------------------------------------------------------------


def test_readers_and_extract(tmpdir: str):
    print("-- parser.readers / extract --")
    from app.catalogue.parser.readers import read_curriculum_document, CUR_EXTENSIONS

    check("supported extensions", set(CUR_EXTENSIONS) == {"pdf", "docx", "doc", "xlsx", "xls", "csv"})

    path = Path(tmpdir) / "bca_curriculum.csv"
    path.write_bytes(SAMPLE_CSV)
    dt = read_curriculum_document(str(path))
    check("csv reader yields a table", len(dt.tables) >= 1 and len(dt.tables[0]) >= 4)

    from app.catalogue.parser.extract import extract_curriculum
    payload = extract_curriculum(
        dt.pages,
        tables=dt.tables,
        hints={"programme_name": "Bachelor of Computer Applications", "programme_code": "BCA", "level": "ug"},
    )
    check("payload has programme", bool(payload.get("programme")))
    check("payload has semesters", isinstance(payload.get("semesters"), list))
    total = sum(len(s.get("subjects", [])) for s in payload["semesters"])
    check("subjects extracted >= seeded", total >= 3, f"total={total}")
    codes = {s["code"] for sem in payload["semesters"] for s in sem["subjects"]}
    check("course codes parsed", "C101" in codes and "C201" in codes, f"codes={sorted(codes)}")
    check("payload has summary", bool(payload.get("summary")))


def test_detection():
    print("-- parser.detect --")
    from app.catalogue.parser.detect import detect_level
    level, _ = detect_level("BCA", "undergraduate programme bachelor of computer applications")
    check("level keyword detected", level == "ug", f"level={level}")
    level2, _ = detect_level("bca", None)
    check("level from degree code", level2 == "ug", f"level={level2}")
    # No DB here (service.list_academic_schemes uses a short-lived session that
    # may be empty before seeding) — detect_programme resolves nothing without a
    # programme, which is correct for unknown sources.


# ---------------------------------------------------------------------------
# Upload service lifecycle (needs DB)
# ---------------------------------------------------------------------------


def setup_bca(db):
    prog = service.resolve_programme("BCA", db=db)
    return prog


def test_upload_lifecycle():
    print("-- service.save_curriculum_upload lifecycle --")
    db = SessionLocal()
    try:
        prog = setup_bca(db)
        check("BCA seeded", bool(prog), f"got={prog and list(prog.keys())[:4]}")

        upload = service.save_curriculum_upload(
            db, SAMPLE_CSV, "BCA_Curriculum_2024.csv",
            uploaded_by=str(uuid.uuid4()),
            metadata={"programme_id": prog["id"], "programme_name": prog["name"], "level": "ug"},
        )
        check("upload saved as draft", upload.get("status") == "draft", f"status={upload.get('status')}")
        check("upload carries filename", upload.get("filename") == "BCA_Curriculum_2024.csv")
        check("parse succeeded", upload.get("parse_status") in ("ok", "partial"),
              f"parse_status={upload.get('parse_status')} warnings={upload.get('warnings')}")
        check("programme detected", upload.get("programme_code") == "BCA", f"code={upload.get('programme_code')}")

        # duplicate detection by hash (same bytes)
        dup = service.check_upload_duplicate(db, upload["sha256"], programme_code="BCA")
        check("duplicate detected by hash", bool(dup and dup.get("id") == upload["id"]))
        dup2 = service.check_upload_duplicate(db, upload["sha256"], programme_code="BBA")
        check("hash not dup for other programme", dup2 is None)

        # get single
        got = service.get_curriculum_upload(db, upload["id"])
        check("get single upload", bool(got and got["id"] == upload["id"]))

        # put back through update (edit payload)
        updated = service.update_curriculum_upload(db, upload["id"], {"revision": "2025B"})
        check("update revision field", updated.get("revision") == "2025B", f"rev={updated.get('revision')}")

        # publish -> active
        pub = service.publish_curriculum_upload(db, upload["id"])
        check("publish -> active", pub.get("status") == "active", f"status={pub.get('status')}")
        check("publish sets published_at", bool(pub.get("published_at")))

        # second version for the SAME programme => first gets archived
        upload2 = service.save_curriculum_upload(
            db, SAMPLE_CSV, "Sample_Revised.csv",
            metadata={"programme_id": prog["id"]},
        )
        pub2 = service.publish_curriculum_upload(db, upload2["id"])
        check("second version active", pub2.get("status") == "active")
        # reload first
        reloaded = service.get_curriculum_upload(db, upload["id"])
        check("previous version archived", reloaded.get("status") == "archived", f"status={reloaded.get('status')}")

        # list with status filter
        actives = service.get_curriculum_uploads(db, status="active")
        check("one active per programme", all(u.get("status") == "active" for u in actives) and len(actives) == 1)

        # archive explicitly
        arch = service.archive_curriculum_upload(db, upload2["id"])
        check("explicit archive", arch.get("status") == "archived")

        # delete draft
        draft = service.save_curriculum_upload(db, SAMPLE_CSV, "discarded.csv")
        deleted = service.delete_curriculum_upload(db, draft["id"])
        check("delete draft ok", deleted is True)
        check("draft gone", service.get_curriculum_upload(db, draft["id"]) is None)

        # cannot delete active
        active_pub = service.publish_curriculum_upload(db, upload["id"])
        # upload is archived now; publish moves it back to active (archived -> active)
        check("re-publish archived -> active", active_pub.get("status") == "active")
        try:
            service.delete_curriculum_upload(db, upload["id"])
            check("active delete refused", False, "no ValueError raised")
        except ValueError:
            check("active delete refused", True)
    finally:
        db.close()


def test_materialization():
    print("-- publish materialization (structured + RAG primary source) --")
    db = SessionLocal()
    try:
        prog = setup_bca(db)
        # Upload a BCA curriculum with a distinct subject, publish it, and check
        # the payload is materialized into the structured catalogue tables.
        upload = service.save_curriculum_upload(
            db, SAMPLE_CSV, "BCA_Curriculum_2025.csv",
            metadata={"programme_id": prog["id"], "programme_name": prog["name"], "level": "ug"},
        )
        payload = upload.get("payload") or {}
        pub = service.publish_curriculum_upload(db, upload["id"])
        check("publish -> active", pub.get("status") == "active")

        # active gate returns the published upload for the programme
        active = service.get_active_curriculum_upload(db, programme_code="BCA")
        check("active upload gate", bool(active and active["status"] == "active"))

        # structured subjects now come from the uploaded payload (primary source)
        sem_subjects = service.get_subjects(programme_id=prog["id"])
        payload_names = {
            str(s.get("name"))
            for sem in (payload.get("semesters") or [])
            for s in sem.get("subjects") or []
        }
        db_names = {str(s.get("subject_name")) for s in sem_subjects}
        check("payload subjects materialized", bool(payload_names & db_names),
              f"intersection={sorted(payload_names & db_names)[:3]}")

        # outcomes from the payload are appended to Programme learning outcomes
        if payload.get("outcomes"):
            outs = service.get_learning_outcomes(prog["id"], db=db)
            check("payload outcomes materialized", any(
                any(str(o).strip().lower() in str(t).lower() for o in payload["outcomes"])
                for t in outs
            ))

        # subject search from the active upload payload
        hits = service.curriculum_subject_search(None, "BCA", "C101")
        search_found = bool(hits and any("C101" in str(h.get("code", "")) for h in hits))
        check("curriculum_subject_search finds C101", search_found,
              f"hits={hits and [h.get('code') for h in hits]}")
        miss = service.curriculum_subject_search(None, "BCA", "xyz-not-a-subject")
        check("subject search returns None on miss", miss is None)

        # RAG document linked to the upload (created on publish)
        got = service.get_curriculum_upload(db, upload["id"])
        check("document_id linked", bool(got.get("document_id")), f"doc={got.get('document_id')}")
        check("rag_status reported", got.get("rag_status") in ("ready", "failed", "processing", None),
              f"rag_status={got.get('rag_status')}")
    finally:
        db.close()


def main():
    _ensure_seeded()
    import tempfile
    print("-- Running curriculum upload tests --")
    with tempfile.TemporaryDirectory() as tmp:
        test_readers_and_extract(tmp)
    test_detection()
    test_upload_lifecycle()
    test_materialization()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)


_ensure_seeded()  # pytest mode parity: tables + catalogue seed (idempotent)

if __name__ == "__main__":
    main()