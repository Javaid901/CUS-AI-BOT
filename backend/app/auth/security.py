"""
backend/app/auth/security.py

Authentication primitives:
  - password hashing via bcrypt (directly, avoiding passlib compatibility issues)
  - JWT access + refresh token creation/verification
  - FastAPI dependencies: get_current_user, require_admin, require_superadmin
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt as _bcrypt
import jwt
from app.config import settings
from app.database import get_db
from app.models import RefreshToken, User
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/login", auto_error=False)

CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


# --------------------------------------------------------------------------- #
# Passwords (bcrypt directly; avoids passlib/bcrypt>=5 compatibility issues)
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:72]
    return _bcrypt.hashpw(pw, _bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str, role: str) -> str:
    expire = _now() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "exp": expire,
        "iat": _now(),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(db: Session, user: User) -> str:
    raw = uuid.uuid4().hex + uuid.uuid4().hex
    expire = _now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    token = RefreshToken(
        user_id=user.id,
        token=raw,
        expires_at=expire,
    )
    db.add(token)
    db.commit()
    return raw


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def _get_user_by_id(db: Session, user_id: str) -> User | None:
    try:
        return db.get(User, uuid.UUID(user_id))
    except (ValueError, AttributeError):
        return None


def get_current_user(token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    if not token:
        raise CREDENTIALS_EXC
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise CREDENTIALS_EXC
        user_id = payload.get("sub")
        if not user_id:
            raise CREDENTIALS_EXC
    except jwt.PyJWTError:
        raise CREDENTIALS_EXC

    user = _get_user_by_id(db, user_id)
    if not user or not user.is_active:
        raise CREDENTIALS_EXC
    return user


def require_role(*roles: str):
    def dependency(current: User = Depends(get_current_user)) -> User:
        if current.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient privileges",
            )
        return current

    return dependency


require_admin = require_role("admin", "superadmin")
require_superadmin = require_role("superadmin")
require_authority_admin = require_role("authority_admin")


def require_authenticated_user(current: User = Depends(get_current_user)) -> User:
    """Any logged-in, active account (student / admin / authority_admin / superadmin)."""
    return current


def require_authority_scope(authority_id: str):
    """Reject access to another authority's resources.

    The effective scope is ALWAYS derived from the authenticated user, never
    from query/path/body parameters:

      * superadmin  -> global scope (override)
      * authority_admin -> the single authority in users.authority_id
      * anyone else -> 403

    Usage: `current: User = Depends(require_authority_scope(body.authority_id))`
    raises 403 for any scope mismatch — the guard never exposes the other
    authority's data.
    """

    def dependency(current: User = Depends(get_current_user)) -> User:
        if current.role == "superadmin":
            return current
        if current.role == "authority_admin":
            if not current.authority_id or str(current.authority_id) != authority_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized for this authority",
                )
            return current
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges",
        )

    return dependency
