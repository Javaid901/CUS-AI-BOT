"""SyncSource model — tracks knowledge sync operations and their status."""

from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database import Base, utcnow


class SyncSource(Base):
    __tablename__ = "sync_sources"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    url = Column(String(1024), nullable=False, index=True)
    filename = Column(String(400), nullable=True)
    category = Column(String(100), nullable=True)
    year = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)
    status = Column(String(20), default="downloaded", nullable=False, index=True)
    # status: downloaded | reviewed | ingested | failed
    document_id = Column(String(36), nullable=True)
    error = Column(Text, nullable=True)
    file_size = Column(Integer, nullable=True)
    source = Column(String(100), default="sync", nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "filename": self.filename,
            "category": self.category,
            "year": self.year,
            "sha256": self.sha256,
            "status": self.status,
            "document_id": self.document_id,
            "error": self.error,
            "file_size": self.file_size,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
