"""
backend/app/authority/models.py

SQLAlchemy model for university authorities/offices.

PHASE 2 additions (additive):
  * `category_id` — optional FK to grievance_categories (DB-driven categorization)
  * `source_kind` — where the record came from (official | manual | custom)
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base, utcnow


class Authority(Base):
    __tablename__ = "authorities"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_name = Column(String(200), nullable=False, index=True)
    authority_name = Column(String(200), nullable=False)
    designation = Column(String(200), nullable=True)
    email = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=False)
    alternate_phone = Column(String(50), nullable=True)
    office_address = Column(Text, nullable=True)
    office_location = Column(String(500), nullable=True)
    office_timings = Column(String(200), nullable=True)
    website = Column(String(500), nullable=True)
    services_offered = Column(Text, nullable=True)
    keywords = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    priority = Column(Integer, default=10, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    logo = Column(String(500), nullable=True)
    office_image = Column(String(500), nullable=True)
    working_days = Column(String(200), nullable=True)
    emergency_contact = Column(String(50), nullable=True)
    additional_contacts = Column(Text, nullable=True)
    # ----- Grievance categorization (Phase 2, additive) -----
    category_id = Column(
        String(36),
        ForeignKey("grievance_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Data provenance: official (imported from the university site) | manual | custom
    source_kind = Column(String(20), default="manual", nullable=True)
    # Soft deletion: set when the Super Admin deletes an authority that still
    # has historical grievances. Deleted authorities are never listed as
    # available destinations for new grievances, but historical records keep
    # resolving their original authority information.
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    category = relationship("GrievanceCategory", back_populates="authorities")

    def __repr__(self) -> str:
        return f"<Authority {self.authority_name} ({self.department_name})>"


class GrievanceCategory(Base):
    """DB-driven grievance category catalog (e.g. Academic Affairs, Examinations).

    Categories are managed by the Super Admin; new categories can be added
    without touching application code (spec: DB-driven, no hardcoded lists).
    """

    __tablename__ = "grievance_categories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(120), nullable=False, unique=True, index=True)
    slug = Column(String(120), nullable=False, unique=True, index=True)
    description = Column(String(500), nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    authorities = relationship("Authority", back_populates="category")

    def __repr__(self) -> str:
        return f"<GrievanceCategory {self.name}>"
