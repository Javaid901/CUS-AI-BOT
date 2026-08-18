"""
backend/app/authority_admin/routes.py

API surface for Authority Admin accounts (PHASE 3 + PHASE 6 portal).

Super Admin (management)  prefix: /api/admin/authority-admins
  GET    /                  list accounts (search + active/inactive filter)
  POST   /                  create account (never returns the password)
  GET    /{user_id}         single account
  PATCH  /{user_id}         update profile fields / email
  POST   /{user_id}/toggle   activate / deactivate
  POST   /{user_id}/assign   change assigned authority (scoped, guarded)

Authority Admin (self-service portal)  prefix: /api/authority-admin
  GET    /me                 own account + assigned authority
  GET    /profile            same profile payload (alias)
  PUT    /profile            update OWN profile (full_name/designation/phone)
  PUT    /password           change OWN password (current verified first)
  GET    /dashboard          authority-scoped stats + recent grievances
  GET    /grievances         paginated list, backend-enforced search+filters
  GET    /grievances/{id}    detail (+ history); opening auto-marks READ once
  POST   /grievances/{id}/read     explicit mark read (idempotent)
  POST   /grievances/{id}/unread   explicit mark unread (idempotent)
  POST   /grievances/{id}/status   workflow transition (immutable history)
  POST   /grievances/{id}/response official response -> email to the student

Scope: every /api/authority-admin endpoint derives the authority from
users.authority_id. A grievance that does not belong to the admin's authority
is indistinguishable from a missing one (404) — cross-authority access is
impossible even with forged ids, query params or bodies.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.auth.security import require_authority_admin, require_superadmin
from app.authority.repository import get_by_id as auth_get_by_id
from app.authority_admin import portal
from app.authority_admin.schemas import (
    AuthorityAdminAssign,
    AuthorityAdminCreate,
    AuthorityAdminUpdate,
    PortalPasswordChange,
    PortalProfileUpdate,
    PortalResponseCreate,
    PortalStatusChange,
)
from app.authority_admin.service import (
    assign_authority,
    change_own_password,
    create,
    get_row,
    list_rows,
    self_scope,
    toggle_active,
    update_own_profile,
    update_row,
)
from app.config import settings
from app.database import get_db
from app.models import User
from app.utils.logging import audit

router = APIRouter(prefix=f"{settings.API_PREFIX}/admin/authority-admins", tags=["authority-admin"])
self_router = APIRouter(prefix=f"{settings.API_PREFIX}/authority-admin", tags=["authority-admin"])

_superadmin = Depends(require_superadmin)
_authority_admin = Depends(require_authority_admin)


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


# ---------------------------------------------------------------------------
# Super Admin management
# ---------------------------------------------------------------------------


@router.get("")
def list_authority_admins(
    db: Session = Depends(get_db),
    current: User = _superadmin,
    query: str | None = Query(None, description="Search by username / name / email"),
    status: str | None = Query(None, pattern="^(active|inactive)?$"),
    authority_id: str | None = Query(None, description="Filter by assigned authority"),
):
    return {"authority_admins": list_rows(db, query=query, status=status, authority_id=authority_id)}


@router.post("", status_code=201)
def create_authority_admin(
    body: AuthorityAdminCreate,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _superadmin,
):
    try:
        row = create(db, body)
    except ValueError as exc:
        raise _conflict(exc)
    audit(
        db, "authority_admin.create", actor_id=str(current.id), actor_role=current.role,
        target=row["username"], detail=f"Created Authority Admin for {row['authority_name']}",
        ip=request.client.host if request.client else None,
    )
    return row


@router.get("/{admin_id}")
def get_authority_admin(
    admin_id: str,
    db: Session = Depends(get_db),
    current: User = _superadmin,
):
    try:
        return get_row(db, admin_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/{admin_id}")
def update_authority_admin(
    admin_id: str,
    body: AuthorityAdminUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _superadmin,
):
    try:
        row = update_row(db, admin_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=409 if "registered" in str(exc) else 404, detail=str(exc))
    audit(
        db, "authority_admin.update", actor_id=str(current.id), actor_role=current.role,
        target=row["username"],
        ip=request.client.host if request.client else None,
    )
    return row


@router.post("/{admin_id}/toggle")
def toggle_authority_admin(
    admin_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _superadmin,
):
    try:
        row = toggle_active(db, admin_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    audit(
        db, "authority_admin.toggle", actor_id=str(current.id), actor_role=current.role,
        target=row["username"], detail=f"{'Activated' if row['is_active'] else 'Deactivated'} {row['username']}",
        ip=request.client.host if request.client else None,
    )
    return row


@router.post("/{admin_id}/assign")
def assign_authority_admin(
    admin_id: str,
    body: AuthorityAdminAssign,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _superadmin,
):
    try:
        result = assign_authority(db, admin_id, body)
    except ValueError as exc:
        if "not found" in str(exc) or "does not exist" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(status_code=409, detail=str(exc))
    row = result["user"]
    audit(
        db, "authority_admin.assign", actor_id=str(current.id), actor_role=current.role,
        target=row["username"],
        detail=f"Authority assignment changed: {result['previous_authority_id'] or 'none'} -> {row['authority_id']}",
        ip=request.client.host if request.client else None,
    )
    return row


# ---------------------------------------------------------------------------
# Authority Admin self-service (Phase 3 foundation + Phase 6 portal)
# ---------------------------------------------------------------------------


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _authority_or_404(db: Session, current: User) -> dict:
    """The admin's authority, or 404 if the account has none assigned."""
    auth = auth_get_by_id(db, str(current.authority_id)) if current.authority_id else None
    if not auth:
        raise HTTPException(status_code=404, detail="No authority is assigned to this account")
    return auth


def _grievance_or_404(db: Session, current: User, grievance_id: str):
    """Scope-safe fetch: other authorities' grievances look like 404s."""
    try:
        scope = portal.scope_authority_id(current)
    except ValueError:
        raise HTTPException(status_code=404, detail="No authority is assigned to this account")
    grievance = portal.get_grievance(db, scope, grievance_id)
    if not grievance:
        raise HTTPException(status_code=404, detail="Grievance not found")
    return grievance


def _parse_date(value: str | None, which: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid {which}: expected ISO date")


@self_router.get("/me")
def authority_admin_me(
    db: Session = Depends(get_db),
    current: User = _authority_admin,
):
    """Authenticated Authority Admin account + assigned authority details.

    The authority scope is derived from users.authority_id (server-side); the
    client cannot influence which authority is returned.
    """
    return self_scope(db, current)


@self_router.get("/profile")
def authority_admin_profile(
    db: Session = Depends(get_db),
    current: User = _authority_admin,
):
    return self_scope(db, current)


@self_router.put("/profile")
def update_authority_admin_profile(
    body: PortalProfileUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _authority_admin,
):
    updated = update_own_profile(db, current, body)
    audit(
        db, "authority_admin.profile_update", actor_id=str(current.id),
        actor_role=current.role, target=current.username, ip=_ip(request),
    )
    return updated


@self_router.put("/password")
def change_authority_admin_password(
    body: PortalPasswordChange,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _authority_admin,
):
    try:
        change_own_password(db, current, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit(
        db, "authority_admin.password_change", actor_id=str(current.id),
        actor_role=current.role, target=current.username, ip=_ip(request),
    )
    return {"ok": True}


@self_router.get("/dashboard")
def authority_admin_dashboard(
    db: Session = Depends(get_db),
    current: User = _authority_admin,
):
    auth = _authority_or_404(db, current)
    stats = portal.dashboard_stats(db, portal.scope_authority_id(current))
    stats["authority"] = {
        "authority_id": auth.get("id"),
        "authority_name": auth.get("authority_name"),
        "department_name": auth.get("department_name"),
        "category_name": auth.get("category_name"),
        "email": auth.get("email"),
    }
    stats["admin"] = {"username": current.username, "full_name": current.full_name}
    return stats


@self_router.get("/grievances")
def authority_admin_grievances(
    db: Session = Depends(get_db),
    current: User = _authority_admin,
    page: int = Query(1, ge=1),
    page_size: int = Query(portal.DEFAULT_PAGE_SIZE, ge=1, le=portal.MAX_PAGE_SIZE),
    q: str | None = Query(None, max_length=200, description="search reference / student / roll / email / subject"),
    status: str | None = Query(None, description="workflow status filter"),
    read: str | None = Query(None, pattern="^(read|unread)$", description="read-state filter"),
    date_from: str | None = Query(None, description="ISO date, inclusive"),
    date_to: str | None = Query(None, description="ISO date, inclusive"),
):
    scope = portal.scope_authority_id(current)
    if status and status not in portal.PORTAL_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status (allowed: {', '.join(portal.PORTAL_STATUSES)})",
        )
    return portal.list_grievances(
        db,
        scope,
        page=page,
        page_size=page_size,
        q=q,
        status=status,
        read_state=read,
        date_from=_parse_date(date_from, "date_from"),
        date_to=_parse_date(date_to, "date_to"),
    )


@self_router.get("/grievances/{grievance_id}")
def authority_admin_grievance_detail(
    grievance_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _authority_admin,
):
    grievance = _grievance_or_404(db, current, grievance_id)
    return portal.open_grievance(db, grievance, current, ip=_ip(request))


@self_router.post("/grievances/{grievance_id}/read")
def authority_admin_grievance_read(
    grievance_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _authority_admin,
):
    grievance = _grievance_or_404(db, current, grievance_id)
    return portal.mark_read(db, grievance, current, ip=_ip(request))


@self_router.post("/grievances/{grievance_id}/unread")
def authority_admin_grievance_unread(
    grievance_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _authority_admin,
):
    grievance = _grievance_or_404(db, current, grievance_id)
    return portal.mark_unread(db, grievance, current, ip=_ip(request))


@self_router.post("/grievances/{grievance_id}/status")
def authority_admin_grievance_status(
    grievance_id: str,
    body: PortalStatusChange,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _authority_admin,
):
    grievance = _grievance_or_404(db, current, grievance_id)
    try:
        return portal.change_status(db, grievance, body.new_status, current, note=body.note, ip=_ip(request))
    except ValueError as exc:
        message = str(exc)
        if "already" in message:
            raise HTTPException(status_code=409, detail=message)
        raise HTTPException(status_code=422, detail=message)


@self_router.post("/grievances/{grievance_id}/response")
def authority_admin_grievance_response(
    grievance_id: str,
    body: PortalResponseCreate,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _authority_admin,
):
    grievance = _grievance_or_404(db, current, grievance_id)
    auth = auth_get_by_id(db, str(current.authority_id)) if current.authority_id else None
    auth_name = (auth or {}).get("authority_name") or "Authority"
    try:
        return portal.add_response(db, grievance, body.response, current, auth_name, ip=_ip(request))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))