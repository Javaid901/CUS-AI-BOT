"""CollegeService — structured lookup for college information."""

from __future__ import annotations

from typing import Any

from app.college.aliases import resolve as resolve_college
from app.college.data import COLLEGE_LIST, COLLEGES


class CollegeService:
    """Fast structured lookups for college data. Never calls LLM."""

    @staticmethod
    def get_college(college_id: str) -> dict[str, Any] | None:
        return COLLEGES.get(college_id)

    @staticmethod
    def find(message: str) -> dict[str, Any] | None:
        """Resolve a message to a college dict."""
        cid = resolve_college(message)
        if cid:
            return COLLEGES.get(cid)
        return None

    @staticmethod
    def list_all() -> list[dict[str, str]]:
        return COLLEGE_LIST

    @staticmethod
    def get_overview(college_id: str) -> dict[str, Any] | None:
        c = COLLEGES.get(college_id)
        if not c:
            return None
        return {
            "name": c["name"],
            "type": c["type"],
            "established": c["established"],
            "naac": c.get("naac", "N/A"),
            "principal": c.get("principal", ""),
            "address": c.get("address", ""),
            "district": c.get("district", ""),
            "about": c.get("about", ""),
            "phone": c.get("phone", ""),
            "email": c.get("email", ""),
            "website": c.get("website", ""),
        }

    @staticmethod
    def get_departments(college_id: str) -> list[str] | None:
        c = COLLEGES.get(college_id)
        if c:
            return c.get("departments", [])
        return None

    @staticmethod
    def get_programmes(college_id: str) -> list[dict[str, str]] | None:
        c = COLLEGES.get(college_id)
        if c:
            return c.get("programmes", [])
        return None

    @staticmethod
    def get_fees(college_id: str, programme_id: str | None = None) -> dict[str, str] | None:
        c = COLLEGES.get(college_id)
        if not c:
            return None
        fees = c.get("fees", {})
        if programme_id and programme_id in fees:
            return {programme_id: fees[programme_id]}
        return fees

    @staticmethod
    def get_facilities(college_id: str) -> list[str] | None:
        c = COLLEGES.get(college_id)
        if c:
            return c.get("facilities", [])
        return None

    @staticmethod
    def get_contact(college_id: str) -> dict[str, str] | None:
        c = COLLEGES.get(college_id)
        if c:
            return c.get("contact", {})
        return None

    @staticmethod
    def get_eligibility(college_id: str, level: str | None = None) -> dict[str, str] | str | None:
        c = COLLEGES.get(college_id)
        if not c:
            return None
        el = c.get("eligibility", {})
        if level and level in el:
            return el[level]
        return el

    @staticmethod
    def has_programme(college_id: str, programme_id: str) -> bool:
        c = COLLEGES.get(college_id)
        if not c:
            return False
        return any(p["id"] == programme_id for p in c.get("programmes", []))

    @staticmethod
    def get_programme_fees(college_id: str, programme_id: str) -> str | None:
        """Get fee for a specific programme at a college."""
        c = COLLEGES.get(college_id)
        if not c:
            return None
        return c.get("fees", {}).get(programme_id)

    @staticmethod
    def search(query: str) -> list[dict[str, Any]]:
        """Search colleges by name/alias/district."""
        q = query.strip().lower()
        results = []
        for c in COLLEGE_LIST:
            if q in c["name"].lower() or q in c.get("district", "").lower():
                results.append(c)
        return results

    @staticmethod
    def get_college_id_for_programme(programme_id: str) -> str | None:
        """Find which college offers a given programme (first match)."""
        for cid, c in COLLEGES.items():
            for p in c.get("programmes", []):
                if p["id"] == programme_id:
                    return cid
        return None
