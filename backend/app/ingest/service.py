"""
backend/app/ingest/service.py

Document ingestion orchestration.

Status lifecycle on the Document row:
  queued -> processing -> indexing -> ready | failed
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.config import settings
from app.models import Document
from app.utils.files import ensure_dir, sanitize_filename, validate_upload
from app.utils.logging import log
from sqlalchemy.orm import Session


def ingest_file(
    db: Session,
    owner_id,
    original_filename: str,
    file_bytes: bytes,
    title: str | None = None,
    existing_doc: Document | None = None,
) -> Document:
    """Synchronous ingestion (kept for backward compatibility).

    New code should use submit_upload_job() for async background ingestion.
    """
    from app.ingest.chunker import chunk_pages
    from app.ingest.embed import embed_documents, mark_document_failed
    from app.ingest.store import add_chunks_with_embeddings
    from app.utils.files import extract_text

    ext = validate_upload(original_filename, len(file_bytes))
    stored_name = sanitize_filename(original_filename)
    upload_dir = ensure_dir(str(Path(settings.CHROMA_PERSIST_DIR).parent / "uploads"))
    dest = upload_dir / stored_name
    dest.write_bytes(file_bytes)
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    if existing_doc:
        doc = existing_doc
        doc.filename = stored_name
        doc.original_filename = original_filename
        doc.file_type = ext
        doc.file_size = len(file_bytes)
        doc.sha256 = file_hash
        doc.status = "processing"
        doc.chunk_count = 0
        doc.error = None
    else:
        doc = Document(
            owner_id=owner_id,
            title=title or original_filename,
            filename=stored_name,
            original_filename=original_filename,
            file_type=ext,
            file_size=len(file_bytes),
            sha256=file_hash,
            status="processing",
            chunk_count=0,
        )
        db.add(doc)
    db.commit()
    db.refresh(doc)

    try:
        doc.status = "indexing"
        db.commit()
        pages = extract_text(str(dest), ext)
        chunks = chunk_pages(pages)
        if not chunks:
            raise ValueError("No extractable text found in document.")
        texts = [c["content"] for c in chunks]
        embeddings = embed_documents(texts)
        add_chunks_with_embeddings(str(doc.id), doc.title, chunks, embeddings)
        doc.chunk_count = len(chunks)
        doc.status = "ready"
        db.commit()
        log.info("Ingested document %s (%d chunks)", doc.id, len(chunks))
    except Exception as exc:
        db.rollback()
        mark_document_failed(db, str(doc.id), str(exc))
        log.exception("Ingestion failed for %s: %s", doc.id, exc)
        raise
    return doc


async def submit_upload_job(
    db: Session,
    owner_id,
    original_filename: str,
    file_bytes: bytes,
    title: str | None = None,
    existing_doc_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Create a background upload job and return immediately.

    Returns {upload_id, document_id, status: "queued"}.
    """
    from app.ingest.job_manager import job_manager
    from app.ingest.worker import worker

    ext = validate_upload(original_filename, len(file_bytes))
    stored_name = sanitize_filename(original_filename)
    upload_dir = ensure_dir(str(Path(settings.CHROMA_PERSIST_DIR).parent / "uploads"))
    dest = upload_dir / stored_name
    dest.write_bytes(file_bytes)
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    if existing_doc_id:
        try:
            import uuid
            doc = db.get(Document, uuid.UUID(existing_doc_id))
        except (ValueError, Exception):
            doc = None
        if doc:
            doc.filename = stored_name
            doc.original_filename = original_filename
            doc.file_type = ext
            doc.file_size = len(file_bytes)
            doc.sha256 = file_hash
            doc.status = "queued"
            doc.chunk_count = 0
            doc.error = None
            db.commit()
            db.refresh(doc)
        else:
            existing_doc_id = None

    if not existing_doc_id:
        existing_hash = (
            db.query(Document)
            .filter(Document.sha256 == file_hash, Document.status == "ready")
            .first()
        )
        if existing_hash:
            log.info("Duplicate upload detected (hash match): %s", original_filename)
            return {
                "upload_id": None,
                "document_id": str(existing_hash.id),
                "status": "duplicate",
                "detail": "Already Indexed",
            }

        doc = Document(
            owner_id=owner_id,
            title=title or original_filename,
            filename=stored_name,
            original_filename=original_filename,
            file_type=ext,
            file_size=len(file_bytes),
            sha256=file_hash,
            status="queued",
            chunk_count=0,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

    job = await job_manager.create(
        filename=stored_name,
        file_size=len(file_bytes),
        file_path=str(dest),
        sha256=file_hash,
        source="upload",
        document_id=str(doc.id),
        metadata=metadata,
    )
    await worker.enqueue(job.upload_id)
    log.info(
        "Upload job %s created for %s (doc %s)",
        job.upload_id,
        original_filename,
        doc.id,
    )
    return {
        "upload_id": job.upload_id,
        "document_id": str(doc.id),
        "status": "queued",
    }
