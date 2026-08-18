"""
backend/app/grievance/intake.py

PHASE 4 — Public grievance intake service (pre-login).

Functions:
  * generate_public_reference / new_tracking_token / hash_tracking_token —
    reference + one-time tracking token material.
  * recommend_authorities — pick the best ACTIVE authority for a complaint.
  * create_submission — persist a pre-login grievance (draft -> submitted),
    attach immutable history, trigger best-effort email.
  * verify_submission — status check gated by BOTH reference AND token.

Security contract:
  * The tracking token is stored ONLY as a SHA-256 digest. The plaintext is
    returned exactly once (at submission) and never persisted or emailed.
  * verify_submission returns a status-only payload — student PII is never
    exposed, and a wrong/missing token fails closed (caller maps to 403).
  * Only ACTIVE authorities are eligible for routing; an inactive or unknown
    authority id in a submission is rejected before anything is written.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.authority.matcher import find_authority
from app.authority.repository import get_by_id as repo_get_by_id
from app.authority.service import authority_service
from app.config import settings
from app.grievance.models import Grievance
from app.grievance.notifications import record_submission_deliveries
from app.grievance.service import record_status_change
from app.utils.email import (
    send_grievance_acknowledgement,
    send_grievance_to_authority,
)
from app.utils.logging import audit

_log = logging.getLogger("cus_ai")

REFERENCE_PREFIX = "CUS-GRV"
_REFERENCE_SUFFIX_LEN = 8
_MAX_REFERENCE_RETRIES = 10
MIN_FINAL_TEXT = 10


# ---------------------------------------------------------------------------
# Reference / token material
# ---------------------------------------------------------------------------


def generate_public_reference(now: datetime | None = None) -> str:
    """A collision-safe public reference: CUS-GRV-<year>-<8 hex chars>."""
    year = (now or datetime.now(timezone.utc)).year
    suffix = secrets.token_hex(_REFERENCE_SUFFIX_LEN // 2).upper()
    return f"{REFERENCE_PREFIX}-{year}-{suffix}"


def new_tracking_token() -> str:
    """One-time status token; returned to the student once, never stored raw."""
    return secrets.token_urlsafe(24)


def token_for_request_id(request_id: str) -> str:
    """Deterministic tracking token for an idempotency key.

    The same request id always maps to the same token, so a retry can re-deliver
    the original receipt (and token) without ever persisting the plaintext. An
    exact-value token is derived from the server SECRET_KEY; a key rotation
    makes re-delivery impossible, in which case callers return the receipt
    without the token rather than inventing a new one.
    """
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        request_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:32]


def hash_tracking_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Authority recommendation (active authorities only)
# ---------------------------------------------------------------------------

_RECOMMEND_MIN_SCORE = 0.55


def recommend_authorities(db: Session, text: str, top_k: int = 3) -> list[dict[str, Any]]:
    """Best-fit active authorities for a grievance text.

    Returns up to top_k entries with stable keys
    (authority_id, authority_name, department_name, email, match_score).
    Empty list when nothing clears the threshold.
    """
    matches = find_authority(text, top_k=top_k)
    out: list[dict[str, Any]] = []
    for m in matches:
        score = float(m.get("_match_score", 0.0) or 0.0)
        if score < _RECOMMEND_MIN_SCORE:
            continue
        out.append({
            "authority_id": m.get("id", ""),
            "authority_name": m.get("authority_name", ""),
            "department_name": m.get("department_name", ""),
            "email": m.get("email", ""),
            "match_score": round(score, 2),
        })
    return out


def is_active_authority(authority_id: str) -> bool:
    row = authority_service.get(authority_id)
    return bool(row)


def authority_summary(authority_id: str, db: Session | None = None) -> dict[str, Any]:
    """Resolve the display fields for an authority (sparse, no internals).

    Active authorities resolve from the in-memory cache. When an authority has
    been deactivated or soft-deleted (historical grievance display), the cache
    no longer contains it, so the original record is resolved from the
    database — historical Grievance records keep showing the authority they
    were originally submitted to.
    """
    row = authority_service.get(authority_id)
    if not row and db is not None:
        row = repo_get_by_id(db, authority_id, include_deleted=True)
    if not row:
        return {}
    return {
        "authority_id": row.get("id", ""),
        "authority_name": row.get("authority_name", ""),
        "department_name": row.get("department_name", ""),
        "email": row.get("email", ""),
    }


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def _receipt(
    db: Session,
    grievance: Grievance,
    *,
    token: str | None,
    deduplicated: bool = False,
) -> dict[str, Any]:
    return {
        "reference": grievance.reference,
        "tracking_token": token,
        "status": grievance.status,
        "category": grievance.category,
        "email_confirmed": grievance.email_status == "sent",
        "authority_email_status": grievance.authority_email_status,
        "authority": authority_summary(grievance.authority_id, db=db) if grievance.authority_id else None,
        "submitted_at": grievance.submitted_at,
        "deduplicated": deduplicated,
    }


def _receipt_for_existing(db: Session, grievance: Grievance, request_id: str) -> dict[str, Any]:
    """Replay the original receipt for an idempotent retry.

    The plaintext token is never stored; it is re-derived from the request id
    (deterministic). When the derivation no longer matches the stored digest
    (e.g. the server key rotated between the original request and the retry)
    the receipt is returned without the token — the grievance is never lost and
    no second row is ever created.
    """
    token: str | None = token_for_request_id(request_id)
    if not grievance.tracking_token_hash or not hmac.compare_digest(
        grievance.tracking_token_hash, hash_tracking_token(token)
    ):
        token = None
    return _receipt(db, grievance, token=token, deduplicated=True)


def submit_grievance(
    db: Session,
    *,
    student_name: str,
    student_email: str,
    roll_number: str | None,
    semester: str | None,
    college: str | None,
    programme: str | None,
    phone: str | None,
    original_input: str | None,
    final_text: str,
    category: str | None,
    authority_id: str | None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create and submit a pre-login grievance. Returns the submission receipt.

    When `idempotency_key` is supplied and a grievance already exists for it,
    the ORIGINAL receipt is returned (deduplicated=True) instead of creating a
    second record — double-clicks, browser and network retries stay safe.
    """
    final_text = (final_text or "").strip()
    if len(final_text) < MIN_FINAL_TEXT:
        raise ValueError("final_text must be at least 10 characters")

    if authority_id:
        if not is_active_authority(authority_id):
            raise ValueError("selected authority is not available for routing")

    request_id = (idempotency_key or "").strip() or None
    if request_id:
        existing = (
            db.query(Grievance)
            .filter(Grievance.client_request_id == request_id)
            .first()
        )
        if existing:
            return _receipt_for_existing(db, existing, request_id)

    ref = ""
    for _ in range(_MAX_REFERENCE_RETRIES):
        ref = generate_public_reference()
        existing = db.query(Grievance).filter(Grievance.reference == ref).first()
        if not existing:
            break
    else:
        raise RuntimeError("could not allocate a unique grievance reference")

    token = token_for_request_id(request_id) if request_id else new_tracking_token()
    grievance = Grievance(
        reference=ref,
        authority_id=authority_id or None,
        student_name=(student_name or "").strip()[:200] or None,
        roll_number=(roll_number or "").strip()[:50] or None,
        semester=(semester or "").strip()[:20] or None,
        college=(college or "").strip()[:200] or None,
        student_email=(student_email or "").strip()[:200] or None,
        programme=(programme or "").strip()[:50] or None,
        phone=(phone or "").strip()[:30] or None,
        source_kind="pre_login",
        category=(category or "").strip()[:100] or None,
        original_student_input=(original_input or "").strip()[:100000] or None,
        final_grievance_text=final_text,
        tracking_token_hash=hash_tracking_token(token),
        client_request_id=request_id,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(grievance)
    db.flush()

    record_status_change(
        db,
        grievance,
        new_status="submitted",
        changed_by="system:pre_login_submission",
        changed_by_role="system",
        comment="Submitted through the public intake form (source: pre_login).",
        is_internal=True,
    )

    # Persist the grievance (and its immutable history) FIRST: the record must
    # exist regardless of what happens to the acknowledgement email.
    try:
        db.commit()
    except IntegrityError:
        # Race: two retries with the same key passed the pre-check together.
        # The winner's row stands; this one replays the winner's receipt.
        db.rollback()
        if request_id:
            winner = (
                db.query(Grievance)
                .filter(Grievance.client_request_id == request_id)
                .first()
            )
            if winner:
                return _receipt_for_existing(db, winner, request_id)
        raise

    db.refresh(grievance)

    # Best-effort emails; NEVER block the submission. The grievance is already
    # committed — delivery state is recorded honestly per destination.
    if student_email:
        sent = send_grievance_acknowledgement(
            student_email, ref,
            subject=(category or "Grievance submission"),
            authority_name=authority_summary(authority_id, db=db).get("authority_name") if authority_id else None,
            student_first_name=(student_name or "").strip().split(" ")[0] or None,
            submitted_on=grievance.submitted_at,
        )
        grievance.email_status = "sent" if sent else "failed"
        audit(
            db, "grievance.email_sent" if sent else "grievance.email_failed",
            actor_id=None, actor_role="system",
            target=ref, detail=f"Submission acknowledgement to student ({'sent' if sent else 'failed'})",
        )

    authority_send_state: str | None = "unavailable"
    authority_email: str | None = None
    if authority_id:
        auth = authority_summary(authority_id, db=db)
        authority_email = (auth.get("email") or "").strip() or None
        if authority_email:
            authority_send_state = (
                "sent"
                if send_grievance_to_authority(
                    authority_email,
                    ref,
                    category or "Grievance",
                    final_text,
                    {
                        "name": student_name,
                        "roll_number": roll_number,
                        "college": college,
                        "semester": semester,
                        "email": student_email,
                    },
                    auth.get("authority_name") or "Authority",
                    grievance.submitted_at,
                )
                else "failed"
            )
    grievance.authority_email_status = authority_send_state
    if authority_id and authority_send_state in ("sent", "failed"):
        audit(
            db, "grievance.email_sent" if authority_send_state == "sent" else "grievance.email_failed",
            actor_id=None, actor_role="system",
            target=ref, detail=f"New-grievance notification to authority ({authority_send_state})",
        )

    # Delivery ledger (passive recorder — no email is sent here; the rows above
    # are the existing sends, this only logs their outcome for retry/recovery).
    record_submission_deliveries(
        db, grievance,
        student_email=student_email,
        student_status=grievance.email_status,
        authority_email=authority_email,
        authority_status=None if not authority_id else authority_send_state,
    )
    db.commit()
    db.refresh(grievance)

    return _receipt(db, grievance, token=token)


# ---------------------------------------------------------------------------
# Verification (token-gated, PII-free)
# ---------------------------------------------------------------------------


def verify_submission(db: Session, reference: str, token: str) -> dict[str, Any] | None:
    """Return the PII-free status payload for a submission.

    Requires BOTH the reference and the one-time tracking token. Returns None
    when the reference is unknown OR the token does not match — the caller
    surfaces an indistinguishable failure so reference existence is not leaked.
    """
    reference = (reference or "").strip().upper()
    token = (token or "").strip()
    if not reference or not token:
        return None

    grievance = (
        db.query(Grievance).filter(Grievance.reference == reference).first()
    )
    if not grievance:
        return None

    expected = grievance.tracking_token_hash
    if not expected or not secrets.compare_digest(expected, hash_tracking_token(token)):
        return None

    auth = {}
    if grievance.authority_id:
        auth = authority_summary(grievance.authority_id, db=db)

    return {
        "reference": grievance.reference,
        "status": grievance.status,
        "category": grievance.category,
        "subject": grievance.category or "Grievance",
        "submitted_at": grievance.submitted_at,
        "authority_name": auth.get("authority_name") or "Pending assignment",
        "department_name": auth.get("department_name") or "",
    }


__all__ = [
    "REFERENCE_PREFIX",
    "authority_summary",
    "generate_public_reference",
    "hash_tracking_token",
    "is_active_authority",
    "new_tracking_token",
    "recommend_authorities",
    "submit_grievance",
    "token_for_request_id",
    "verify_submission",
]