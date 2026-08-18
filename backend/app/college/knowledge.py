"""
backend/app/college/knowledge.py

College-scoped knowledge base.

Every college can manage its own slice of the retrieval corpus. Sources are
Document rows stamped with scope="college" and the owning college_id; they
flow through the standard ingestion pipeline (queue -> worker -> Chroma), and
their chunk metadata carries college_id / college_name / scope so retrieval
can be scoped to a single college (or fall back to the university corpus).

Sources have a source_kind: upload (admin file), manual (typed text),
url (fetched from the web) or backfill (auto-generated digest of the
hardcoded college data, created lazily on first management access).
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from app.college.service import CollegeService
from app.config import settings
from app.models import Document
from app.utils.files import validate_upload
from app.utils.logging import log
from sqlalchemy.orm import Session

SCOPE_COLLEGE = "college"
SCOPE_UNIVERSITY = "university"

SOURCE_KIND_UPLOAD = "upload"
SOURCE_KIND_MANUAL = "manual"
SOURCE_KIND_URL = "url"
SOURCE_KIND_BACKFILL = "backfill"


def _get_college(college_id: str) -> dict | None:
    return CollegeService.get_college(college_id)


def _slugify(text: str, fallback: str = "document") -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return slug[:60] or fallback


def _college_meta(
    college_id: str, college_name: str, source_kind: str
) -> dict:
    return {
        "college_id": college_id,
        "college_name": college_name,
        "scope": SCOPE_COLLEGE,
        "source_kind": source_kind,
    }


async def _submit_into(
    db: Session,
    owner_id,
    college_id: str,
    filename: str,
    data: bytes,
    title: str,
    source_kind: str,
    document_type: str | None = None,
    category: str | None = None,
) -> dict:
    """Shared submit path: validate -> queue a background ingestion job."""
    from app.ingest.service import submit_upload_job

    validate_upload(filename, len(data))
    college = _get_college(college_id)
    college_name = college["name"] if college else college_id
    metadata = _college_meta(college_id, college_name, source_kind)
    if document_type:
        metadata["document_type"] = document_type
    if category:
        metadata["category"] = category
    return await submit_upload_job(
        db, owner_id, filename, data, title=title, metadata=metadata
    )


async def submit_upload(
    db: Session,
    owner_id,
    college_id: str,
    original_filename: str,
    data: bytes,
    title: str | None = None,
    document_type: str | None = None,
    category: str | None = None,
) -> dict:
    """Queue a file upload into a college's knowledge base."""
    return await _submit_into(
        db, owner_id, college_id, original_filename, data,
        title=title or original_filename,
        source_kind=SOURCE_KIND_UPLOAD,
        document_type=document_type,
        category=category,
    )


async def submit_manual(
    db: Session,
    owner_id,
    college_id: str,
    title: str,
    content: str,
    document_type: str | None = None,
    category: str | None = None,
) -> dict:
    """Queue a manually typed knowledge entry (as a .txt source)."""
    text = (content or "").strip()
    if len(text) < 20:
        raise ValueError("Manual content must be at least 20 characters")
    if len(text) > 200_000:
        raise ValueError("Manual content is too large (max 200 000 characters)")
    filename = f"{_slugify(title, 'manual_note')}.txt"
    return await _submit_into(
        db, owner_id, college_id, filename, text.encode("utf-8"),
        title=title.strip() or "Manual note",
        source_kind=SOURCE_KIND_MANUAL,
        document_type=document_type or "notice",
        category=category,
    )


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs are supported")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL must include a hostname")
    lowered = host.lower()
    if lowered in ("localhost", "::1") or lowered.endswith(".local"):
        raise ValueError("Local addresses are not allowed")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("Private or reserved addresses are not allowed")
    except ValueError:
        pass  # hostname, not an IP literal
    return url


async def submit_url(
    db: Session,
    owner_id,
    college_id: str,
    url: str,
    title: str | None = None,
    category: str | None = None,
) -> dict:
    """Fetch a public page, extract its text, and queue it as a URL source."""
    import httpx

    _validate_url(url)
    headers = {"User-Agent": "CUS-AI-College-Knowledge/1.0"}
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
    except Exception as exc:
        raise ValueError(f"Could not fetch URL: {exc}") from exc
    if resp.status_code >= 400:
        raise ValueError(f"URL returned HTTP {resp.status_code}")
    content_type = (resp.headers.get("content-type") or "").lower()
    if len(resp.content) > 5 * 1024 * 1024:
        raise ValueError("Page is too large (max 5 MB)")
    if "html" not in content_type and not content_type.startswith("text/plain"):
        raise ValueError("URL does not point to a readable web page")

    from app.knowledge_sync.web_extractor import extract_html

    if "html" in content_type:
        page = extract_html(resp.text, base_url=str(resp.url))
        text = (page.get("text") or "").strip()
    else:
        text = resp.text.strip()
    if len(text) < 100:
        raise ValueError("No readable content found on the page")

    title = (title or "").strip() or f"{urlparse(str(resp.url)).hostname or 'web'} page"
    filename = f"{_slugify(title, 'web_page')}.txt"
    return await _submit_into(
        db, owner_id, college_id, filename, text.encode("utf-8"),
        title=title,
        source_kind=SOURCE_KIND_URL,
        document_type="webpage",
        category=category,
    )


def _flatten_college(data: dict, max_lines: int = 500) -> str:
    """Render a college data dict as a flat, indexable text digest."""
    lines: list[str] = []

    def walk(prefix: str, value) -> None:
        if len(lines) >= max_lines:
            return
        if value is None:
            return
        if isinstance(value, str):
            value = value.strip()
            if value:
                lines.append(f"{prefix}: {value}")
        elif isinstance(value, (int, float)):
            lines.append(f"{prefix}: {value}")
        elif isinstance(value, list):
            if value:
                lines.append(f"{prefix}: {', '.join(str(item) for item in value)}")
        elif isinstance(value, dict):
            for key in sorted(value):
                walk(f"{prefix}.{key}" if prefix else key, value[key])

    walk("", data)
    return "\n".join(lines[:max_lines])


async def ensure_backfill(db: Session, college_id: str) -> dict:
    """Lazily generate the RAG digest for a college from hard-coded data.

    Runs once per college: creates a .txt source of the flattened data and
    queues an ingestion job. Returns {"created": bool, ...}.
    """
    from app.ingest.service import submit_upload_job

    college = _get_college(college_id)
    if not college:
        raise ValueError(f"Unknown college: {college_id}")
    existing = (
        db.query(Document)
        .filter(
            Document.college_id == college_id,
            Document.scope == SCOPE_COLLEGE,
            Document.source_kind == SOURCE_KIND_BACKFILL,
        )
        .all()
    )
    if any(doc.status in ("queued", "processing", "indexing", "ready") for doc in existing):
        return {"created": False, "reason": "already indexed"}
    for doc in existing:  # stale failed backfills are dropped and retried
        db.delete(doc)
    db.commit()

    digest = _flatten_college(college)
    filename = f"auto_{_slugify(college_id)}.txt"
    result = await submit_upload_job(
        db, None, filename, digest.encode("utf-8"),
        title=f"{college['name']} — College Information (Auto)",
        metadata={
            **_college_meta(college_id, college["name"], SOURCE_KIND_BACKFILL),
            "document_type": "college_profile",
        },
    )
    log.info(
        "College backfill queued for %s (doc %s)",
        college_id,
        result.get("document_id"),
    )
    return {"created": True, "document_id": result.get("document_id")}


def list_sources(db: Session, college_id: str) -> list[dict]:
    """List all knowledge sources owned by a college (newest first)."""
    docs = (
        db.query(Document)
        .filter(
            Document.college_id == college_id,
            Document.scope == SCOPE_COLLEGE,
        )
        .order_by(Document.created_at.desc())
        .all()
    )
    return [source_view(doc) for doc in docs]


def source_view(doc: Document) -> dict:
    return {
        "id": str(doc.id),
        "title": doc.title,
        "original_filename": doc.original_filename,
        "source_kind": doc.source_kind or SOURCE_KIND_UPLOAD,
        "document_type": doc.document_type,
        "category": doc.category,
        "status": doc.status,
        "chunks": doc.chunk_count or 0,
        "error": doc.error,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


def get_source(db: Session, source_id: str) -> Document | None:
    try:
        uid = uuid.UUID(source_id)
    except (ValueError, TypeError):
        return None
    return db.get(Document, uid)


def _assert_college_source(doc: Document | None) -> None:
    if doc is None:
        raise ValueError("Knowledge source not found")
    if doc.scope != SCOPE_COLLEGE:
        raise ValueError("Not a college knowledge source")


def summarize_college(db: Session, college_id: str) -> dict:
    """Knowledge summary for the college's info panel."""
    college = _get_college(college_id)
    docs = (
        db.query(Document)
        .filter(
            Document.college_id == college_id,
            Document.scope == SCOPE_COLLEGE,
        )
        .all()
    )
    total_chunks = 0
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    last_updated = None
    for doc in docs:
        kind = doc.source_kind or SOURCE_KIND_MANUAL
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_status[doc.status] = by_status.get(doc.status, 0) + 1
        if doc.status == "ready":
            total_chunks += doc.chunk_count or 0
        if doc.updated_at and (last_updated is None or doc.updated_at > last_updated):
            last_updated = doc.updated_at
    return {
        "college_id": college_id,
        "college_name": college["name"] if college else college_id,
        "sources": len(docs),
        "chunks": total_chunks,
        "by_source_kind": by_kind,
        "by_status": by_status,
        "last_updated": last_updated.isoformat() if last_updated else None,
    }


def archive_source(db: Session, source_id: str) -> dict:
    """Archive a source: row stays, vectors are removed (no longer retrieved)."""
    doc = get_source(db, source_id)
    _assert_college_source(doc)
    if doc.status != "archived":
        from app.ingest.store import delete_document

        doc.status = "archived"
        db.commit()
        delete_document(str(doc.id))  # best-effort vector cleanup
    return source_view(doc)


async def restore_source(db: Session, source_id: str) -> dict:
    """Restore an archived source by re-indexing it from its stored file."""
    doc = get_source(db, source_id)
    _assert_college_source(doc)
    if doc.status == "archived":
        doc.status = "queued"
        db.commit()
        return await reindex_source(db, None, source_id)
    return source_view(doc)


def delete_source(db: Session, source_id: str) -> dict:
    """Hard-delete a source (rows + vectors)."""
    from app.ingest.store import delete_document

    doc = get_source(db, source_id)
    _assert_college_source(doc)
    from app.models import DocumentChunk

    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()
    db.delete(doc)
    db.commit()
    delete_document(source_id)
    return {"status": "deleted", "id": source_id}


async def reindex_source(db: Session, owner_id, source_id: str) -> dict:
    """Re-run ingestion for a source using its stored file (unchanged metadata)."""
    from app.ingest.service import submit_upload_job
    from app.ingest.store import delete_document

    doc = get_source(db, source_id)
    _assert_college_source(doc)
    upload_dir = Path(settings.CHROMA_PERSIST_DIR).parent / "uploads"
    path = upload_dir / doc.filename
    if not path.exists():
        raise ValueError("Source file no longer available for reindex")

    metadata = {
        "college_id": doc.college_id,
        "college_name": doc.college_name,
        "scope": doc.scope,
        "source_kind": doc.source_kind,
    }
    if doc.document_type:
        metadata["document_type"] = doc.document_type
    if doc.category:
        metadata["category"] = doc.category

    data = path.read_bytes()
    result = await submit_upload_job(
        db, owner_id, doc.original_filename or doc.filename, data,
        title=doc.title, existing_doc_id=source_id, metadata=metadata,
    )
    # Old vectors are rebuilt by the new job — best-effort cleanup.
    delete_document(source_id)
    return result