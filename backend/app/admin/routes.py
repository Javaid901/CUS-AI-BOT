"""
backend/app/admin/routes.py

Admin document management.

  GET    /api/documents                       -> [{id, filename, status, chunks}]
  POST   /api/documents/upload                (multipart "file") -> 202 {upload_id, document_id, status}
  DELETE /api/documents/{id}                  -> delete doc + vectors
  POST   /api/documents/{id}/reindex          -> 202 {upload_id, document_id}
  GET    /api/admin/jobs                      -> list recent upload jobs
  GET    /api/admin/jobs/{upload_id}          -> job detail
  POST   /api/admin/jobs/{upload_id}/cancel   -> cancel a queued/running job
  POST   /api/admin/jobs/{upload_id}/retry    -> retry a failed job
  GET    /api/admin/jobs/{upload_id}/events   -> SSE stream for job progress
  GET    /api/admin/jobs/events               -> SSE stream for all jobs
  GET    /api/admin/logs                      -> recent audit logs
  POST   /api/admin/sync-website              -> sync official website documents
  GET    /api/admin/kb-stats                  -> knowledge base statistics
  GET    /api/admin/kb-health                 -> knowledge base health check
  GET    /api/admin/metrics/ingestion         -> ingestion performance metrics

Spec-listed aliases are also mounted (see main.py) so both contract styles work:
  /api/admin/documents, /api/admin/upload, /api/admin/document/{id}, /api/admin/reindex/{id}
"""

from __future__ import annotations

import uuid

from app.auth.security import require_admin
from app.config import settings
from app.database import get_db
from app.ingest.job_manager import job_manager
from app.ingest.service import submit_upload_job
from app.ingest.sse import sse_manager
from app.ingest.store import delete_document as _delete_chroma_vectors
from app.ingest.worker import worker
from app.models import AuditLog, Conversation, Document, User
from app.utils.files import validate_upload
from app.utils.logging import audit
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

router = APIRouter(tags=["admin"])
_protected = Depends(require_admin)


def _doc_view(doc: Document) -> dict:
    return {
        "id": str(doc.id),
        "filename": doc.filename,
        "original_filename": doc.original_filename,
        "title": doc.title,
        "status": doc.status,
        "chunks": doc.chunk_count,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "error": doc.error,
    }


@router.get(f"{settings.API_PREFIX}/documents")
def list_documents(
    db: Session = Depends(get_db),
    current: User = _protected,
    limit: int = Query(100, ge=1, le=500),
):
    docs = db.query(Document).order_by(Document.created_at.desc()).limit(limit).all()
    return [_doc_view(d) for d in docs]


@router.post(f"{settings.API_PREFIX}/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current: User = _protected,
):
    data = await file.read()
    try:
        validate_upload(file.filename or "file", len(data))
    except HTTPException as exc:
        audit(db, "upload_rejected", actor_id=str(current.id), actor_role=current.role, target=file.filename, detail=exc.detail)
        raise
    try:
        result = await submit_upload_job(
            db, current.id, file.filename, data, title=file.filename
        )
    except Exception as exc:
        audit(db, "upload_failed", actor_id=str(current.id), actor_role=current.role, target=file.filename, detail=str(exc)[:300])
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")

    if result.get("status") == "duplicate":
        audit(db, "upload_duplicate", actor_id=str(current.id), actor_role=current.role, target=file.filename)
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "document_id": result.get("document_id"), "detail": "Already Indexed"},
        )

    audit(db, "upload_queued", actor_id=str(current.id), actor_role=current.role, target=result.get("document_id"), detail=file.filename)
    return JSONResponse(
        status_code=202,
        content=result,
    )


@router.delete(f"{settings.API_PREFIX}/documents/{{doc_id}}")
def delete_document(
    doc_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    try:
        uid = uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document id")
    doc = db.get(Document, uid)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    # Remove DB rows first; if this fails the Chroma vectors stay consistent.
    from app.models import DocumentChunk

    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()
    db.delete(doc)
    db.commit()
    # Best-effort vector cleanup — logged, never raises.
    _delete_chroma_vectors(str(doc.id))
    audit(db, "delete", actor_id=str(current.id), actor_role=current.role, target=doc_id)
    return {"status": "deleted", "id": doc_id}


@router.post(f"{settings.API_PREFIX}/documents/{{doc_id}}/reindex")
async def reindex_document(
    doc_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    try:
        uid = uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document id")
    doc = db.get(Document, uid)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Locate the raw file on disk. If missing, fail clearly.
    from pathlib import Path

    upload_dir = Path(settings.CHROMA_PERSIST_DIR).parent / "uploads"
    path = upload_dir / doc.filename
    if not path.exists():
        raise HTTPException(status_code=409, detail="Source file no longer available for reindex")

    # Clear existing chunks from DB first, then Chroma (best-effort).
    from app.models import DocumentChunk
    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()
    db.commit()
    _delete_chroma_vectors(str(doc.id))

    try:
        data = path.read_bytes()
        result = await submit_upload_job(
            db, current.id, doc.original_filename or doc.filename, data,
            title=doc.title, existing_doc_id=doc_id,
        )
    except Exception as exc:
        audit(db, "reindex_failed", actor_id=str(current.id), actor_role=current.role, target=doc_id, detail=str(exc)[:300])
        raise HTTPException(status_code=500, detail=f"Reindex failed: {exc}")
    audit(db, "reindex_queued", actor_id=str(current.id), actor_role=current.role, target=doc_id)
    return JSONResponse(status_code=202, content=result)


@router.get(f"{settings.API_PREFIX}/admin/logs")
def list_logs(
    db: Session = Depends(get_db),
    current: User = _protected,
    limit: int = Query(100, ge=1, le=500),
):
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": str(l.id),
            "action": l.action,
            "actor_role": l.actor_role,
            "target": l.target,
            "detail": l.detail,
            "ip": l.ip,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in logs
    ]


@router.post(f"{settings.API_PREFIX}/admin/sync-website")
def sync_website(
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Sync official CUS website documents into the knowledge base."""
    from app.ingest.knowledge_base import sync_all

    result = sync_all()
    audit(db, "sync", actor_id=str(current.id), actor_role=current.role, detail=f"Downloaded {result.get('downloaded', 0)} files")
    return result


@router.get(f"{settings.API_PREFIX}/admin/kb-stats")
def kb_stats(
    current: User = _protected,
):
    """Knowledge base statistics."""
    from app.ingest.knowledge_base import get_knowledge_stats

    return get_knowledge_stats()


@router.get(f"{settings.API_PREFIX}/admin/kb-health")
def kb_health(
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Knowledge base health check — counts, model info, DB size."""
    from app.ingest.generator import is_ollama_available, list_models
    from app.ingest.knowledge_base import get_knowledge_stats
    from sqlalchemy import func

    doc_count = db.query(Document).count()
    chunk_count_row = db.query(func.sum(Document.chunk_count)).filter(Document.status == "ready").first()
    chunk_count = chunk_count_row[0] or 0 if chunk_count_row else 0
    conv_count = db.query(Conversation).count()

    kb = get_knowledge_stats()
    ollama_ok = is_ollama_available()

    return {
        "status": "ok" if ollama_ok else "degraded",
        "documents": {"total": doc_count, "ready": db.query(Document).filter(Document.status == "ready").count()},
        "chunks": chunk_count,
        "conversations": conv_count,
        "knowledge_base": kb,
        "ollama": {"reachable": ollama_ok, "models": list_models() if ollama_ok else [], "llm": settings.LLM_MODEL, "embed": settings.EMBED_MODEL},
        "db_size_bytes": _db_size(),
    }


def _db_size() -> int:
    from pathlib import Path

    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        path_part = url.replace("sqlite:///", "").split("?")[0]
        db_path = Path(path_part)
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        if db_path.exists():
            return db_path.stat().st_size
    return 0


# ---------------------------------------------------------------------------
# Upload Job management
# ---------------------------------------------------------------------------


@router.get(f"{settings.API_PREFIX}/admin/jobs")
async def list_jobs(
    current: User = _protected,
    limit: int = Query(50, ge=1, le=200),
):
    """List recent upload jobs with status and metrics."""
    jobs = await job_manager.list_jobs(limit=limit)
    return jobs


@router.get(f"{settings.API_PREFIX}/admin/jobs/events")
async def all_job_events(
    current: User = _protected,
):
    """SSE stream for all upload jobs (global)."""
    return StreamingResponse(
        sse_manager.global_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(f"{settings.API_PREFIX}/admin/jobs/{{upload_id}}")
async def get_job(
    upload_id: str,
    current: User = _protected,
):
    """Get details for a single upload job."""
    job = await job_manager.get(upload_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@router.post(f"{settings.API_PREFIX}/admin/jobs/{{upload_id}}/cancel")
async def cancel_job(
    upload_id: str,
    current: User = _protected,
):
    """Cancel a queued or running upload job."""
    cancelled = await job_manager.cancel(upload_id)
    if not cancelled:
        job = await job_manager.get(upload_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"status": "cannot_cancel", "current_status": job.status}
    await sse_manager.publish(upload_id, "cancelled", {"upload_id": upload_id})
    return {"status": "cancelled", "upload_id": upload_id}


@router.post(f"{settings.API_PREFIX}/admin/jobs/{{upload_id}}/retry")
async def retry_job(
    upload_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Retry a failed upload job."""
    job = await job_manager.get(upload_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "failed":
        return {"status": "cannot_retry", "current_status": job.status}

    # Clear old Chroma vectors if any
    if job.document_id:
        _delete_chroma_vectors(job.document_id)

    # Reset job status
    await job_manager.update(
        upload_id,
        status="queued",
        progress=0.0,
        current_stage="",
        error=None,
        finished_at=None,
        started_at=None,
        cancelled=False,
    )

    # Reset document status
    if job.document_id:
        try:
            doc = db.get(Document, uuid.UUID(job.document_id))
            if doc:
                doc.status = "queued"
                doc.error = None
                db.commit()
        except Exception:
            pass

    await worker.enqueue(upload_id)
    await sse_manager.publish(upload_id, "retrying", {"upload_id": upload_id})
    return {"status": "queued", "upload_id": upload_id}


@router.get(f"{settings.API_PREFIX}/admin/jobs/{{upload_id}}/events")
async def job_events(
    upload_id: str,
    current: User = _protected,
):
    """SSE stream for a specific upload job."""
    return StreamingResponse(
        sse_manager.event_generator(upload_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(f"{settings.API_PREFIX}/admin/metrics/ingestion")
async def ingestion_metrics(
    current: User = _protected,
):
    """Ingestion pipeline performance metrics."""
    return await job_manager.get_metrics()


@router.get(f"{settings.API_PREFIX}/admin/metrics/operations")
async def operations_metrics(
    current: User = _protected,
):
    """Request management layer operational metrics.

    Returns real-time data about the token bucket, request queue, service
    semaphores, response cache, backpressure, worker pool, and RPS/latency.
    """
    from app.request_manager.backpressure import backpressure
    from app.request_manager.metrics import request_metrics
    from app.request_manager.request_queue import request_queue as _queue
    from app.request_manager.response_cache import response_cache
    from app.request_manager.service_semaphores import service_semaphores
    from app.request_manager.token_bucket import token_bucket
    from app.request_manager.worker_pool import worker_pool

    return {
        "token_bucket": token_bucket.stats(),
        "queue": _queue.stats,
        "queue_snapshot": await _queue.snapshot(),
        "semaphores": service_semaphores.stats,
        "cache": response_cache.stats,
        "backpressure": backpressure.stats,
        "worker_pool": worker_pool.stats,
        "request_metrics": request_metrics.snapshot,
        "uptime_sec": request_metrics.uptime_seconds,
    }


# ---------------------------------------------------------------------------
# Knowledge Sync endpoints (admin-only document acquisition)
# ---------------------------------------------------------------------------

@router.post(f"{settings.API_PREFIX}/admin/knowledge-sync/run")
async def knowledge_sync_run(
    db: Session = Depends(get_db),
    current: User = _protected,
    urls: str | None = Query(None, description="Comma-separated URLs to sync"),
    auto_discover: bool = Query(False, description="Crawl approved domains for documents"),
):
    """Run Knowledge Sync: download approved documents and ingest via background pipeline."""
    from app.knowledge_sync.engine import SyncEngine

    engine = SyncEngine(db)
    url_list = [u.strip() for u in urls.split(",") if u.strip()] if urls else None
    result = await engine.run_async(url_list, auto_discover=auto_discover)
    audit(db, "knowledge_sync", actor_id=str(current.id), actor_role=current.role, detail=f"Downloaded {result.get('downloaded', 0)} files")
    return result


@router.get(f"{settings.API_PREFIX}/admin/knowledge-sync/status")
def knowledge_sync_status(
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Get Knowledge Sync status and stats."""
    from app.knowledge_sync.engine import SyncEngine

    engine = SyncEngine(db)
    return engine.get_status()


@router.get(f"{settings.API_PREFIX}/admin/knowledge-sync/sources")
def knowledge_sync_sources(
    db: Session = Depends(get_db),
    current: User = _protected,
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """List synced sources with optional status filter."""
    from app.knowledge_sync.engine import SyncEngine

    engine = SyncEngine(db)
    return engine.list_sources(status=status, limit=limit)


@router.post(f"{settings.API_PREFIX}/admin/knowledge-sync/approve/{{sync_id}}")
def knowledge_sync_approve(
    sync_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Approve a synced file for ingestion (review mode)."""
    from app.knowledge_sync.engine import SyncEngine

    engine = SyncEngine(db)
    result = engine.approve_for_ingestion(sync_id)
    audit(db, "knowledge_sync_approve", actor_id=str(current.id), actor_role=current.role, target=sync_id, detail=result.get("status"))
    return result


# ---------------------------------------------------------------------------
# College-Course Mapping admin endpoints
# ---------------------------------------------------------------------------


@router.get(f"{settings.API_PREFIX}/admin/college-mappings")
def college_mappings(
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Get college-course mapping overview for admin."""
    from app.college.course_map import (
        get_all_college_ids,
        get_all_programme_ids,
        get_college_programmes,
        get_colleges_for_programme,
    )
    from app.college.data import COLLEGES

    result = {
        "total_colleges": len(get_all_college_ids()),
        "total_programmes": len(get_all_programme_ids()),
        "colleges": {},
        "missing_mappings": [],
    }
    for cid in get_all_college_ids():
        programmes = get_college_programmes(cid)
        result["colleges"][cid] = {
            "name": COLLEGES.get(cid, {}).get("name", ""),
            "programme_count": len(programmes),
            "programmes": [p["id"] for p in programmes],
        }
    # Check for programmes not offered by any college
    from app.orchestrator.context import PROGRAMME_ALIASES
    set(PROGRAMME_ALIASES.keys())
    offered = set(get_all_programme_ids())
    for pid in sorted(offered):
        if not get_colleges_for_programme(pid):
            result["missing_mappings"].append(pid)
    return result


@router.get(f"{settings.API_PREFIX}/admin/college-mappings/college/{{college_id}}")
def college_mapping_detail(
    college_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Get detailed course mapping for a specific college."""
    from app.college.course_map import get_college_programmes

    programmes = get_college_programmes(college_id)
    if not programmes:
        raise HTTPException(status_code=404, detail="College not found or no programmes mapped")
    return {
        "college_id": college_id,
        "programmes": programmes,
    }


@router.get(f"{settings.API_PREFIX}/admin/college-mappings/programme/{{programme_id}}")
def programme_mapping_detail(
    programme_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Get colleges offering a specific programme."""
    from app.college.course_map import get_colleges_for_programme

    colleges = get_colleges_for_programme(programme_id)
    return {
        "programme_id": programme_id,
        "colleges": colleges,
    }


# ---------------------------------------------------------------------------
# RAG Debug / Diagnostic endpoint (admin-only)
# ---------------------------------------------------------------------------


@router.get("/api/admin/rag-debug")
def rag_debug(
    q: str = Query(..., min_length=2, description="Query to test retrieval against"),
    current: User = Depends(require_admin),
):
    """Run the full hybrid retrieval pipeline and return diagnostics.
    
    This is an admin-only endpoint for inspecting retrieval quality.
    It runs query rewriting, hybrid search, reranking, context compression,
    and answer verification, then returns the full diagnostic state.
    """
    from app.ingest.retriever import clear_diagnostics, retrieve_hybrid

    clear_diagnostics()
    chunks = retrieve_hybrid(q, top_k=6)

    from app.ingest.retriever import get_diagnostics

    diag = get_diagnostics()

    return {
        "query": q,
        "chunk_count": len(chunks),
        "chunks": [
            {
                "document_title": c.get("document_title"),
                "page_number": c.get("page_number"),
                "heading": c.get("heading"),
                "score": c.get("rerank_score") or c.get("combined_score"),
                "content_preview": c.get("content", "")[:150],
            }
            for c in chunks
        ],
        "diagnostics": diag,
    }


# ---------------------------------------------------------------------------
# Demo Data Manager (admin-only)
# ---------------------------------------------------------------------------


@router.get("/api/admin/demo/status")
def demo_status(db: Session = Depends(get_db), current=Depends(require_admin)):
    """Return demo data status — student count and per-service record counts."""
    from app.models import Student, StudentSession
    from app.models.demo_models import (
        BacklogStatus,
        CourseRegistration,
        FeeReceipt,
        HelpdeskTicket,
        MigrationCertificate,
        Revaluation,
        StudentAdmitCard,
        StudentAttendance,
        StudentExamForm,
        StudentResult,
        StudentTranscript,
        XeroxRequest,
    )
    tables = {
        "students": Student,
        "results": StudentResult,
        "admit_cards": StudentAdmitCard,
        "exam_forms": StudentExamForm,
        "fee_receipts": FeeReceipt,
        "attendance": StudentAttendance,
        "transcripts": StudentTranscript,
        "migration_certificates": MigrationCertificate,
        "revaluations": Revaluation,
        "xerox_requests": XeroxRequest,
        "backlogs": BacklogStatus,
        "course_registrations": CourseRegistration,
        "helpdesk_tickets": HelpdeskTicket,
        "active_sessions": StudentSession,
    }
    counts = {}
    for name, model in tables.items():
        try:
            counts[name] = db.query(model).count()
        except Exception:
            counts[name] = -1
    return {
        "demo_mode": settings.DEMO_MODE,
        "counts": counts,
    }


@router.post("/api/admin/demo/seed")
def seed_demo_data(db: Session = Depends(get_db), current=Depends(require_admin)):
    """Seed demo data into all service tables."""
    from app.models import Student
    from app.seeders.demo_data import seed_demo_data as _seed
    existing = db.query(Student).count()
    if existing > 0:
        return {"message": f"Demo data already exists ({existing} students). Use 'reset' first to regenerate.", "seeded": 0}
    count = _seed(db, count=settings.DEMO_STUDENT_COUNT)
    return {"message": f"Demo data seeded for {count} students.", "seeded": count}


@router.post("/api/admin/demo/reset")
def reset_demo_data(db: Session = Depends(get_db), current=Depends(require_admin)):
    """Delete all demo data from all service tables + student sessions + students."""
    from app.seeders.demo_data import reset_demo_data as _reset
    _reset(db)
    return {"message": "All demo data has been deleted."}


@router.post("/api/admin/demo/regenerate")
def regenerate_demo_data(db: Session = Depends(get_db), current=Depends(require_admin)):
    """Reset and re-seed demo data."""
    from app.seeders.demo_data import reset_demo_data as _reset
    from app.seeders.demo_data import seed_demo_data as _seed
    _reset(db)
    count = _seed(db, count=settings.DEMO_STUDENT_COUNT)
    return {"message": f"Demo data regenerated for {count} students.", "seeded": count}


@router.get("/api/admin/demo/export")
def export_demo_data(db: Session = Depends(get_db), current=Depends(require_admin)):
    """Export all demo students as JSON."""
    from app.models import Student
    students = db.query(Student).order_by(Student.reg_no).all()
    result = []
    for s in students:
        result.append({
            "reg_no": s.reg_no,
            "roll_no": s.roll_no,
            "name": s.name,
            "father_name": s.father_name,
            "mother_name": s.mother_name,
            "dob": s.dob,
            "gender": s.gender,
            "category": s.category,
            "email": s.email,
            "phone": s.phone,
            "college": s.college,
            "programme": s.programme,
            "semester": s.current_semester,
            "admission_year": s.admission_year,
            "batch": s.batch,
            "status": s.status,
        })
    return {"students": result, "count": len(result)}


@router.delete("/api/admin/demo/students")
def delete_demo_students(db: Session = Depends(get_db), current=Depends(require_admin)):
    """Delete all demo students (triggers cascade to all service data)."""
    from app.models import Student, StudentSession
    db.query(StudentSession).delete()
    db.query(Student).delete()
    db.commit()
    return {"message": "All demo students and their service data have been deleted."}
