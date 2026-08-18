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
    create_category as repo_create_category,
)
from app.authority.repository import (
    delete as repo_delete,
)
from app.authority.repository import (
    find_duplicate as repo_find_duplicate,
)
from app.authority.repository import (
    get_by_id as repo_get_by_id,
)
from app.authority.repository import (
    get_category_by_slug as repo_get_category_by_slug,
)
from app.authority.repository import (
    list_all as repo_list_all,
)
from app.authority.repository import (
    list_categories as repo_list_categories,
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
        """Create an authority (duplicate-guarded) and refresh cache."""
        dup = repo_find_duplicate(db, data.authority_name, data.email)
        if dup:
            raise ValueError(f"Duplicate authority: {dup['field']} already exists ({dup['value']})")
        row = repo_create(db, data.model_dump())
        self.refresh_cache(db)
        return row

    def update(self, db: Session, authority_id: str, data: AuthorityUpdate) -> dict[str, Any] | None:
        """Update an authority (duplicate-guarded) and refresh cache."""
        dump = {k: v for k, v in data.model_dump().items() if v is not None}
        existing = repo_get_by_id(db, authority_id)
        if existing:
            new_name = dump.get("authority_name", existing["authority_name"])
            new_email = dump.get("email", existing["email"])
            dup = repo_find_duplicate(db, new_name, new_email, exclude_id=authority_id)
            if dup:
                raise ValueError(f"Duplicate authority: {dup['field']} already exists ({dup['value']})")
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

    # ----- Grievance authority matching (chatbot auto-routing) -----

    def match_for_grievance(self, db: Session, text: str) -> dict[str, Any]:
        """Resolve the authority a student named in a grievance message.

        Returns one of:
          {"status": "matched",     "authority": {...}}                 unique ACTIVE match
          {"status": "ambiguous",   "matches": [{...}, ...]}            several ACTIVE matches
          {"status": "unavailable", "names": ["...", ...]}              mentioned but inactive/deleted
          {"status": "none"}                                            nothing matched

        Matching is normalized (lowercase, punctuation collapsed, "&" → "and")
        and evaluated against real authority records — never hardcoded names.
        A bare generic mention such as "the dean" surfaces as ambiguous when
        several Dean authorities exist, so the student chooses; an inactive or
        deleted authority is reported as unavailable and never auto-selected.
        """
        import re

        text = (text or "").strip()
        if len(text) < 3:
            return {"status": "none"}

        def _norm(value: str) -> str:
            v = value.lower().replace("&", " and ")
            v = re.sub(r"[^a-z0-9]+", " ", v)
            return re.sub(r"\s+", " ", v).strip()

        message = " " + _norm(text) + " "

        rows = repo_list_all(db, include_deleted=True)
        phrases: list[tuple[dict[str, Any], set[str]]] = []
        for row in rows:
            name = _norm(row.get("authority_name") or "")
            dept = _norm(row.get("department_name") or "")
            if not name:
                continue
            variants = {name}
            name_words = name.split(" ")
            if name_words and name_words[0] == "dean" and len(name_words) > 1:
                # "Dean Science" is also commonly written "Dean of Science".
                variants.add(f"dean of {' '.join(name_words[1:])}")
                # A bare "the Dean" mention intentionally surfaces as ambiguous
                # when several Dean authorities exist (student chooses).
                variants.add("dean")
            elif len(name_words) == 1:
                # Single-word offices ("registrar") match their bare word.
                variants.add(name)
            if dept:
                variants.add(dept)
            phrases.append((row, variants))

        active_matches: list[dict[str, Any]] = []
        unavailable_names: list[str] = []
        for row, variants in phrases:
            if any(v and v in message for v in variants):
                if row.get("active") and row.get("deleted_at") is None:
                    active_matches.append(row)
                else:
                    unavailable_names.append(
                        (row.get("authority_name") or "").strip() or row.get("department_name") or "that office"
                    )

        if len(active_matches) == 1:
            return {"status": "matched", "authority": self._public_card(active_matches[0])}
        if len(active_matches) > 1:
            active_matches.sort(key=lambda r: (r.get("priority") or 10, r.get("authority_name") or ""))
            return {"status": "ambiguous", "matches": [self._public_card(r) for r in active_matches]}

        # Fallback: alias / service-route resolution ("examination branch" →
        # Controller of Examinations, "fee issue" → Finance). Only accepts a
        # UNIQUE active authority for the resolved department — never invents
        # a match and never returns inactive/deleted records.
        dept_hint = self._department_from_aliases(text)
        if dept_hint:
            dept_matches = [
                r for r in repo_list_all(db, include_deleted=True)
                if r.get("active") and r.get("deleted_at") is None
                and _norm((r.get("department_name") or "")).lower() == dept_hint.lower()
            ]
            if len(dept_matches) == 1:
                return {"status": "matched", "authority": self._public_card(dept_matches[0])}

        if unavailable_names:
            seen: list[str] = []
            for n in unavailable_names:
                if n not in seen:
                    seen.append(n)
            return {"status": "unavailable", "names": seen[:5]}
        return {"status": "none"}

    @staticmethod
    def _department_from_aliases(text: str) -> str | None:
        """Resolve a department via the matcher alias/service-route maps.

        Mirrors matcher._department_from_aliases: collects every matching
        alias, prefers the longest one, and ignores trivial 2-char aliases
        ("it", "vc") that would false-positive on everyday words.
        """
        from app.authority.matcher import DEPARTMENT_ALIASES, SERVICE_ROUTES
        lower = text.lower()
        matches: list[tuple[str, int]] = []
        for alias, dept in DEPARTMENT_ALIASES.items():
            if len(alias) < 3:
                continue
            if alias in lower:
                matches.append((dept, len(alias)))
        if not matches:
            for svc, dept in SERVICE_ROUTES.items():
                if svc in lower:
                    matches.append((dept, len(svc)))
        if not matches:
            return None
        matches.sort(key=lambda x: -x[1])
        return matches[0][0]

    @staticmethod
    def _public_card(row: dict[str, Any]) -> dict[str, Any]:
        """Sparse student-facing fields (same shape as /api/authority/active)."""
        return {
            "authority_id": row.get("id", ""),
            "authority_name": row.get("authority_name", ""),
            "department_name": row.get("department_name") or "",
            "designation": row.get("designation") or "",
            "email": row.get("email") or "",
        }

    # ----- Grievance categories -----

    def list_categories(self, db: Session, active_only: bool = False) -> list[dict[str, Any]]:
        return repo_list_categories(db, active_only=active_only)

    def get_category_by_slug(self, db: Session, slug: str) -> dict[str, Any] | None:
        return repo_get_category_by_slug(db, slug)

    def create_category(self, db: Session, name: str, description: str | None = None, slug: str | None = None) -> dict[str, Any]:
        """Create a category (duplicate-guarded on name, case-insensitive)."""
        existing = repo_list_categories(db)
        lower = name.strip().lower()
        if any(c["name"].lower() == lower for c in existing):
            raise ValueError(f"Category already exists: {name}")
        return repo_create_category(db, name, description=description, slug=slug)


authority_service = AuthorityService()
