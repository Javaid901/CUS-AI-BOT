"""
backend/app/authority_admin/service.py

Business logic for Authority Admin account management.

Enforces the Phase-3 rules:
  * role is always authority_admin
  * duplicate username / duplicate email rejected (case-insensitive email)
  * authority must exist and be ACTIVE (an inactive authority can never be
    assigned to a new account — and a Supervisor reassignment to an inactive
    authority is refused too)
  * passwords are hashed immediately and never returned to any caller
  * scope (users.authority_id) is derived/stored server-side only
"""

from __future__ import annotations

import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.authority.repository import get_by_id as auth_get_by_id
from app.authority_admin.schemas import (
    AuthorityAdminAssign,
    AuthorityAdminCreate,
    AuthorityAdminUpdate,
    PortalPasswordChange,
    PortalProfileUpdate,
)
from app.models import User

ROLE = "authority_admin"


def _authority_or_raise(db: Session, authority_id: str) -> dict:
    """Return the authority or raise; refuses inactive authorities."""
    auth = auth_get_by_id(db, authority_id)
    if not auth:
        raise ValueError("Authority does not exist")
    if not auth.get("active", True):
        raise ValueError("Cannot assign an inactive authority to an account")
    return auth


def create(db: Session, body: AuthorityAdminCreate) -> dict:
    """Create an Authority Admin account (duplicate- and authority-guarded)."""
    if db.query(User).filter(func.lower(User.username) == body.username.strip().lower()).first():
        raise ValueError("Username is already taken")
    if db.query(User).filter(func.lower(User.email) == body.email.lower()).first():
        raise ValueError("Email is already registered")
    authority = _authority_or_raise(db, body.authority_id)

    user = User(
        id=uuid.uuid4(),
        username=body.username.strip(),
        email=body.email,
        hashed_password=hash_password(body.password),
        role=ROLE,
        is_active=body.is_active,
        full_name=body.full_name,
        designation=body.designation,
        authority_id=str(authority["id"]),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_view(db, user)


def _user_or_raise(db: Session, user_id: str) -> User:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        uid = None
    user = db.query(User).filter(User.id == uid).first() if uid else None
    if not user or user.role != ROLE:
        raise ValueError("Authority Admin account not found")
    return user


def update_row(db: Session, user_id: str, body: AuthorityAdminUpdate) -> dict:
    """Update account profile fields (never the scope or role here)."""
    user = _user_or_raise(db, user_id)
    if body.email is not None and body.email != user.email:
        clash = (
            db.query(User)
            .filter(func.lower(User.email) == body.email.lower(), User.id != user.id)
            .first()
        )
        if clash:
            raise ValueError("Email is already registered")
        user.email = body.email
    if body.full_name is not None:
        user.full_name = body.full_name.strip() or None
    if body.designation is not None:
        user.designation = body.designation.strip() or None
    db.commit()
    db.refresh(user)
    return _user_view(db, user)


def assign_authority(db: Session, user_id: str, body: AuthorityAdminAssign) -> dict:
    """Change the assigned authority (super admin only, revalidated here)."""
    user = _user_or_raise(db, user_id)
    auth = _authority_or_raise(db, body.authority_id)
    previous = user.authority_id
    user.authority_id = str(auth["id"])
    db.commit()
    db.refresh(user)
    return {
        "user": _user_view(db, user),
        "previous_authority_id": str(previous) if previous else None,
    }


def toggle_active(db: Session, user_id: str) -> dict:
    """Deactivate / reactivate an Authority Admin account."""
    user = _user_or_raise(db, user_id)
    user.is_active = not user.is_active
    db.commit()
    db.refresh(user)
    return _user_view(db, user)


def list_rows(
    db: Session,
    query: str | None = None,
    status: str | None = None,
    authority_id: str | None = None,
) -> list[dict]:
    """List Authority Admin accounts with optional search + filters."""
    q = db.query(User).filter(User.role == ROLE)
    if query:
        like = f"%{query.strip()}%"
        q = q.filter(
            (User.username.ilike(like))
            | (User.full_name.ilike(like))
            | (User.email.ilike(like))
        )
    if status == "active":
        q = q.filter(User.is_active.is_(True))
    elif status == "inactive":
        q = q.filter(User.is_active.is_(False))
    if authority_id:
        q = q.filter(User.authority_id == authority_id)
    rows = q.order_by(User.created_at.desc(), User.username).all()
    return [_user_view(db, u) for u in rows]


def get_row(db: Session, user_id: str) -> dict:
    return _user_view(db, _user_or_raise(db, user_id))


def self_scope(db: Session, user: User) -> dict:
    """Authenticated profile + server-derived authority scope for an Authority Admin."""
    view = _user_view(db, user)
    view["authority"] = _authority_view(db, user)
    return view
    return view


def update_own_profile(db: Session, user: User, body: PortalProfileUpdate) -> dict:
    """Authority Admin updates ONLY their own profile fields.

    Authority identity/ownership/category/status/official email are never
    editable here — those stay Super Admin responsibilities.
    """
    if body.full_name is not None:
        user.full_name = body.full_name.strip() or None
    if body.designation is not None:
        user.designation = body.designation.strip() or None
    if body.phone is not None:
        user.phone = body.phone.strip() or None
    db.commit()
    db.refresh(user)
    return self_scope(db, user)


def change_own_password(db: Session, user: User, body: PortalPasswordChange) -> bool:
    """Verify the current password, then rotate it. Never logs either value."""
    from app.auth.security import verify_password  # local import: no circular deps

    if not verify_password(body.current_password, user.hashed_password):
        raise ValueError("Current password is incorrect")
    user.hashed_password = hash_password(body.new_password)
    db.commit()
    db.refresh(user)
    return True


def _authority_view(db: Session, user: User) -> dict | None:
    """Full public authority record for an Authority Admin account.

    Used by both the Super Admin detail view and the admin's own profile.
    Only non-sensitive directory fields are exposed.
    """
    if not user.authority_id:
        return None
    auth = auth_get_by_id(db, str(user.authority_id))
    if not auth:
        return None
    return {
        "id": str(auth["id"]),
        "authority_name": auth.get("authority_name"),
        "department_name": auth.get("department_name"),
        "designation": auth.get("designation"),
        "category_id": auth.get("category_id"),
        "category_name": auth.get("category_name"),
        "email": auth.get("email"),
        "phone": auth.get("phone"),
        "office_location": auth.get("office_location"),
        "office_address": auth.get("office_address"),
        "office_timings": auth.get("office_timings"),
        "website": auth.get("website"),
        "description": auth.get("description"),
        "services_offered": auth.get("services_offered"),
        "active": auth.get("active"),
    }


def _user_view(db: Session, user: User) -> dict:
    """Non-sensitive admin account view (never any credential material)."""
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "full_name": user.full_name,
        "designation": user.designation,
        "is_active": user.is_active,
        "authority_id": str(user.authority_id) if user.authority_id else None,
        "authority_name": (_authority_view(db, user) or {}).get("authority_name"),
        "authority": _authority_view(db, user),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
    }