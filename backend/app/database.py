"""
backend/app/database.py

SQLAlchemy engine / session management.

The metadata layer (users, documents, chunks, conversations, messages, audit logs)
uses SQLAlchemy Core 2.0 style with a session factory. UUID primary keys are used
everywhere. Works with SQLite out of the box; switch to PostgreSQL by setting
DATABASE_URL (e.g. postgresql+psycopg://user:pass@host:5432/cus_ai).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _make_engine():
    url = settings.DATABASE_URL
    connect_args = {}
    # SQLite needs check_same_thread=False for use across FastAPI threads.
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    return create_engine(
        url,
        echo=settings.DB_ECHO,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    """Create all tables (used by run.py / startup). Prefer Alembic in production."""
    # Import models so they are registered on Base.metadata before create_all.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
