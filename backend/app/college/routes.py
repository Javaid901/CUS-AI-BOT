"""College Intelligence API endpoints — fast structured data for the chatbot."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.auth.security import require_admin
from app.college.service import CollegeService
from app.config import settings
from app.database import get_db
from app.models import User
from app.utils.files import validate_upload
from app.utils.logging import audit
from sqlalchemy.orm import Session

router = APIRouter(prefix=f"{settings.API_PREFIX}/college", tags=["college"])
_protected = Depends(require_admin)

svc = CollegeService()


@router.get("/list")
def list_colleges():
    """Return a list of all constituent colleges."""
    return svc.list_all()


@router.get("/{college_id}")
def get_college(college_id: str):
    """Return full details for a specific college."""
    college = svc.get_college(college_id)
    if not college:
        raise HTTPException(status_code=404, detail="College not found")
    return college


@router.get("/{college_id}/overview")
def college_overview(college_id: str):
    """Return overview for a college."""
    overview = svc.get_overview(college_id)
    if not overview:
        raise HTTPException(status_code=404, detail="College not found")
    return overview


@router.get("/{college_id}/departments")
def college_departments(college_id: str):
    """Return departments for a college."""
    depts = svc.get_departments(college_id)
    if depts is None:
        raise HTTPException(status_code=404, detail="College not found")
    return {"college_id": college_id, "departments": depts}


@router.get("/{college_id}/programmes")
def college_programmes(college_id: str):
    """Return programmes offered by a college."""
    progs = svc.get_programmes(college_id)
    if progs is None:
        raise HTTPException(status_code=404, detail="College not found")
    return {"college_id": college_id, "programmes": progs}


@router.get("/{college_id}/fees")
def college_fees(college_id: str, programme: str | None = Query(None)):
    """Return fee structure for a college, optionally for a specific programme."""
    fees = svc.get_fees(college_id, programme)
    if fees is None:
        raise HTTPException(status_code=404, detail="College not found")
    return {"college_id": college_id, "fees": fees}


@router.get("/{college_id}/facilities")
def college_facilities(college_id: str):
    """Return facilities for a college."""
    facilities = svc.get_facilities(college_id)
    if facilities is None:
        raise HTTPException(status_code=404, detail="College not found")
    return {"college_id": college_id, "facilities": facilities}


@router.get("/{college_id}/contact")
def college_contact(college_id: str):
    """Return contact info for a college."""
    contact = svc.get_contact(college_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="College not found")
    return {"college_id": college_id, "contact": contact}


@router.get("/{college_id}/eligibility")
def college_eligibility(college_id: str, level: str | None = Query(None)):
    """Return eligibility criteria for a college."""
    el = svc.get_eligibility(college_id, level)
    if el is None:
        raise HTTPException(status_code=404, detail="College not found")
    return {"college_id": college_id, "eligibility": el}


@router.get("/search/{query}")
def search_colleges(query: str):
    """Search colleges by name or district."""
    return svc.search(query)


# Admin endpoints for college management
@router.post("/admin/{college_id}")
def update_college(
    college_id: str,
    current: User = _protected,
):
    """Placeholder: update college data (future: DB-backed)."""
    college = svc.get_college(college_id)
    if not college:
        raise HTTPException(status_code=404, detail="College not found")
    return {"status": "ok", "message": "College data is currently file-based. DB-backed management coming soon."}


# ---------------------------------------------------------------------------
# College knowledge base management (admin)
# ---------------------------------------------------------------------------


class ManualSourceIn(BaseModel):
    title: str
    content: str
    document_type: str | None = None
    category: str | None = None


class UrlSourceIn(BaseModel):
    url: str
    title: str | None = None
    category: str | None = None


def _require_college(college_id: str) -> dict:
    college = svc.get_college(college_id)
    if not college:
        raise HTTPException(status_code=404, detail="College not found")
    return college


@router.get("/{college_id}/knowledge/status")
def college_knowledge_status(
    college_id: str,
    db: Session = Depends(get_db),
):
    """Public knowledge of a college's knowledge base (counts only)."""
    _require_college(college_id)
    from app.college.knowledge import summarize_college

    return summarize_college(db, college_id)


@router.get("/admin/{college_id}/knowledge")
async def college_knowledge_admin(
    college_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Admin: list sources of a college knowledge base (auto-seeds digest)."""
    _require_college(college_id)
    from app.college.knowledge import ensure_backfill, list_sources, summarize_college

    try:
        backfill = await ensure_backfill(db, college_id)
    except Exception as exc:
        backfill = {"created": False, "reason": str(exc)[:200]}
    return {
        "college_id": college_id,
        "college_name": svc.get_college(college_id)["name"],
        "backfill": backfill,
        "summary": summarize_college(db, college_id),
        "sources": list_sources(db, college_id),
    }


@router.post("/admin/{college_id}/knowledge/upload")
async def admin_knowledge_upload(
    college_id: str,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    document_type: str | None = Form(None),
    category: str | None = Form(None),
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Admin: upload a document into a college knowledge base."""
    _require_college(college_id)
    from app.college.knowledge import submit_upload

    data = await file.read()
    try:
        validate_upload(file.filename or "file", len(data))
    except HTTPException as exc:
        audit(db, "college_knowledge_upload_rejected", actor_id=str(current.id),
              actor_role=current.role, target=college_id, detail=exc.detail)
        raise
    try:
        result = await submit_upload(
            db, current.id, college_id, file.filename, data,
            title=title, document_type=document_type, category=category,
        )
    except Exception as exc:
        audit(db, "college_knowledge_upload_failed", actor_id=str(current.id),
              actor_role=current.role, target=college_id, detail=str(exc)[:300])
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")
    audit(db, "college_knowledge_upload", actor_id=str(current.id),
          actor_role=current.role, target=college_id, detail=file.filename)
    return result


@router.post("/admin/{college_id}/knowledge/manual")
async def admin_knowledge_manual(
    college_id: str,
    payload: ManualSourceIn,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Admin: add a manually typed knowledge entry."""
    _require_college(college_id)
    from app.college.knowledge import submit_manual

    try:
        result = await submit_manual(
            db, current.id, college_id, payload.title, payload.content,
            document_type=payload.document_type, category=payload.category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit(db, "college_knowledge_manual", actor_id=str(current.id),
          actor_role=current.role, target=college_id, detail=payload.title[:200])
    return result


@router.post("/admin/{college_id}/knowledge/url")
async def admin_knowledge_url(
    college_id: str,
    payload: UrlSourceIn,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Admin: add a knowledge entry fetched from a public URL."""
    _require_college(college_id)
    from app.college.knowledge import submit_url

    try:
        result = await submit_url(
            db, current.id, college_id, payload.url,
            title=payload.title, category=payload.category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit(db, "college_knowledge_url", actor_id=str(current.id),
          actor_role=current.role, target=college_id, detail=payload.url[:300])
    return result


def _get_college_source(db: Session, source_id: str):
    from app.college.knowledge import get_source

    doc = get_source(db, source_id)
    if doc is None or doc.scope != "college":
        raise HTTPException(status_code=404, detail="Knowledge source not found")
    return doc


@router.post("/admin/knowledge/{source_id}/reindex")
async def admin_knowledge_reindex(
    source_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Admin: re-index a college knowledge source from its stored file."""
    _get_college_source(db, source_id)
    from app.college.knowledge import reindex_source

    try:
        result = await reindex_source(db, current.id, source_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    audit(db, "college_knowledge_reindex", actor_id=str(current.id),
          actor_role=current.role, target=source_id)
    return result


@router.post("/admin/knowledge/{source_id}/archive")
def admin_knowledge_archive(
    source_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Admin: archive a source (kept, but no longer retrieved)."""
    _get_college_source(db, source_id)
    from app.college.knowledge import archive_source

    audit(db, "college_knowledge_archive", actor_id=str(current.id),
          actor_role=current.role, target=source_id)
    return archive_source(db, source_id)


@router.post("/admin/knowledge/{source_id}/restore")
async def admin_knowledge_restore(
    source_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Admin: restore an archived source and re-index it."""
    _get_college_source(db, source_id)
    from app.college.knowledge import restore_source

    audit(db, "college_knowledge_restore", actor_id=str(current.id),
          actor_role=current.role, target=source_id)
    return await restore_source(db, source_id)


@router.delete("/admin/knowledge/{source_id}")
def admin_knowledge_delete(
    source_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    """Admin: permanently delete a college knowledge source."""
    _get_college_source(db, source_id)
    from app.college.knowledge import delete_source

    audit(db, "college_knowledge_delete", actor_id=str(current.id),
          actor_role=current.role, target=source_id)
    return delete_source(db, source_id)
