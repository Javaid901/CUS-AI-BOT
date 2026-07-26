"""
backend/app/auth/routes.py

Authentication endpoints.

Mirrors what the existing frontend expects:
  - POST /api/auth/register   (JSON)  -> {access_token, token_type, user}
  - POST /api/auth/login       (form x-www-form-urlencoded) -> {access_token, token_type}
  - POST /api/auth/refresh     (JSON {refresh_token}) -> {access_token}

The chat widget self-registers a "student" account to obtain a JWT. Admins are
seeded from env on startup and log in with the same login endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.auth.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.models import RefreshToken, User
from app.utils.logging import audit
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

router = APIRouter(prefix=f"{settings.API_PREFIX}/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    role: str = "student"  # widget sends "student"; ignored for privilege


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None
    user: dict | None = None


@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="Username already registered")
    # Self-registration is always a low-privilege "student" (chat-only) account.
    user = User(
        id=uuid.uuid4(),
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        role="student",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    audit(db, "register", actor_id=str(user.id), actor_role=user.role, ip=request.client.host if request.client else None)
    access = create_access_token(str(user.id), user.role)
    refresh = create_refresh_token(db, user)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user={"id": str(user.id), "username": user.username, "role": user.role},
    )


@router.post("/login", response_model=TokenResponse)
def login(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        audit(db, "login_failed", target=username, ip=request.client.host if request.client else None)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    audit(db, "login", actor_id=str(user.id), actor_role=user.role, ip=request.client.host if request.client else None)
    access = create_access_token(str(user.id), user.role)
    refresh = create_refresh_token(db, user)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user={"id": str(user.id), "username": user.username, "role": user.role},
    )


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    token = db.query(RefreshToken).filter(RefreshToken.token == body.refresh_token).first()
    if not token or token.revoked:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token expired")
    user = db.get(User, token.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")
    access = create_access_token(str(user.id), user.role)
    return TokenResponse(access_token=access, user={"id": str(user.id), "username": user.username, "role": user.role})
