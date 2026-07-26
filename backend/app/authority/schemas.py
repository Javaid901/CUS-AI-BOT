"""
backend/app/authority/schemas.py

Pydantic schemas for Authority Management.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AdditionalContact(BaseModel):
    name: str = ""
    designation: str = ""
    email: str = ""
    phone: str = ""


class AuthorityCreate(BaseModel):
    department_name: str
    authority_name: str
    designation: str | None = None
    email: str
    phone: str
    alternate_phone: str | None = None
    office_address: str | None = None
    office_location: str | None = None
    office_timings: str | None = None
    website: str | None = None
    services_offered: list[str] = []
    keywords: list[str] = []
    description: str | None = None
    priority: int = 10
    active: bool = True
    logo: str | None = None
    office_image: str | None = None
    working_days: str | None = None
    emergency_contact: str | None = None
    additional_contacts: list[AdditionalContact] = []


class AuthorityUpdate(BaseModel):
    department_name: str | None = None
    authority_name: str | None = None
    designation: str | None = None
    email: str | None = None
    phone: str | None = None
    alternate_phone: str | None = None
    office_address: str | None = None
    office_location: str | None = None
    office_timings: str | None = None
    website: str | None = None
    services_offered: list[str] | None = None
    keywords: list[str] | None = None
    description: str | None = None
    priority: int | None = None
    active: bool | None = None
    logo: str | None = None
    office_image: str | None = None
    working_days: str | None = None
    emergency_contact: str | None = None
    additional_contacts: list[AdditionalContact] | None = None


class AuthorityResponse(BaseModel):
    id: str
    department_name: str
    authority_name: str
    designation: str | None
    email: str
    phone: str
    alternate_phone: str | None
    office_address: str | None
    office_location: str | None
    office_timings: str | None
    website: str | None
    services_offered: list[str]
    keywords: list[str]
    description: str | None
    priority: int
    active: bool
    logo: str | None
    office_image: str | None
    working_days: str | None
    emergency_contact: str | None
    additional_contacts: list[dict[str, str]]
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class AuthorityCard(BaseModel):
    """Lightweight contact card for chatbot responses."""
    id: str
    department_name: str
    authority_name: str
    designation: str | None
    email: str
    phone: str
    alternate_phone: str | None
    office_address: str | None
    office_location: str | None
    office_timings: str | None
    working_days: str | None
    emergency_contact: str | None
    services_offered: list[str]
    description: str | None
    priority: int


class AuthorityMatchResult(BaseModel):
    authorities: list[AuthorityCard]
    query: str
    match_type: str = "keyword"
    confidence: float = 0.0
