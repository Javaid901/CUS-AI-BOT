"""
backend/app/authority/routes.py

Admin CRUD API for Authority Management + public lookup endpoint.

Admin endpoints  (prefix: /api/admin/authorities):
  GET    /                        — list all authorities (with search/filter)
  GET    /departments             — list distinct department names
  GET    /{authority_id}          — get single authority
  POST   /                        — create authority
  PUT    /{authority_id}          — update authority
  DELETE /{authority_id}          — delete authority
  POST   /bulk-import             — bulk import from JSON array
  GET    /export                  — export all as CSV/JSON
  POST   /{authority_id}/toggle   — enable/disable authority

Public endpoint  (prefix: /api/authority):
  GET    /lookup?q=...            — find matching authority for a query
  GET    /departments             — list departments
  GET    /{authority_id}          — get authority contact card
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.auth.security import require_admin
from app.authority.matcher import find_authority, format_contact_card
from app.authority.repository import list_all as repo_list_all
from app.authority.schemas import AuthorityCreate, AuthorityUpdate
from app.authority.service import authority_service
from app.config import settings
from app.database import get_db
from app.models import User
from app.utils.logging import audit

router = APIRouter(prefix=f"{settings.API_PREFIX}/admin/authorities", tags=["authority"])
public_router = APIRouter(prefix=f"{settings.API_PREFIX}/authority", tags=["authority"])

_protected = Depends(require_admin)


# ---------------------------------------------------------------------------
# Admin CRUD
# ---------------------------------------------------------------------------


@router.get("")
def list_authorities(
    db: Session = Depends(get_db),
    current: User = _protected,
    query: str | None = Query(None, description="Search term"),
    department: str | None = Query(None, description="Filter by department"),
    active_only: bool = Query(False, description="Only active authorities"),
):
    if query or department:
        return authority_service.search(db, query=query, department=department)
    return repo_list_all(db, active_only=active_only)


@router.get("/departments")
def list_departments(
    db: Session = Depends(get_db),
    current: User = _protected,
):
    return {"departments": authority_service.list_departments(db)}


@router.get("/export")
def export_authorities(
    db: Session = Depends(get_db),
    current: User = _protected,
    fmt: str = Query("json", pattern="^(json|csv)$"),
):
    rows = authority_service.search(db)
    if fmt == "csv":
        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        if rows:
            writer.writerow(rows[0].keys())
            for r in rows:
                writer.writerow(str(v) if v else "" for v in r.values())
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=authorities.csv"})
    return rows


@router.post("/bulk-import", status_code=201)
def bulk_import(
    body: list[AuthorityCreate],
    db: Session = Depends(get_db),
    current: User = _protected,
    request: Request = None,
):
    rows = authority_service.bulk_create(db, body)
    audit(db, "authority.bulk_import", actor_id=str(current.id), actor_role=current.role, detail=f"Bulk imported {len(rows)} authorities", ip=request.client.host if request else None)
    return {"status": "imported", "count": len(rows)}


@router.post("", status_code=201)
def create_authority(
    body: AuthorityCreate,
    db: Session = Depends(get_db),
    current: User = _protected,
    request: Request = None,
):
    row = authority_service.create(db, body)
    audit(db, "authority.create", actor_id=str(current.id), actor_role=current.role, detail=f"Created {row['authority_name']}", ip=request.client.host if request else None)
    return row


@router.get("/{authority_id}")
def get_authority(
    authority_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    row = authority_service.get_db(db, authority_id)
    if not row:
        raise HTTPException(status_code=404, detail="Authority not found")
    return row


@router.put("/{authority_id}")
def update_authority(
    authority_id: str,
    body: AuthorityUpdate,
    db: Session = Depends(get_db),
    current: User = _protected,
    request: Request = None,
):
    row = authority_service.update(db, authority_id, body)
    if not row:
        raise HTTPException(status_code=404, detail="Authority not found")
    audit(db, "authority.update", actor_id=str(current.id), actor_role=current.role, detail=f"Updated {row['authority_name']}", ip=request.client.host if request else None)
    return row


@router.delete("/{authority_id}")
def delete_authority(
    authority_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
    request: Request = None,
):
    row = authority_service.get_db(db, authority_id)
    if not row:
        raise HTTPException(status_code=404, detail="Authority not found")
    name = row["authority_name"]
    authority_service.delete(db, authority_id)
    audit(db, "authority.delete", actor_id=str(current.id), actor_role=current.role, detail=f"Deleted {name}", ip=request.client.host if request else None)
    return {"status": "deleted", "id": authority_id}


@router.post("/{authority_id}/toggle")
def toggle_authority(
    authority_id: str,
    db: Session = Depends(get_db),
    current: User = _protected,
    request: Request = None,
):
    row = authority_service.get_db(db, authority_id)
    if not row:
        raise HTTPException(status_code=404, detail="Authority not found")
    updated = authority_service.update(db, authority_id, AuthorityUpdate(active=not row["active"]))
    audit(db, "authority.toggle", actor_id=str(current.id), actor_role=current.role, detail=f"{'Activated' if updated['active'] else 'Deactivated'} {row['authority_name']}", ip=request.client.host if request else None)
    return updated


# ---------------------------------------------------------------------------
# Public / Chatbot lookup
# ---------------------------------------------------------------------------


@public_router.get("/lookup")
def lookup_authority(
    q: str = Query(..., min_length=2, description="User query to match against authorities"),
):
    matches = find_authority(q)
    if not matches:
        return {"query": q, "authorities": [], "match_type": "none", "confidence": 0.0}
    cards = [format_contact_card(m) for m in matches]
    return {
        "query": q,
        "authorities": cards,
        "match_type": "keyword",
        "confidence": matches[0].get("_match_score", 0.0) if matches else 0.0,
    }


@public_router.get("/departments")
def public_departments(
    db: Session = Depends(get_db),
):
    return {"departments": authority_service.list_departments(db)}


@public_router.get("/{authority_id}")
def public_authority(
    authority_id: str,
    db: Session = Depends(get_db),
):
    row = authority_service.get_db(db, authority_id)
    if not row or not row.get("active"):
        raise HTTPException(status_code=404, detail="Authority not found")
    return format_contact_card(row)
