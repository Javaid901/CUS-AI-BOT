"""
backend/app/authority_admin/schemas.py

Request/response schemas for Authority Admin account management.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class AuthorityAdminCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    full_name: str | None = Field(None, max_length=120)
    designation: str | None = Field(None, max_length=120)
    authority_id: str = Field(min_length=36, max_length=36)
    is_active: bool = True


class AuthorityAdminUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=120)
    designation: str | None = Field(None, max_length=120)
    email: EmailStr | None = None


class AuthorityAdminAssign(BaseModel):
    authority_id: str = Field(min_length=36, max_length=36)


class PortalProfileUpdate(BaseModel):
    """Fields an Authority Admin may update on their OWN account.

    Authority/licensing fields (authority_id, category, status, official
    email, routing) are deliberately absent — they are Super Admin concerns.
    """
    full_name: str | None = Field(None, max_length=120)
    designation: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, max_length=30)


class PortalPasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class PortalStatusChange(BaseModel):
    new_status: str = Field(min_length=1, max_length=20)
    note: str | None = Field(None, max_length=5000)


class PortalResponseCreate(BaseModel):
    response: str = Field(min_length=2, max_length=5000)


class AuthorityAdminResponse(BaseModel):
    id: str
    username: str
    email: str | None
    role: str
    full_name: str | None
    designation: str | None
    is_active: bool
    authority_id: str | None
    authority_name: str | None
    created_at: str | None
    last_login: str | None

    class Config:
        from_attributes = True