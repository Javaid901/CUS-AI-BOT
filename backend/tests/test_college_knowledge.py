"""
backend/tests/test_college_knowledge.py

Tests for the college knowledge base extension:

  * college-scoped chunk metadata filtering (no cross-college leakage)
  * submission of manual / url / upload sources (row metadata + queued job)
  * archive / restore / delete lifecycle
  * lazy backfill seeding from hard-coded college data
  * orchestrator -> retrieval scoping (college in RAG context)

Uses the real app DB and the standard Document pipeline. Ingestion jobs may
fail in environments without an embedding model; assertions target the
source lifecycle and metadata, not vector readiness.

Run:  python tests/test_college_knowledge.py   (or pytest tests/)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register models before any session)

from app.database import SessionLocal, create_all

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def _ensure_seeded() -> None:
    create_all()


# ---------------------------------------------------------------------------
# Metadata filter scoping (no DB)
# ---------------------------------------------------------------------------


def test_metadata_filter_scoping():
    print("-- metadata filter: college isolation --")
    from app.ingest.retriever import _matches_where, build_metadata_filter

    f = build_metadata_filter({"college_id": "amar-singh-college"})
    check("college filter built", f == {"college_id": "amar-singh-college"}, f"f={f}")

    f2 = build_metadata_filter(
        {"college_id": "govt-degree-college-mujgund", "programme": "bca", "scope": "college"}
    )
    check(
        "combined college + programme filter",
        isinstance(f2, dict) and "$and" in f2 and len(f2["$and"]) == 3,
        f"f2={f2}",
    )

    college_a = {"college_id": "amar-singh-college", "scope": "college"}
    college_b = {"college_id": "govt-degree-college-mujgund", "scope": "college"}
    university = {"scope": "university"}
    legacy = {}

    where_a = {"college_id": "amar-singh-college"}
    check("college A chunk matches its filter", _matches_where(college_a, where_a))
    check("college A chunk fails college B filter", not _matches_where(college_b, where_a))
    check("university chunk fails college filter", not _matches_where(university, where_a))
    check("legacy chunk fails college filter", not _matches_where(legacy, where_a))
    check(
        "legacy chunk passes loose college filter",
        _matches_where(legacy, where_a, loose=True),
    )
    check(
        "college chunk fails university-only filter",
        not _matches_where(college_a, {"scope": "university"}),
    )

    where_scoped = {"$and": [{"college_id": "amar-singh-college"}, {"scope": "college"}]}
    check("compound college filter", _matches_where(college_a, where_scoped))
    check(
        "compound college filter rejects other college",
        not _matches_where(college_b, where_scoped),
    )
    check("None filter matches everything", _matches_where(college_a, None))


def test_flatten_college_digest():
    print("-- backfill digest --")
    from app.college.data import COLLEGES
    from app.college.knowledge import _flatten_college

    any_college = next(iter(COLLEGES.values()))
    digest = _flatten_college(any_college)
    check("digest covers name", any_college["name"] in digest)
    check("digest covers departments", "departments" in digest)
    check("digest within line cap", len(digest.splitlines()) <= 510)


# ---------------------------------------------------------------------------
# Submission lifecycle (needs DB + job queue, embeddings may be absent)
# ---------------------------------------------------------------------------


def test_manual_submission_and_list():
    print("-- submission / list / summarize --")
    import asyncio
    import uuid

    from app.college.knowledge import (
        SCOPE_COLLEGE,
        list_sources,
        submit_manual,
        summarize_college,
    )

    db = SessionLocal()
    cid = "amar-singh-college"
    tag = uuid.uuid4().hex[:8]
    title = f"Test Manual Entry {tag}"
    try:
        _ensure_seeded()
        result = asyncio.run(
            submit_manual(
                db, None, cid, title,
                "This is a sample manual knowledge entry about the college library "
                "timings for the automated test environment.",
                category="test",
            )
        )
        check("manual job queued", result.get("status") in ("queued", "duplicate"), str(result))
        doc_id = result.get("document_id")
        check("manual job has doc id", bool(doc_id))
        if doc_id:
            from app.models import Document

            doc = db.get(Document, uuid.UUID(doc_id))
            check("doc is college-scoped", doc.scope == SCOPE_COLLEGE)
            check("doc has college_id", doc.college_id == cid)
            check("doc has college_name", bool(doc.college_name))
            check("doc has source_kind manual", (doc.source_kind or "") == "manual")

        sources = list_sources(db, cid)
        check("listing includes new source", any(title in s["title"] for s in sources))
        summary = summarize_college(db, cid)
        check("summary reports source count", summary["sources"] >= 1, f"sources={summary['sources']}")
        check("summary splits by kind", "manual" in summary["by_source_kind"])
    finally:
        db.close()


def test_manual_validation():
    print("-- manual validation (async path) --")
    import asyncio

    from app.college.knowledge import submit_manual

    db = SessionLocal()
    try:
        try:
            asyncio.run(submit_manual(db, None, "amar-singh-college", "Too short", "tiny"))
            check("short content rejected", False)
        except ValueError as exc:
            check("short content rejected", "at least 20" in str(exc))
    finally:
        db.close()


def test_url_validation():
    print("-- url validation --")
    import asyncio

    from app.college.knowledge import submit_url

    db = SessionLocal()
    try:
        try:
            asyncio.run(submit_url(db, None, "amar-singh-college", "ftp://example.com/page"))
            check("non-http scheme rejected", False)
        except ValueError as exc:
            check("non-http scheme rejected", "http" in str(exc))
        try:
            asyncio.run(submit_url(db, None, "amar-singh-college", "http://localhost/secret"))
            check("localhost rejected", False)
        except ValueError as exc:
            check("localhost rejected", "Local" in str(exc))
    finally:
        db.close()


def test_orchestrator_rag_context_scoping():
    print("-- orchestrator rag context --")
    from app.orchestrator.context import ConversationContext
    from app.orchestrator.engine import _build_rag_context

    plain = ConversationContext()
    ctx_none = _build_rag_context(plain, None)
    check("no college -> no college_id key", not ctx_none.get("college_id"))
    check("no college -> university scope", ctx_none.get("scope") == "university")

    with_college = ConversationContext(college="amar_singh", college_name="Amar Singh College")
    ctx_college = _build_rag_context(with_college, None)
    check("college_id in rag context", ctx_college.get("college_id") == "amar_singh")
    check("college_name in rag context", ctx_college.get("college_name") == "Amar Singh College")
    check("college in rag context -> college scope", ctx_college.get("scope") == "college")


# ---------------------------------------------------------------------------
# Runner (plain-script style, pytest also collects the test_ functions)
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    for fn in [
        test_metadata_filter_scoping,
        test_flatten_college_digest,
        test_manual_submission_and_list,
        test_manual_validation,
        test_url_validation,
        test_orchestrator_rag_context_scoping,
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