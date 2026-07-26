"""
backend/app/authority/models.py

SQLAlchemy model for university authorities/offices.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

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
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<Authority {self.authority_name} ({self.department_name})>"
