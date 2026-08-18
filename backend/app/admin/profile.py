"""
backend/app/admin/profile.py

Admin profile management.

  GET    /api/admin/profile                    -> current admin profile
  PUT    /api/admin/profile                    -> update name/designation/phone/email
  PUT    /api/admin/profile/username           -> change username (password required)
  PUT    /api/admin/profile/password           -> change password (old password required)
  POST   /api/admin/profile/avatar             (multipart "file") -> upload profile photo

Avatars are stored under backend/uploads/avatars and served from the
/api/uploads static mount (registered in main.py before the frontend mount).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from app.auth.security import hash_password, require_admin, verify_password
from app.config import settings
from app.database import get_db
from app.models import RefreshToken, User
from app.utils.logging import audit
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix=f"{settings.API_PREFIX}/admin/profile", tags=["admin-profile"])
_protected = Depends(require_admin)

AVATAR_DIR = Path(__file__).resolve().parent.parent.parent / "uploads" / "avatars"
MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB
ALLOWED_AVATAR_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")

# Magic-byte signatures so a spoofed Content-Type can't smuggle non-image bytes
# into a file served back as an image.
MAGIC_BYTES = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/webp": b"RIFF",  # RIFF....WEBP
    "image/gif": b"GIF8",
}


def _looks_like_image(content_type: str, data: bytes) -> bool:
    if not data:
        return False
    if content_type in ("image/png", "image/jpeg", "image/gif"):
        return data.startswith(MAGIC_BYTES[content_type])
    if content_type == "image/webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    return False


def _profile_view(user: User) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "full_name": user.full_name,
        "designation": user.designation,
        "phone": user.phone,
        "avatar_url": f"{settings.API_PREFIX}/uploads/avatars/{user.avatar_path}" if user.avatar_path else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


@router.get("")
def get_profile(current: User = _protected):
    return _profile_view(current)


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=120)
    designation: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, max_length=30)
    email: str | None = Field(None, max_length=255)


@router.put("")
def update_profile(
    body: ProfileUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    email = (body.email or "").strip() or None
    if email and "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    if email != current.email:
        clash = db.query(User).filter(User.email == email, User.id != current.id).first()
        if clash:
            raise HTTPException(status_code=409, detail="Email is already in use")
    if body.full_name is not None:
        current.full_name = body.full_name.strip() or None
    if body.designation is not None:
        current.designation = body.designation.strip() or None
    if body.phone is not None:
        current.phone = body.phone.strip() or None
    current.email = email
    current.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(current)
    audit(
        db, "profile_update", actor_id=str(current.id), actor_role=current.role,
        ip=request.client.host if request.client else None,
        detail=f"updated by {current.username}",
    )
    return _profile_view(current)


class UsernameChange(BaseModel):
    new_username: str
    password: str


@router.put("/username")
def change_username(
    body: UsernameChange,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    new_username = body.new_username.strip()
    if not USERNAME_RE.match(new_username):
        raise HTTPException(status_code=400, detail="Username must be 3-32 characters (letters, digits, underscore)")
    if not verify_password(body.password, current.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if new_username != current.username:
        clash = db.query(User).filter(User.username == new_username).first()
        if clash:
            raise HTTPException(status_code=409, detail="Username is already taken")
        current.username = new_username
    current.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(current)
    audit(
        db, "username_change", actor_id=str(current.id), actor_role=current.role,
        ip=request.client.host if request.client else None,
        detail=f"renamed to {new_username}",
    )
    return _profile_view(current)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


@router.put("/password")
def change_password(
    body: PasswordChange,
    request: Request,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    if not verify_password(body.current_password, current.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if verify_password(body.new_password, current.hashed_password):
        raise HTTPException(status_code=400, detail="New password must differ from the current one")
    current.hashed_password = hash_password(body.new_password)
    current.updated_at = datetime.now(timezone.utc)
    # Revoke all existing refresh tokens — force a fresh sign-in elsewhere.
    db.query(RefreshToken).filter(RefreshToken.user_id == current.id, RefreshToken.revoked.is_(False)).update(
        {"revoked": True}, synchronize_session=False
    )
    db.commit()
    db.refresh(current)
    audit(
        db, "password_change", actor_id=str(current.id), actor_role=current.role,
        ip=request.client.host if request.client else None,
    )
    return {"ok": True, "detail": "Password updated"}


@router.delete("/avatar")
def remove_avatar(
    request: Request,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    if not current.avatar_path:
        return _profile_view(current)
    old = AVATAR_DIR / Path(current.avatar_path).name
    if old.exists():
        try:
            old.unlink()
        except OSError:
            pass
    current.avatar_path = None
    current.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(current)
    audit(
        db, "avatar_removed", actor_id=str(current.id), actor_role=current.role,
        ip=request.client.host if request.client else None,
    )
    return _profile_view(current)


@router.post("/avatar")
def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current: User = _protected,
):
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_AVATAR_TYPES:
        raise HTTPException(status_code=400, detail="Only PNG, JPG, WEBP or GIF images are allowed")
    try:
        data = file.file.read(MAX_AVATAR_BYTES + 1)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read uploaded file")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="Image must be 2 MB or smaller")
    if not _looks_like_image(content_type, data):
        raise HTTPException(status_code=400, detail="File content does not match the image type")

    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    ext = ALLOWED_AVATAR_TYPES[content_type]
    filename = f"{current.id}{ext}"

    # Remove the previous avatar (any extension).
    if current.avatar_path:
        old = AVATAR_DIR / Path(current.avatar_path).name
        if old.exists():
            try:
                old.unlink()
            except OSError:
                pass

    target = AVATAR_DIR / filename
    target.write_bytes(data)

    current.avatar_path = filename
    current.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(current)
    audit(
        db, "avatar_update", actor_id=str(current.id), actor_role=current.role,
        ip=request.client.host if request.client else None,
    )
    return _profile_view(current)
