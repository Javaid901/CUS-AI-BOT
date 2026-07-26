"""
backend/app/authority/service.py

Business logic and in-memory cache for Authority records.

Cache is refreshed automatically after any admin CRUD operation.
Authority lookups against the cache are < 1ms (pure dict ops).
"""

from __future__ import annotations

import threading
from typing import Any

from sqlalchemy.orm import Session

from app.authority.repository import (
    bulk_create as repo_bulk_create,
)
from app.authority.repository import (
    create as repo_create,
)
from app.authority.repository import (
    delete as repo_delete,
)
from app.authority.repository import (
    get_by_id as repo_get_by_id,
)
from app.authority.repository import (
    list_all as repo_list_all,
)
from app.authority.repository import (
    list_departments as repo_list_departments,
)
from app.authority.repository import (
    search as repo_search,
)
from app.authority.repository import (
    update as repo_update,
)
from app.authority.schemas import AuthorityCreate, AuthorityUpdate


class AuthorityService:
    """Thread-safe service with in-memory cache."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: dict[str, dict[str, Any]] = {}  # id -> row dict
        self._department_index: dict[str, list[str]] = {}  # department -> [id, ...]
        self._keyword_index: dict[str, list[str]] = {}  # keyword -> [id, ...]
        self._loaded = False

    def _build_indexes(self, rows: list[dict[str, Any]]) -> None:
        self._cache = {}
        self._department_index = {}
        self._keyword_index = {}
        for row in rows:
            rid = row["id"]
            self._cache[rid] = row
            dept = (row["department_name"] or "").lower()
            self._department_index.setdefault(dept, []).append(rid)
            for kw in row.get("keywords", []):
                w = kw.lower().strip()
                if w:
                    self._keyword_index.setdefault(w, []).append(rid)
            for svc in row.get("services_offered", []):
                w = svc.lower().strip()
                if w:
                    self._keyword_index.setdefault(w, []).append(rid)

    def load_cache(self, db: Session) -> None:
        """Load all active authorities into memory."""
        with self._lock:
            rows = repo_list_all(db, active_only=True)
            self._build_indexes(rows)
            self._loaded = True

    def refresh_cache(self, db: Session) -> None:
        """Force a full cache reload."""
        self.load_cache(db)

    def get(self, authority_id: str) -> dict[str, Any] | None:
        """Lookup an authority by ID from cache."""
        with self._lock:
            return self._cache.get(authority_id)

    def get_db(self, db: Session, authority_id: str) -> dict[str, Any] | None:
        """Lookup from DB directly (bypasses cache)."""
        return repo_get_by_id(db, authority_id)

    def list_active(self) -> list[dict[str, Any]]:
        """Return all cached active authorities."""
        with self._lock:
            return list(self._cache.values())

    def list_by_department(self, department: str) -> list[dict[str, Any]]:
        """Return cached authorities for a given department."""
        with self._lock:
            ids = self._department_index.get(department.lower(), [])
            return [self._cache[rid] for rid in ids if rid in self._cache]

    def search(self, db: Session, query: str | None = None, department: str | None = None) -> list[dict[str, Any]]:
        """Search from DB (admin full-text search)."""
        return repo_search(db, query=query, department=department)

    def list_departments(self, db: Session) -> list[str]:
        """Return distinct department names from DB."""
        return repo_list_departments(db)

    def create(self, db: Session, data: AuthorityCreate) -> dict[str, Any]:
        """Create an authority and refresh cache."""
        row = repo_create(db, data.model_dump())
        self.refresh_cache(db)
        return row

    def update(self, db: Session, authority_id: str, data: AuthorityUpdate) -> dict[str, Any] | None:
        """Update an authority and refresh cache."""
        dump = {k: v for k, v in data.model_dump().items() if v is not None}
        row = repo_update(db, authority_id, dump)
        if row:
            self.refresh_cache(db)
        return row

    def delete(self, db: Session, authority_id: str) -> bool:
        """Delete an authority and refresh cache."""
        ok = repo_delete(db, authority_id)
        if ok:
            self.refresh_cache(db)
        return ok

    def bulk_create(self, db: Session, items: list[AuthorityCreate]) -> list[dict[str, Any]]:
        """Bulk import authorities and refresh cache."""
        rows = repo_bulk_create(db, [i.model_dump() for i in items])
        self.refresh_cache(db)
        return rows


authority_service = AuthorityService()
