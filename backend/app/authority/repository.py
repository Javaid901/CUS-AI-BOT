"""
backend/app/authority/repository.py

Database access layer for Authority CRUD operations.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.authority.models import Authority, GrievanceCategory


def _parse_json(value: str | None) -> Any:
    if not value:
        return [] if value is None else value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []


def _to_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value)


def _row_to_dict(row: Authority) -> dict[str, Any]:
    return {
        "id": row.id,
        "department_name": row.department_name,
        "authority_name": row.authority_name,
        "designation": row.designation,
        "email": row.email,
        "phone": row.phone,
        "alternate_phone": row.alternate_phone,
        "office_address": row.office_address,
        "office_location": row.office_location,
        "office_timings": row.office_timings,
        "website": row.website,
        "services_offered": _parse_json(row.services_offered),
        "keywords": _parse_json(row.keywords),
        "description": row.description,
        "priority": row.priority,
        "active": row.active,
        "logo": row.logo,
        "office_image": row.office_image,
        "working_days": row.working_days,
        "emergency_contact": row.emergency_contact,
        "additional_contacts": _parse_json(row.additional_contacts),
        "category_id": row.category_id,
        "category_name": row.category.name if row.category else None,
        "source_kind": row.source_kind or "manual",
        "deleted_at": row.deleted_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _not_deleted() -> Any:
    """Filter predicate: non-deleted authorities only."""
    return Authority.deleted_at.is_(None)


def _category_cols(data: dict[str, Any]) -> dict[str, Any]:
    """Extract category handling from a create/update payload."""
    out: dict[str, Any] = {}
    if "category_id" in data:
        out["category_id"] = data.get("category_id") or None
    if "source_kind" in data:
        out["source_kind"] = data.get("source_kind") or "manual"
    return out


def find_duplicate(db: Session, authority_name: str, email: str, exclude_id: str | None = None) -> dict[str, str] | None:
    """Return which uniqueness rule is violated (name/email) or None.

    Soft-deleted records do not block re-creation (a deleted authority may be
    re-added under the same name/email once it is removed from management).
    """
    q = db.query(Authority).filter(
        _not_deleted(),
        or_(
            Authority.authority_name == authority_name,
            Authority.email == email,
        ),
    )
    existing = q.all()
    for row in existing:
        if exclude_id and row.id == exclude_id:
            continue
        if row.authority_name.lower() == authority_name.lower():
            return {"field": "authority_name", "value": row.authority_name}
        if row.email.lower() == email.lower():
            return {"field": "email", "value": row.email}
    return None


def list_all(db: Session, active_only: bool = False, include_deleted: bool = False) -> list[dict[str, Any]]:
    q = db.query(Authority)
    if not include_deleted:
        q = q.filter(_not_deleted())
    if active_only:
        q = q.filter(Authority.active == True)
    q = q.order_by(Authority.priority, Authority.department_name)
    return [_row_to_dict(r) for r in q.all()]


def get_by_id(db: Session, authority_id: str, include_deleted: bool = False) -> dict[str, Any] | None:
    q = db.query(Authority).filter(Authority.id == authority_id)
    if not include_deleted:
        q = q.filter(_not_deleted())
    row = q.first()
    return _row_to_dict(row) if row else None


def search(
    db: Session,
    query: str | None = None,
    department: str | None = None,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    q = db.query(Authority).filter(_not_deleted())
    if active_only:
        q = q.filter(Authority.active == True)
    if department:
        q = q.filter(Authority.department_name == department)
    if query:
        pattern = f"%{query}%"
        q = q.filter(
            or_(
                Authority.authority_name.ilike(pattern),
                Authority.department_name.ilike(pattern),
                Authority.designation.ilike(pattern),
                Authority.email.ilike(pattern),
                Authority.description.ilike(pattern),
                Authority.keywords.ilike(pattern),
                Authority.services_offered.ilike(pattern),
            )
        )
    q = q.order_by(Authority.priority, Authority.department_name)
    return [_row_to_dict(r) for r in q.all()]


def list_departments(db: Session, active_only: bool = False) -> list[str]:
    q = db.query(Authority.department_name).distinct().filter(_not_deleted())
    if active_only:
        q = q.filter(Authority.active == True)
    rows = q.order_by(Authority.department_name).all()
    seen: set[str] = set()
    result: list[str] = []
    for (name,) in rows:
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def create(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    row = Authority(
        department_name=data["department_name"],
        authority_name=data["authority_name"],
        designation=data.get("designation"),
        email=data["email"],
        phone=data["phone"],
        alternate_phone=data.get("alternate_phone"),
        office_address=data.get("office_address"),
        office_location=data.get("office_location"),
        office_timings=data.get("office_timings"),
        website=data.get("website"),
        services_offered=_to_json(data.get("services_offered")),
        keywords=_to_json(data.get("keywords")),
        description=data.get("description"),
        priority=data.get("priority", 10),
        active=data.get("active", True),
        logo=data.get("logo"),
        office_image=data.get("office_image"),
        working_days=data.get("working_days"),
        emergency_contact=data.get("emergency_contact"),
        additional_contacts=_to_json(data.get("additional_contacts")),
        **(_category_cols(data)),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_to_dict(row)


def update(db: Session, authority_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    row = db.query(Authority).filter(Authority.id == authority_id).first()
    if not row:
        return None
    for key, value in data.items():
        if value is None and key in ("services_offered", "keywords", "additional_contacts"):
            continue
        if key in ("services_offered", "keywords"):
            setattr(row, key, _to_json(value))
        elif key == "additional_contacts":
            setattr(row, key, _to_json([c.model_dump() if hasattr(c, "model_dump") else c for c in value]))
        elif key in ("category_id", "source_kind"):
            setattr(row, key, value)
        elif hasattr(row, key):
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return _row_to_dict(row)


def delete(db: Session, authority_id: str) -> bool:
    """Delete an authority safely.

    * If historical (grievance) records reference this authority, the row is
      SOFT-deleted (`deleted_at` set, record kept) so historical grievances
      keep resolving their original authority information.
    * If nothing references it, the row is hard-deleted as before.
    In both cases the authority is immediately ineligible for NEW grievances.
    """
    from app.grievance.models import Grievance

    row = db.query(Authority).filter(Authority.id == authority_id).first()
    if not row:
        return False

    has_historical = (
        db.query(Grievance.id)
        .filter(Grievance.authority_id == authority_id)
        .first()
        is not None
    )
    if has_historical:
        from app.database import utcnow

        row.deleted_at = utcnow()
        row.active = False
        db.commit()
        return True

    db.delete(row)
    db.commit()
    return True


def bulk_create(db: Session, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    created: list[Authority] = []
    for data in items:
        row = Authority(
            department_name=data["department_name"],
            authority_name=data["authority_name"],
            designation=data.get("designation"),
            email=data["email"],
            phone=data["phone"],
            alternate_phone=data.get("alternate_phone"),
            office_address=data.get("office_address"),
            office_location=data.get("office_location"),
            office_timings=data.get("office_timings"),
            website=data.get("website"),
            services_offered=_to_json(data.get("services_offered")),
            keywords=_to_json(data.get("keywords")),
            description=data.get("description"),
            priority=data.get("priority", 10),
            active=data.get("active", True),
            logo=data.get("logo"),
            office_image=data.get("office_image"),
            working_days=data.get("working_days"),
            emergency_contact=data.get("emergency_contact"),
            additional_contacts=_to_json(data.get("additional_contacts")),
            **(_category_cols(data)),
        )
        db.add(row)
        created.append(row)
    db.commit()
    for row in created:
        db.refresh(row)
    return [_row_to_dict(r) for r in created]


# ---------------------------------------------------------------------------
# Grievance categories (DB-driven catalog)
# ---------------------------------------------------------------------------


def list_categories(db: Session, active_only: bool = False) -> list[dict[str, Any]]:
    q = db.query(GrievanceCategory)
    if active_only:
        q = q.filter(GrievanceCategory.active == True)
    rows = q.order_by(GrievanceCategory.name).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "slug": c.slug,
            "description": c.description,
            "active": c.active,
            "authority_count": len(c.authorities),
            "created_at": c.created_at,
            "updated_at": c.updated_at,
        }
        for c in rows
    ]


def get_category_by_slug(db: Session, slug: str) -> dict[str, Any] | None:
    row = db.query(GrievanceCategory).filter(GrievanceCategory.slug == slug).first()
    if not row:
        return None
    return {
        "id": row.id,
        "name": row.name,
        "slug": row.slug,
        "description": row.description,
        "active": row.active,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def create_category(db: Session, name: str, description: str | None = None, slug: str | None = None) -> dict[str, Any]:
    import re

    row = GrievanceCategory(
        name=name,
        slug=slug or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
        description=description,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "name": row.name,
        "slug": row.slug,
        "description": row.description,
        "active": row.active,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
