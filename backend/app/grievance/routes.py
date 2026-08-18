"""
backend/app/grievance/routes.py

PHASE 4 — Public (pre-login) grievance intake API.

Endpoints (all unauthenticated by design — the intake exists BEFORE login;
every endpoint is per-IP rate limited):
  POST  /api/grievances/draft/generate   AI formalization draft
  POST  /api/grievances/recommend        authority recommendation
  GET   /api/grievances/categories       category choices for the composer
  POST  /api/grievances                  submit (create) a grievance
  GET   /api/grievances/{reference}/verify?token=  status check (token-gated)

Security notes:
  * Submission requires a valid email and a real grievance text (>= 10 chars).
  * The selected authority MUST be currently active; inactive/unknown ids are
    rejected outright (never routed to a stale office).
  * Verify returns a PII-free status payload; wrong/missing token and unknown
    reference both fail identically via 403/404 without leaking anything.
  * tracking_token is returned ONCE, at submission time, never logged.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.grievance.detect import CATEGORY_ORDER
from app.grievance.intake import (
    recommend_authorities,
    submit_grievance,
    verify_submission,
)
from app.grievance.llm import formalize
from app.utils.logging import audit
from app.utils.rate_limit import endpoint_rate_limit

router = APIRouter(prefix="/api/grievances", tags=["grievances"])

_log = logging.getLogger("cus_ai")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class DraftRequest(BaseModel):
    input: str = Field(min_length=3, max_length=4000)


class DraftResponse(BaseModel):
    generated: bool
    subject: str
    text: str
    error: str | None = None
    manual: bool = False


class RecommendRequest(BaseModel):
    input: str = Field(min_length=3, max_length=4000)


class StudentInfo(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    roll_number: str | None = Field(default=None, max_length=50)
    semester: str | None = Field(default=None, max_length=20)
    college: str | None = Field(default=None, max_length=200)
    programme: str | None = Field(default=None, max_length=50)
    phone: str | None = Field(default=None, max_length=30)


class SubmitRequest(BaseModel):
    student: StudentInfo
    final_text: str = Field(min_length=10, max_length=100000)
    original_input: str | None = Field(default=None, max_length=100000)
    category: str | None = Field(default=None, max_length=100)
    authority_id: str | None = Field(default=None, max_length=36)
    # Client-generated key (e.g. crypto.randomUUID) making retries idempotent.
    idempotency_key: str | None = Field(
        default=None,
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class VerifyResponse(BaseModel):
    reference: str
    status: str
    category: str | None = None
    subject: str | None = None
    submitted_at: str | None = None
    authority_name: str | None = None
    department_name: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _try_submit(db: "Session", payload: SubmitRequest) -> dict:
    try:
        return submit_grievance(
            db,
            student_name=payload.student.name,
            student_email=payload.student.email,
            roll_number=payload.student.roll_number,
            semester=payload.student.semester,
            college=payload.student.college,
            programme=payload.student.programme,
            phone=payload.student.phone,
            original_input=payload.original_input,
            final_text=payload.final_text,
            category=payload.category,
            authority_id=payload.authority_id,
            idempotency_key=payload.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/draft/generate",
    response_model=DraftResponse,
    dependencies=[Depends(endpoint_rate_limit(settings.GRIEVANCE_GENERATE_LIMIT, "grievance-generate"))],
)
def generate_draft(payload: DraftRequest):
    """AI-formalized draft of the student's complaint (editable by the student)."""
    result = formalize(payload.input)
    return DraftResponse(
        generated=result["generated"],
        subject=result["subject"],
        text=result["text"],
        error=result.get("error"),
        manual=result.get("manual", False),
    )


@router.get("/categories")
def list_categories():
    """Suggested categories for the composer dropdown."""
    return {"categories": CATEGORY_ORDER}


@router.post(
    "/recommend",
    dependencies=[Depends(endpoint_rate_limit(settings.GRIEVANCE_RECOMMEND_LIMIT, "grievance-recommend"))],
)
def recommend(payload: RecommendRequest, db: Session = Depends(get_db)):
    """Recommend the best ACTIVE authority for a grievance text."""
    text = payload.input if payload else ""
    matches = recommend_authorities(db, text, top_k=3)
    return {"authority": matches[0] if matches else None, "alternatives": matches[1:]}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(endpoint_rate_limit(settings.GRIEVANCE_CREATE_LIMIT, "grievance-create"))],
)
def create_grievance(
    payload: SubmitRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Submit a grievance without an account. Returns the one-time receipt."""
    if not EMAIL_RE.match(payload.student.email):
        raise HTTPException(status_code=422, detail="A valid email address is required.")

    receipt = _try_submit(db, payload)
    if not receipt.get("deduplicated"):
        # Retries replayed from the idempotency key are not new events.
        audit(
            db, "grievance",
            actor_id=None,
            actor_role="guest",
            detail=(
                f"Grievance submitted (public intake): {receipt['reference']} "
                f"| student_email={receipt.get('email_confirmed')} "
                f"| authority_email={receipt.get('authority_email_status')}"
            ),
            ip=request.client.host if request.client else None,
        )
        # Fresh submissions don't need the internal dedupe flag; replayed
        # submissions keep it so the client can tell the user "already seen".
        receipt.pop("deduplicated", None)
    # Timestamp serialization for the receipt
    receipt["submitted_at"] = receipt["submitted_at"].isoformat() if receipt.get("submitted_at") else None
    return receipt


@router.get(
    "/{reference}/verify",
    dependencies=[Depends(endpoint_rate_limit(settings.GRIEVANCE_VERIFY_LIMIT, "grievance-verify"))],
)
def verify_grievance(
    reference: str,
    token: str = Query(..., min_length=8, max_length=200),
    db: Session = Depends(get_db),
):
    """PII-free status check. Requires BOTH the reference and tracking token."""
    result = verify_submission(db, reference, token)
    if result is None:
        # Indistinguishable failure: never leak whether a reference exists.
        raise HTTPException(status_code=403, detail="Invalid reference or tracking token.")
    return result


__all__ = ["router"]