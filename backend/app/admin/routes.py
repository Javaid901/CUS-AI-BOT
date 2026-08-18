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
  GET    /api/admin/kb-health                 -> knowledge base health check
  GET    /api/admin/metrics/ingestion         -> ingestion performance metrics

Spec-listed aliases are also mounted (see main.py) so both contract styles work:
  /api/admin/documents, /api/admin/upload, /api/admin/document/{id}, /api/admin/reindex/{id}
"""

from __future__ import annotations

import uuid

from app.auth.security import require_admin, require_superadmin
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
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
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
        "academic_scheme": doc.academic_scheme,
        "programme": doc.programme,
        "department": doc.department,
        "batch": doc.batch,
        "semester": doc.semester,
        "document_type": doc.document_type,
        "category": doc.category,
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
    academic_scheme: str | None = Form(None),
    programme: str | None = Form(None),
    department: str | None = Form(None),
    batch: str | None = Form(None),
    semester: str | None = Form(None),
    document_type: str | None = Form(None),
    category: str | None = Form(None),
    db: Session = Depends(get_db),
    current: User = _protected,
):
    data = await file.read()
    try:
        validate_upload(file.filename or "file", len(data))
    except HTTPException as exc:
        audit(db, "upload_rejected", actor_id=str(current.id), actor_role=current.role, target=file.filename, detail=exc.detail)
        raise
    metadata = {
        "academic_scheme": academic_scheme,
        "programme": programme,
        "department": department,
        "batch": batch,
        "semester": semester,
        "document_type": document_type,
        "category": category,
    }
    try:
        result = await submit_upload_job(
            db, current.id, file.filename, data, title=file.filename, metadata=metadata
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
            metadata={
                "academic_scheme": doc.academic_scheme,
                "programme": doc.programme,
                "department": doc.department,
                "batch": doc.batch,
                "semester": doc.semester,
                "document_type": doc.document_type,
                "category": doc.category,
            },
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


@router.get(f"{settings.API_PREFIX}/admin/kb-health")
def kb_health(
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Knowledge base health check — counts, model info, DB size."""
    from app.ingest.generator import is_ollama_available, list_models
    from sqlalchemy import func

    doc_count = db.query(Document).count()
    chunk_count_row = db.query(func.sum(Document.chunk_count)).filter(Document.status == "ready").first()
    chunk_count = chunk_count_row[0] or 0 if chunk_count_row else 0
    conv_count = db.query(Conversation).count()

    from app.knowledge_sync.web_engine import WebsiteSyncEngine

    ws = WebsiteSyncEngine(db).get_status()
    ollama_ok = is_ollama_available()

    return {
        "status": "ok" if ollama_ok else "degraded",
        "documents": {"total": doc_count, "ready": db.query(Document).filter(Document.status == "ready").count()},
        "chunks": chunk_count,
        "conversations": conv_count,
        "knowledge_base": {
            "total_files": ws.get("indexed_pages", 0),
            "total_pages": ws.get("total_pages", 0),
            "categories": ws.get("categories", {}),
        },
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
# Website Knowledge Sync endpoints (enterprise crawler engine)
# ---------------------------------------------------------------------------


class WebsiteSyncToggle(BaseModel):
    enabled: bool | None = None
    schedule: str | None = None  # disabled | manual | hourly | daily | weekly | monthly


class WebsiteSyncManualRun(BaseModel):
    urls: list[str] | None = None
    trigger: str = "manual"


@router.post(f"{settings.API_PREFIX}/admin/website-sync/run")
async def website_sync_run(
    body: WebsiteSyncManualRun | None = None,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Run a full (or URL-scoped) website sync pass."""
    from app.knowledge_sync.web_engine import WebsiteSyncEngine

    engine = WebsiteSyncEngine(db)
    result = await engine.run_async(
        trigger=(body.trigger if body else "manual"),
        seed_urls=(body.urls if body else None),
    )
    audit(db, "website_sync", actor_id=str(current.id), actor_role=current.role,
          detail=f"{result.get('status')} · {result.get('new_pages', 0)} new, "
                 f"{result.get('updated_pages', 0)} updated")
    return result


@router.get(f"{settings.API_PREFIX}/admin/website-sync/status")
def website_sync_status(
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Dashboard status: toggle state, counts, category breakdown, last run."""
    from app.knowledge_sync.web_engine import WebsiteSyncEngine

    return WebsiteSyncEngine(db).get_status()


@router.post(f"{settings.API_PREFIX}/admin/website-sync/toggle")
def website_sync_toggle(
    body: WebsiteSyncToggle,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Enable/disable scheduled sync and set cadence."""
    from app.knowledge_sync.web_engine import SCHEDULE_HOURS, load_state, save_state

    state = load_state()
    if body.enabled is not None:
        state["enabled"] = body.enabled
    if body.schedule:
        if body.schedule not in SCHEDULE_HOURS:
            raise HTTPException(status_code=422, detail=f"Unknown schedule '{body.schedule}'")
        state["schedule"] = body.schedule
        state["hours"] = SCHEDULE_HOURS[body.schedule]
    save_state(state)
    audit(db, "website_sync_toggle", actor_id=str(current.id), actor_role=current.role,
          detail=f"enabled={state['enabled']} schedule={state['schedule']}")
    return state


@router.get(f"{settings.API_PREFIX}/admin/website-sync/pages")
def website_sync_pages(
    db: Session = Depends(get_db),
    current: User = _protected,
    category: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List crawled pages with filters (category / status / search)."""
    from app.knowledge_sync.web_engine import WebsiteSyncEngine

    return WebsiteSyncEngine(db).list_pages(
        category=category, status=status, q=q, limit=limit, offset=offset
    )


@router.get(f"{settings.API_PREFIX}/admin/website-sync/pages/{{page_id}}")
def website_sync_page_detail(
    page_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Page detail incl. version history."""
    from app.models.website_sync import WebsitePage
    from app.knowledge_sync.web_engine import WebsiteSyncEngine

    page = db.get(WebsitePage, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    data = page.to_dict()
    data["versions"] = WebsiteSyncEngine(db).list_versions(page_id)
    return data


@router.get(f"{settings.API_PREFIX}/admin/website-sync/runs")
def website_sync_runs(
    db: Session = Depends(get_db),
    current: User = _protected,
    limit: int = Query(25, ge=1, le=200),
):
    """List recent crawl runs (dashboard history table)."""
    from app.knowledge_sync.web_engine import WebsiteSyncEngine

    return WebsiteSyncEngine(db).list_runs(limit=limit)


@router.post(f"{settings.API_PREFIX}/admin/website-sync/pages/{{page_id}}/reindex")
def website_sync_reindex(
    page_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Re-index a page's content into the RAG store."""
    from app.knowledge_sync.web_engine import WebsiteSyncEngine

    result = WebsiteSyncEngine(db).reindex_page(page_id)
    audit(db, "website_sync_reindex", actor_id=str(current.id), actor_role=current.role,
          target=page_id, detail=str(result.get("indexed")))
    return result


@router.delete(f"{settings.API_PREFIX}/admin/website-sync/pages/{{page_id}}")
def website_sync_archive(
    page_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Archive a page (versioned snapshot; never hard-deleted)."""
    from app.knowledge_sync.web_engine import WebsiteSyncEngine

    result = WebsiteSyncEngine(db).archive_page(page_id)
    audit(db, "website_sync_archive", actor_id=str(current.id), actor_role=current.role,
          target=page_id, detail=result.get("status"))
    return result


@router.get(f"{settings.API_PREFIX}/admin/website-sync/duplicates")
def website_sync_duplicates(
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Scan pages sharing identical content hashes (duplicate detection report)."""
    from app.knowledge_sync.web_engine import WebsiteSyncEngine

    return WebsiteSyncEngine(db).scan_duplicates()


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


# ---------------------------------------------------------------------------
# Email health (Super Admin only). Never exposes credentials or secrets.
# ---------------------------------------------------------------------------

@router.get("/api/admin/email/health")
def email_health(current: User = Depends(require_superadmin)):
    """Super-Admin email health check: configuration + live SMTP connectivity.

    Returns provider/port/TLS booleans and a connection probe result only —
    no usernames, passwords or secrets of any kind.
    """
    import smtplib

    configured = bool(settings.EMAIL_ENABLED and settings.SMTP_HOST)
    connection = "not_configured"
    if configured:
        smtp = None
        try:
            smtp = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=5)
            smtp.ehlo()
            connection = "ok"
        except Exception:
            connection = "unreachable"
        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except Exception:
                    pass
    return {
        "configured": configured,
        "provider": "smtp" if configured else "none",
        "smtp_host_configured": bool(settings.SMTP_HOST),
        "smtp_port": settings.SMTP_PORT,
        "starttls": bool(settings.SMTP_STARTTLS),
        "mail_from_configured": bool(settings.MAIL_FROM),
        "email_enabled": bool(settings.EMAIL_ENABLED),
        "connection": connection,
        "note": (
            "SMTP delivery is synchronous and best-effort: grievances never "
            "fail when mail does. See backend/.env EMAIL_* keys."
            if not configured else
            "SMTP probe accepted the connection; a full send is verified with "
            "POST /api/admin/email/test."
        ),
    }


class _TestEmailRequest(BaseModel):
    to_email: str


@router.post("/api/admin/email/test")
def email_test(
    body: _TestEmailRequest,
    current: User = Depends(require_superadmin),
):
    """Super-Admin test message to an explicitly supplied address.

    Validates the address format, reuses the production sender, and reports
    acceptance honestly (never "sent" unless the provider accepted it).
    """
    import re

    from app.utils.email import send_test_email

    to_email = (body.to_email or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", to_email):
        raise HTTPException(status_code=422, detail="A valid recipient email is required")
    if len(to_email) > 200:
        raise HTTPException(status_code=422, detail="Recipient email is too long")
    configured = bool(settings.EMAIL_ENABLED and settings.SMTP_HOST)
    accepted = send_test_email(to_email) if configured else False
    return {
        "configured": configured,
        "accepted": accepted,
        "recipient": to_email,
        "provider": "smtp" if configured else "none",
        "detail": (
            "Message accepted by the SMTP server."
            if accepted else
            "Email service is not configured (no message sent)."
            if not configured else
            "The SMTP server did not accept the message."
        ),
    }
