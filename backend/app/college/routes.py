"""College Intelligence API endpoints — fast structured data for the chatbot."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.security import require_admin
from app.college.service import CollegeService
from app.config import settings
from app.models import User

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
