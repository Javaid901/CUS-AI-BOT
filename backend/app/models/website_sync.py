"""
backend/app/models/website_sync.py

ORM models for the Enterprise Website Knowledge Synchronization Engine.

  website_pages          - latest snapshot of every crawled page (HTML or document).
  website_page_versions  - immutable version history (old content is archived here,
                           never hard-deleted).
  crawl_runs             - one row per sync execution (manual / scheduled / API)
                           with per-run counters for the monitoring dashboard.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text

from app.database import Base, utcnow


class WebsitePage(Base):
    __tablename__ = "website_pages"
    __table_args__ = (Index("ix_website_pages_url", "url"),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    url = Column(String(1024), nullable=False)
    base_url = Column(String(255), nullable=True)          # originating crawl seed
    title = Column(String(400), nullable=True)
    normalized_title = Column(String(400), nullable=True)  # for title-similarity dedup
    category = Column(String(50), nullable=True, index=True)
    content_type = Column(String(30), nullable=True)       # html | pdf | docx | xlsx | csv | txt | md | ...
    content = Column(Text, nullable=True)                  # latest extracted text (HTML pages)
    raw_path = Column(String(500), nullable=True)          # on-disk copy for binary documents
    content_hash = Column(String(64), nullable=True, index=True)
    title_hash = Column(String(64), nullable=True)
    http_status = Column(Integer, nullable=True)
    etag = Column(String(255), nullable=True)
    last_modified = Column(String(100), nullable=True)
    version = Column(Integer, default=1, nullable=False)
    # status: new | unchanged | updated | archived | failed
    status = Column(String(20), default="new", nullable=False, index=True)
    document_id = Column(String(36), nullable=True)        # linked Document row (RAG source)
    first_seen = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_synced = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "base_url": self.base_url,
            "title": self.title,
            "category": self.category,
            "content_type": self.content_type,
            "content_hash": self.content_hash,
            "http_status": self.http_status,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "version": self.version,
            "status": self.status,
            "document_id": self.document_id,
            "char_len": len(self.content or ""),
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_synced": self.last_synced.isoformat() if self.last_synced else None,
            "archived_at": self.archived_at.isoformat() if self.archived_at else None,
            "last_error": self.last_error,
        }


class WebsitePageVersion(Base):
    __tablename__ = "website_page_versions"
    __table_args__ = (Index("ix_web_page_ver_page", "page_id"),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    page_id = Column(String(36), ForeignKey("website_pages.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    title = Column(String(400), nullable=True)
    category = Column(String(50), nullable=True)
    content = Column(Text, nullable=True)                  # immutable snapshot
    content_hash = Column(String(64), nullable=True)
    http_status = Column(Integer, nullable=True)
    etag = Column(String(255), nullable=True)
    last_modified = Column(String(100), nullable=True)
    synced_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "page_id": self.page_id,
            "version": self.version,
            "title": self.title,
            "category": self.category,
            "content_hash": self.content_hash,
            "http_status": self.http_status,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
        }


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    trigger = Column(String(30), default="manual", nullable=False)  # manual | scheduled | api
    status = Column(String(20), default="running", nullable=False, index=True)
    # status: running | completed | failed | stopped
    base_url = Column(String(255), nullable=True)
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)
    total_urls = Column(Integer, default=0, nullable=False)
    pages_found = Column(Integer, default=0, nullable=False)
    new_pages = Column(Integer, default=0, nullable=False)
    updated_pages = Column(Integer, default=0, nullable=False)
    unchanged_pages = Column(Integer, default=0, nullable=False)
    archived_pages = Column(Integer, default=0, nullable=False)
    duplicates_skipped = Column(Integer, default=0, nullable=False)
    failed_pages = Column(Integer, default=0, nullable=False)
    indexed_pages = Column(Integer, default=0, nullable=False)
    error = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "trigger": self.trigger,
            "status": self.status,
            "base_url": self.base_url,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
            "total_urls": self.total_urls,
            "pages_found": self.pages_found,
            "new_pages": self.new_pages,
            "updated_pages": self.updated_pages,
            "unchanged_pages": self.unchanged_pages,
            "archived_pages": self.archived_pages,
            "duplicates_skipped": self.duplicates_skipped,
            "failed_pages": self.failed_pages,
            "indexed_pages": self.indexed_pages,
            "error": self.error,
        }
