"""
backend/app/authority/repository.py

Database access layer for Authority CRUD operations.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.authority.models import Authority


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
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def list_all(db: Session, active_only: bool = False) -> list[dict[str, Any]]:
    q = db.query(Authority)
    if active_only:
        q = q.filter(Authority.active == True)
    q = q.order_by(Authority.priority, Authority.department_name)
    return [_row_to_dict(r) for r in q.all()]


def get_by_id(db: Session, authority_id: str) -> dict[str, Any] | None:
    row = db.query(Authority).filter(Authority.id == authority_id).first()
    return _row_to_dict(row) if row else None


def search(
    db: Session,
    query: str | None = None,
    department: str | None = None,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    q = db.query(Authority)
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
    q = db.query(Authority.department_name).distinct()
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
        elif hasattr(row, key):
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return _row_to_dict(row)


def delete(db: Session, authority_id: str) -> bool:
    row = db.query(Authority).filter(Authority.id == authority_id).first()
    if not row:
        return False
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
        )
        db.add(row)
        created.append(row)
    db.commit()
    for row in created:
        db.refresh(row)
    return [_row_to_dict(r) for r in created]
