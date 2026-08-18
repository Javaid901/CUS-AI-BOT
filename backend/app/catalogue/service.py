"""
backend/app/catalogue/service.py

Academic Catalogue service layer.

Query + admin CRUD functions. Every function accepts an optional `db`
session (used by admin routes and tests). The chatbot pipeline calls them
without one — a short-lived session is opened and closed internally.

Design note: structured catalogue data has priority over generic RAG.
The orchestrator only routes to the catalogue when matching records
actually exist; otherwise the existing pipeline handles the query.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.orchestrator.context import PROGRAMME_ALIASES

_log = logging.getLogger("cus")

from app.catalogue.models import (  # noqa: E402  (module docstring grouping)
    AcademicScheme,
    CurriculumDocument,
    CurriculumUpload,
    LearningOutcome,
    MinorDiscipline,
    Programme,
    ProgrammeCategory,
    ProgrammeSubject,
)

CATEGORY_LABELS: dict[str, str] = {
    "major": "Major Subjects",
    "minor": "Minor Subjects",
    "vac": "Value Added Courses (VAC)",
    "sec": "Skill Enhancement Courses (SEC)",
    "aec": "Ability Enhancement Courses (AEC)",
    "generic": "Generic",
}


def _session(db: Session | None) -> tuple[Session, bool]:
    own = db is None
    session = db if db is not None else _db()
    return session, own


def _db() -> Session:
    from app.database import SessionLocal
    return SessionLocal()


def _close(session: Session, own: bool) -> None:
    if own and session is not None:
        session.close()


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def programme_view(p: Programme) -> dict[str, Any]:
    category = p.category
    scheme = p.scheme
    return {
        "id": str(p.id),
        "name": p.name,
        "code": p.code,
        "degree_level": p.degree_level,
        "category": category.name if category else None,
        "category_id": str(category.id) if category else None,
        "level": category.level_label if category else None,
        "scheme_id": str(scheme.id) if scheme else None,
        "scheme_name": scheme.name if scheme else None,
        "scheme_code": scheme.code if scheme else None,
        "academic_scheme": (scheme.code if scheme else None) or p.academic_scheme,
        "eligibility": p.eligibility,
        "fee_structure": _fee_entries(p.fee_structure),
        "duration_years": p.duration_years,
        "total_credits": p.total_credits,
        "major_disciplines": p.major_disciplines or [],
        "description": p.description,
        "subject_count": len(p.subjects) if p.subjects else 0,
        "minor_count": len(p.minor_disciplines) if p.minor_disciplines else 0,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _fee_entries(value: Any) -> list[dict[str, str]]:
    """Normalise the fee_structure column (JSON list or legacy text) to entries."""
    if value is None:
        return []
    if isinstance(value, list):
        return [{"label": str(e.get("label") or "-"), "value": str(e.get("value") or "")} for e in value if isinstance(e, dict)]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [{"label": str(e.get("label") or "-"), "value": str(e.get("value") or "")} for e in parsed if isinstance(e, dict)]
        except (ValueError, TypeError):
            pass
        return [{"label": f"Line {i + 1}", "value": line.strip()} for i, line in enumerate(text.splitlines()) if line.strip()]
    return []


def subject_view(s: ProgrammeSubject) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "programme_id": str(s.programme_id) if s.programme_id else None,
        "minor_discipline_id": str(s.minor_discipline_id) if s.minor_discipline_id else None,
        "minor_discipline": s.minor_discipline.name if s.minor_discipline else None,
        "category": s.category,
        "category_label": CATEGORY_LABELS.get(s.category, s.category),
        "semester": s.semester,
        "subject_code": s.subject_code,
        "subject_name": s.subject_name,
        "credits": s.credits,
        "hours": s.hours,
    }


def minor_view(m: MinorDiscipline) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "programme_id": str(m.programme_id) if m.programme_id else None,
        "name": m.name,
        "description": m.description,
    }


def curriculum_view(cd: CurriculumDocument, doc: Any | None = None) -> dict[str, Any]:
    return {
        "id": str(cd.id),
        "programme_id": str(cd.programme_id) if cd.programme_id else None,
        "document_id": str(cd.document_id) if cd.document_id else None,
        "filename": cd.filename,
        "title": (doc.title or doc.original_filename or doc.filename) if doc is not None else None,
        "status": doc.status if doc is not None else None,
        "semester": cd.semester,
        "uploaded_at": cd.uploaded_at.isoformat() if cd.uploaded_at else None,
    }


def scheme_view(s: AcademicScheme, programme_count: int = 0) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "name": s.name,
        "code": s.code,
        "description": s.description,
        "sort_order": s.sort_order,
        "is_active": bool(s.is_active),
        "programme_count": programme_count,
    }


# ---------------------------------------------------------------------------
# Public queries (orchestrator + admin UI)
# ---------------------------------------------------------------------------

# Common ways users refer to schemes without typing the stored name/code.
_SCHEME_KEYWORDS: dict[str, list[str]] = {
    "nep2020": ["nep", "nep 2020", "nep2020", "nep-2020", "new education policy",
               "national education policy", "national education policy 2020",
               "fyugp", "fygup", "four year undergraduate programme",
               "four-year undergraduate programme"],
    "traditional": ["traditional", "conventional", "old curriculum", "old scheme",
                    "legacy", "cbcs", "choice based credit system", "choice-based credit system"],
}


def _scheme_aliases(scheme: AcademicScheme) -> list[str]:
    code = (scheme.code or "").lower()
    names = [scheme.name.lower()]
    return names + _SCHEME_KEYWORDS.get(code, [])


def list_academic_schemes(db: Session | None = None) -> list[dict[str, Any]]:
    session, own = _session(db)
    try:
        rows = session.query(AcademicScheme).order_by(AcademicScheme.sort_order, AcademicScheme.name).all()
        counts = {
            str(p): n for n, p in (
                session.query(func.count(Programme.id), Programme.scheme_id)
                .group_by(Programme.scheme_id)
                .all()
            ) if p is not None
        }
        return [scheme_view(s, programme_count=counts.get(str(s.id), 0)) for s in rows]
    finally:
        _close(session, own)


def academic_scheme_by_id(scheme_id: str, db: Session | None = None) -> dict[str, Any] | None:
    try:
        uid = uuid.UUID(str(scheme_id))
    except (ValueError, TypeError):
        return None
    session, own = _session(db)
    try:
        s = session.query(AcademicScheme).filter(AcademicScheme.id == uid).first()
        return scheme_view(s) if s else None
    finally:
        _close(session, own)


def resolve_academic_scheme(text: str | None, db: Session | None = None) -> dict[str, Any] | None:
    """Resolve a user-facing scheme reference (id / code / name / alias) to a row."""
    if not text or not str(text).strip():
        return None
    norm = " ".join(str(text).strip().lower().split())

    try:
        _ = uuid.UUID(norm)  # valid id syntax -> delegate to by-id lookup
        return academic_scheme_by_id(norm, db=db)
    except (ValueError, TypeError):
        pass

    session, own = _session(db)
    try:
        schemes = session.query(AcademicScheme).order_by(AcademicScheme.sort_order).all()
        for s in schemes:
            if (s.code or "").lower() == norm or (s.name or "").lower() == norm:
                return scheme_view(s)
        for s in schemes:
            aliases = _scheme_aliases(s)
            if any(alias == norm for alias in aliases):
                return scheme_view(s)
            for alias in sorted(aliases, key=len, reverse=True):
                if len(alias) >= 3 and re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", norm):
                    return scheme_view(s)
        return None
    finally:
        _close(session, own)


def has_schemes(db: Session | None = None) -> bool:
    session, own = _session(db)
    try:
        return session.query(AcademicScheme).count() > 0
    finally:
        _close(session, own)


def list_categories(db: Session | None = None) -> list[dict[str, Any]]:
    session, own = _session(db)
    try:
        rows = session.query(ProgrammeCategory).order_by(ProgrammeCategory.sort_order).all()
        return [
            {
                "id": str(c.id),
                "name": c.name,
                "level_label": c.level_label,
                "sort_order": c.sort_order,
            }
            for c in rows
        ]
    finally:
        _close(session, own)


def list_programmes(
    level: str | None = None,
    scheme: str | None = None,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    """List programmes, optionally filtered by level and/or academic scheme.

    `scheme` may be a scheme UUID, code, name or alias ("nep2020", "traditional").
    Rows linked through `scheme_id` match first; legacy rows carrying only the
    denormalised `academic_scheme` string still match by code.
    """
    session, own = _session(db)
    try:
        q = session.query(Programme)
        if level:
            q = q.join(ProgrammeCategory).filter(
                func.lower(ProgrammeCategory.level_label) == str(level).lower()
            )
        if scheme:
            resolved = resolve_academic_scheme(scheme, db=session)
            if resolved:
                try:
                    uid = uuid.UUID(str(resolved["id"]))
                except (ValueError, TypeError):
                    uid = None
                if uid:
                    q = q.filter(
                        (Programme.scheme_id == uid)
                        | (Programme.academic_scheme == resolved.get("code"))
                    )
            else:
                q = q.filter(func.lower(Programme.academic_scheme) == str(scheme).lower())
        rows = q.order_by(Programme.name).all()
        return [programme_view(p) for p in rows]
    finally:
        _close(session, own)


def list_catalogue_programmes(db: Session | None = None) -> list[dict[str, Any]]:
    """All programmes in the catalogue (unsorted wrapper used by pickers)."""
    return list_programmes(level=None, scheme=None, db=db)


def has_programmes(db: Session | None = None) -> bool:
    session, own = _session(db)
    try:
        return session.query(Programme).count() > 0
    finally:
        _close(session, own)


def programme_by_id(programme_id: str, db: Session | None = None) -> dict[str, Any] | None:
    try:
        uid = uuid.UUID(str(programme_id))
    except (ValueError, TypeError):
        return None
    session, own = _session(db)
    try:
        p = session.query(Programme).filter(Programme.id == uid).first()
        return programme_view(p) if p else None
    finally:
        _close(session, own)


def resolve_programme(text: str | None, db: Session | None = None) -> dict[str, Any] | None:
    """Resolve a user-facing programme reference to a Programme row.

    Matches (in order): UUID id, full name, code, a known alias (context.py),
    a "CODE — Name" option label, then substring containment.
    """
    if not text or not str(text).strip():
        return None
    norm = " ".join(str(text).strip().lower().split())
    # "B.Com"/"B.com" vs "bcom"/"b.com" — strip punctuation for code/alias compares
    norm_plain = norm.replace(".", "").replace("-", "").lower()

    # 1) UUID id
    try:
        uid = uuid.UUID(norm)
        session, own = _session(db)
        try:
            p = session.query(Programme).filter(Programme.id == uid).first()
            return programme_view(p) if p else None
        finally:
            _close(session, own)
    except (ValueError, TypeError):
        pass

    session, own = _session(db)
    try:
        # 2) exact full name
        p = session.query(Programme).filter(func.lower(Programme.name) == norm).first()
        if p:
            return programme_view(p)
        # 3) exact code
        p = session.query(Programme).filter(func.lower(Programme.code) == norm).first()
        if not p and norm_plain != norm:
            for row in session.query(Programme).all():
                if (row.code or "").lower().replace(".", "").replace("-", "") == norm_plain:
                    p = row
                    break
        if p:
            return programme_view(p)
        # 4) known alias -> code
        alias_id = PROGRAMME_ALIASES.get(norm)
        if alias_id:
            alias_plain = str(alias_id).replace(".", "").replace("-", "").lower()
            p = session.query(Programme).filter(func.lower(Programme.code) == alias_id).first()
            if not p and alias_plain != alias_id:
                for row in session.query(Programme).all():
                    if (row.code or "").lower().replace(".", "").replace("-", "") == alias_plain:
                        p = row
                        break
            if p:
                return programme_view(p)
        # 5) "BCA — Bachelor of Computer Applications" option labels
        if " — " in norm:
            first = norm.split(" — ", 1)[0].strip()
            p = session.query(Programme).filter(func.lower(Programme.code) == first).first()
            if not p:
                first_plain = first.replace(".", "").replace("-", "").lower()
                for row in session.query(Programme).all():
                    if (row.code or "").lower().replace(".", "").replace("-", "") == first_plain:
                        p = row
                        break
            if p:
                return programme_view(p)
        # 5b) programme code or alias mentioned inline in the text
        #     (e.g. "bca major subjects", "tell me about the BCA programme")
        programmes = session.query(Programme).all()
        for code in sorted({(p.code or "").lower() for p in programmes}, key=len, reverse=True):
            if len(code) >= 2 and re.search(rf"(?<![\w]){re.escape(code)}(?![\w])", norm):
                for p in programmes:
                    if (p.code or "").lower() == code:
                        return programme_view(p)
        for alias in sorted((str(k).lower() for k in PROGRAMME_ALIASES), key=len, reverse=True):
            if len(alias) >= 3 and re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", norm):
                alias_plain = str(PROGRAMME_ALIASES[alias]).replace(".", "").replace("-", "").lower()
                for p in programmes:
                    if (p.code or "").lower().replace(".", "").replace("-", "") == alias_plain:
                        return programme_view(p)
        # 6) substring containment (guarded by length to avoid greedy matches)
        if len(norm) >= 5:
            rows = sorted(session.query(Programme).all(), key=lambda r: len(r.name or ""))
            for p in rows:
                name = (p.name or "").lower()
                if norm in name or (len(name) >= 5 and name in norm):
                    return programme_view(p)
        return None
    finally:
        _close(session, own)


def get_programme(programme: Any, db: Session | None = None) -> dict[str, Any] | None:
    """Fetch a programme by id string or resolvable reference."""
    if isinstance(programme, dict):
        return programme
    if isinstance(programme, str):
        return programme_by_id(programme, db=db) or resolve_programme(programme, db=db)
    return None


def programme_in_catalogue(text: str | None, db: Session | None = None) -> bool:
    return resolve_programme(text, db=db) is not None


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------


def get_subjects(
    programme_id: str | None = None,
    category: str | None = None,
    semester: int | None = None,
    minor: str | None = None,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    session, own = _session(db)
    try:
        q = session.query(ProgrammeSubject)
        if programme_id:
            q = q.filter(ProgrammeSubject.programme_id == uuid.UUID(str(programme_id)))
        if category:
            q = q.filter(ProgrammeSubject.category == category)
        if semester is not None:
            q = q.filter(ProgrammeSubject.semester == int(semester))
        if minor:
            q = q.join(MinorDiscipline).filter(func.lower(MinorDiscipline.name) == minor.lower())
        rows = q.order_by(
            ProgrammeSubject.semester,
            ProgrammeSubject.subject_name,
        ).all()
        return [subject_view(s) for s in rows]
    finally:
        _close(session, own)


def get_major_subjects(
    programme_id: str,
    semester: int | None = None,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    return get_subjects(programme_id=programme_id, category="major", semester=semester, db=db)


def get_semester_subjects(
    programme_id: str,
    semester: int | None = None,
    category: str | None = None,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    return get_subjects(programme_id=programme_id, semester=semester, category=category, db=db)


def get_category_subjects(
    category: str,
    programme_id: str | None = None,
    semester: int | None = None,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    return get_subjects(programme_id=programme_id, category=category, semester=semester, db=db)


def get_semesters(
    programme_id: str,
    category: str | None = None,
    db: Session | None = None,
) -> list[int]:
    session, own = _session(db)
    try:
        q = session.query(ProgrammeSubject.semester).distinct()
        if programme_id:
            q = q.filter(ProgrammeSubject.programme_id == uuid.UUID(str(programme_id)))
        if category:
            q = q.filter(ProgrammeSubject.category == category)
        sems = [r[0] for r in q.all() if r[0] is not None]
        return sorted(set(sems))
    finally:
        _close(session, own)


# ---------------------------------------------------------------------------
# Minors
# ---------------------------------------------------------------------------


def get_minor_disciplines(programme_id: str, db: Session | None = None) -> list[dict[str, Any]]:
    session, own = _session(db)
    try:
        rows = (
            session.query(MinorDiscipline)
            .filter(MinorDiscipline.programme_id == uuid.UUID(str(programme_id)))
            .order_by(MinorDiscipline.name)
            .all()
        )
        return [minor_view(m) for m in rows]
    finally:
        _close(session, own)


def get_minor_subjects(
    programme_id: str,
    minor: str | None = None,
    semester: int | None = None,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    return get_subjects(
        programme_id=programme_id,
        category="minor",
        minor=minor,
        semester=semester,
        db=db,
    )


def resolve_minor_name(programme_id: str, text: str | None, db: Session | None = None) -> str | None:
    """Resolve a user's minor selection (id, exact name, or substring) to a name."""
    if not text or not str(text).strip():
        return None
    norm = " ".join(str(text).strip().lower().split())
    minors = get_minor_disciplines(programme_id, db=db)
    for m in minors:
        if str(m["id"]) == norm:
            return m["name"]
    for m in minors:
        name = (m["name"] or "").lower()
        if name == norm or (name and (norm in name or name in norm)):
            return m["name"]
    return None


def find_curriculum_document(
    programme_id: str,
    text: str | None,
    db: Session | None = None,
) -> dict[str, Any] | None:
    """Resolve a curriculum-document reference (id/document_id/filename)."""
    if not text or not str(text).strip():
        return None
    norm = str(text).strip()
    docs = get_curriculum_documents(programme_id, db=db)
    for doc in docs:
        if norm in (str(doc.get("id")), str(doc.get("document_id"))):
            return doc
    needle = norm.lower()
    for doc in docs:
        filename = (doc.get("filename") or "").lower()
        if filename and (needle in filename or filename in needle):
            return doc
    return None


# ---------------------------------------------------------------------------
# Outcomes / curriculum
# ---------------------------------------------------------------------------


def get_learning_outcomes(programme_id: str, db: Session | None = None) -> list[str]:
    session, own = _session(db)
    try:
        rows = (
            session.query(LearningOutcome)
            .filter(LearningOutcome.programme_id == uuid.UUID(str(programme_id)))
            .order_by(LearningOutcome.position)
            .all()
        )
        return [r.outcome_text for r in rows]
    finally:
        _close(session, own)


def get_curriculum_documents(programme_id: str, db: Session | None = None) -> list[dict[str, Any]]:
    session, own = _session(db)
    try:
        rows = (
            session.query(CurriculumDocument)
            .filter(CurriculumDocument.programme_id == uuid.UUID(str(programme_id)))
            .order_by(CurriculumDocument.uploaded_at.desc())
            .all()
        )
        out: list[dict[str, Any]] = []
        for cd in rows:
            linked = None
            if cd.document_id:
                try:
                    from app.models import Document
                    linked = session.query(Document).filter(Document.id == cd.document_id).first()
                except Exception:
                    linked = None
            out.append(curriculum_view(cd, linked))
        return out
    finally:
        _close(session, own)


# ---------------------------------------------------------------------------
# Admin CRUD — Categories
# ---------------------------------------------------------------------------


def create_category(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    cat = ProgrammeCategory(
        name=str(data["name"]).strip(),
        level_label=str(data.get("level_label") or "ug"),
        sort_order=int(data.get("sort_order") or 0),
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return {"id": str(cat.id), "name": cat.name, "level_label": cat.level_label, "sort_order": cat.sort_order}


def update_category(db: Session, category_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    cat = db.query(ProgrammeCategory).filter(ProgrammeCategory.id == _as_uuid(category_id)).first()
    if not cat:
        return None
    if "name" in data and data["name"]:
        cat.name = str(data["name"]).strip()
    if "level_label" in data:
        cat.level_label = str(data["level_label"] or "ug")
    if "sort_order" in data:
        cat.sort_order = int(data["sort_order"] or 0)
    db.commit()
    db.refresh(cat)
    return {"id": str(cat.id), "name": cat.name, "level_label": cat.level_label, "sort_order": cat.sort_order}


def delete_category(db: Session, category_id: str) -> bool:
    cat = db.query(ProgrammeCategory).filter(ProgrammeCategory.id == _as_uuid(category_id)).first()
    if not cat:
        return False
    db.delete(cat)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Admin CRUD — Programmes
# ---------------------------------------------------------------------------


def create_programme(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    scheme_id = _as_uuid(data.get("scheme_id")) if data.get("scheme_id") else None
    if scheme_id is None and data.get("academic_scheme"):
        resolved = resolve_academic_scheme(str(data["academic_scheme"]), db=db)
        scheme_id = _as_uuid(resolved["id"]) if resolved else None
    prog = Programme(
        name=str(data["name"]).strip(),
        code=str(data["code"]).strip(),
        degree_level=data.get("degree_level") or None,
        scheme_id=scheme_id,
        academic_scheme=data.get("academic_scheme") or None,
        eligibility=data.get("eligibility") or None,
        fee_structure=data.get("fee_structure") or None,
        duration_years=data.get("duration_years"),
        total_credits=data.get("total_credits"),
        description=data.get("description") or None,
        major_disciplines=data.get("major_disciplines") or [],
        category_id=_as_uuid(data.get("category_id")) if data.get("category_id") else None,
    )
    db.add(prog)
    db.commit()
    db.refresh(prog)
    return programme_view(prog)


def update_programme(db: Session, programme_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    prog = db.query(Programme).filter(Programme.id == _as_uuid(programme_id)).first()
    if not prog:
        return None
    if "name" in data and data["name"]:
        prog.name = str(data["name"]).strip()
    if "code" in data and data["code"]:
        prog.code = str(data["code"]).strip()
    if "category_id" in data:
        prog.category_id = _as_uuid(data["category_id"]) if data["category_id"] else None
    if "degree_level" in data:
        prog.degree_level = data["degree_level"] or None
    if "scheme_id" in data:
        prog.scheme_id = _as_uuid(data["scheme_id"]) if data["scheme_id"] else None
    if "academic_scheme" in data:
        prog.academic_scheme = data["academic_scheme"] or None
    if "eligibility" in data:
        prog.eligibility = data["eligibility"] or None
    if "fee_structure" in data:
        prog.fee_structure = data["fee_structure"] or None
    if "duration_years" in data:
        prog.duration_years = data["duration_years"]
    if "total_credits" in data:
        prog.total_credits = data["total_credits"]
    if "description" in data:
        prog.description = data["description"] or None
    if "major_disciplines" in data:
        prog.major_disciplines = data["major_disciplines"] or []
    db.commit()
    db.refresh(prog)
    return programme_view(prog)


def delete_programme(db: Session, programme_id: str) -> bool:
    prog = db.query(Programme).filter(Programme.id == _as_uuid(programme_id)).first()
    if not prog:
        return False
    db.delete(prog)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Admin CRUD — Academic Schemes
# ---------------------------------------------------------------------------


def create_academic_scheme(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    scheme = AcademicScheme(
        name=str(data["name"]).strip(),
        code=str(data.get("code") or "").strip().lower() or uuid.uuid4().hex[:8],
        description=data.get("description") or None,
        sort_order=int(data.get("sort_order") or 0),
        is_active=bool(data.get("is_active", True)),
    )
    db.add(scheme)
    db.commit()
    db.refresh(scheme)
    return scheme_view(scheme)


def update_academic_scheme(db: Session, scheme_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    scheme = db.query(AcademicScheme).filter(AcademicScheme.id == _as_uuid(scheme_id)).first()
    if not scheme:
        return None
    if "name" in data and data["name"]:
        scheme.name = str(data["name"]).strip()
    if "code" in data and data["code"]:
        scheme.code = str(data["code"]).strip().lower()
    if "description" in data:
        scheme.description = data["description"] or None
    if "sort_order" in data:
        scheme.sort_order = int(data["sort_order"] or 0)
    if "is_active" in data:
        scheme.is_active = bool(data["is_active"])
    db.commit()
    db.refresh(scheme)
    return scheme_view(scheme)


def delete_academic_scheme(db: Session, scheme_id: str) -> bool:
    """Delete a scheme. Refuses when programmes are linked to it."""
    scheme = db.query(AcademicScheme).filter(AcademicScheme.id == _as_uuid(scheme_id)).first()
    if not scheme:
        return False
    linked = db.query(Programme).filter(Programme.scheme_id == scheme.id).count()
    if linked:
        raise ValueError(f"Scheme is referenced by {linked} programme(s)")
    db.delete(scheme)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Admin CRUD — Subjects
# ---------------------------------------------------------------------------


def add_subject(db: Session, programme_id: str | None, data: dict[str, Any]) -> dict[str, Any]:
    subject = ProgrammeSubject(
        programme_id=_as_uuid(programme_id) if programme_id else None,
        minor_discipline_id=_as_uuid(data.get("minor_discipline_id")) if data.get("minor_discipline_id") else None,
        category=str(data.get("category") or "major"),
        semester=int(data["semester"]) if data.get("semester") is not None else None,
        subject_code=data.get("subject_code") or None,
        subject_name=str(data["subject_name"]).strip(),
        credits=int(data["credits"]) if data.get("credits") is not None else None,
        hours=int(data["hours"]) if data.get("hours") is not None else None,
    )
    db.add(subject)
    db.commit()
    db.refresh(subject)
    return subject_view(subject)


def update_subject(db: Session, subject_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    s = db.query(ProgrammeSubject).filter(ProgrammeSubject.id == _as_uuid(subject_id)).first()
    if not s:
        return None
    if "category" in data:
        s.category = str(data["category"] or "major")
    if "semester" in data:
        s.semester = int(data["semester"]) if data["semester"] is not None else None
    if "subject_code" in data:
        s.subject_code = data["subject_code"] or None
    if "subject_name" in data and data["subject_name"]:
        s.subject_name = str(data["subject_name"]).strip()
    if "credits" in data:
        s.credits = int(data["credits"]) if data["credits"] is not None else None
    if "hours" in data:
        s.hours = int(data["hours"]) if data["hours"] is not None else None
    if "minor_discipline_id" in data:
        s.minor_discipline_id = _as_uuid(data.get("minor_discipline_id")) if data.get("minor_discipline_id") else None
    db.commit()
    db.refresh(s)
    return subject_view(s)


def delete_subject(db: Session, subject_id: str) -> bool:
    s = db.query(ProgrammeSubject).filter(ProgrammeSubject.id == _as_uuid(subject_id)).first()
    if not s:
        return False
    db.delete(s)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Admin CRUD — Minors
# ---------------------------------------------------------------------------


def add_minor(db: Session, programme_id: str, data: dict[str, Any]) -> dict[str, Any]:
    m = MinorDiscipline(
        programme_id=_as_uuid(programme_id),
        name=str(data["name"]).strip(),
        description=data.get("description") or None,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return minor_view(m)


def update_minor(db: Session, minor_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    m = db.query(MinorDiscipline).filter(MinorDiscipline.id == _as_uuid(minor_id)).first()
    if not m:
        return None
    if "name" in data and data["name"]:
        m.name = str(data["name"]).strip()
    if "description" in data:
        m.description = data["description"] or None
    db.commit()
    db.refresh(m)
    return minor_view(m)


def delete_minor(db: Session, minor_id: str) -> bool:
    m = db.query(MinorDiscipline).filter(MinorDiscipline.id == _as_uuid(minor_id)).first()
    if not m:
        return False
    db.delete(m)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Admin CRUD — Learning Outcomes
# ---------------------------------------------------------------------------


def replace_outcomes(db: Session, programme_id: str, texts: list[str]) -> list[str]:
    uid = _as_uuid(programme_id)
    if not uid:
        raise ValueError("invalid programme id")
    db.query(LearningOutcome).filter(LearningOutcome.programme_id == uid).delete()
    for i, text in enumerate(texts or []):
        text = str(text or "").strip()
        if text:
            db.add(LearningOutcome(programme_id=uid, outcome_text=text, position=i))
    db.commit()
    return get_learning_outcomes(programme_id, db=db)


# ---------------------------------------------------------------------------
# Admin CRUD — Curriculum documents
# ---------------------------------------------------------------------------


def add_curriculum_document(
    db: Session,
    programme_id: str,
    document_id: str | None,
    filename: str,
    semester: int | None = None,
) -> dict[str, Any]:
    cd = CurriculumDocument(
        programme_id=_as_uuid(programme_id),
        document_id=_as_uuid(document_id) if document_id else None,
        filename=filename,
        semester=semester,
    )
    db.add(cd)
    db.commit()
    db.refresh(cd)
    return curriculum_view(cd)


def delete_curriculum_document(db: Session, curdoc_id: str) -> bool:
    cd = db.query(CurriculumDocument).filter(CurriculumDocument.id == _as_uuid(curdoc_id)).first()
    if not cd:
        return False
    db.delete(cd)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Admin CRUD — Curriculum uploads (draft -> review -> publish lifecycle)
# ---------------------------------------------------------------------------

_CUR_EXT = ("pdf", "docx", "doc", "xlsx", "xls", "csv")


def _curriculum_storage() -> Any:
    from pathlib import Path as _Path

    from app.config import settings

    base = _Path(settings.CHROMA_PERSIST_DIR).parent / "uploads"
    curdir = base / "curriculum"
    curdir.mkdir(parents=True, exist_ok=True)
    return curdir


def curriculum_upload_view(u: CurriculumUpload, doc: Any | None = None) -> dict[str, Any]:
    prog = u.programme
    scheme = u.scheme
    return {
        "id": str(u.id),
        "programme_id": str(u.programme_id) if u.programme_id else None,
        "programme_name": u.programme_name or (prog.name if prog else None),
        "programme_code": u.programme_code or (prog.code if prog else None),
        "scheme_id": str(u.scheme_id) if u.scheme_id else None,
        "scheme_name": u.scheme_name or (scheme.name if scheme else None),
        "scheme_code": u.scheme_code or (scheme.code if scheme else None),
        "document_id": str(u.document_id) if u.document_id else None,
        "filename": u.filename,
        "stored_filename": u.stored_filename,
        "file_type": u.file_type,
        "file_size": u.file_size,
        "sha256": u.sha256,
        "version": u.version,
        "revision": u.revision,
        "academic_session": u.academic_session,
        "level": u.level,
        "status": u.status,
        "parse_status": u.parse_status,
        "warnings": u.warnings or [],
        "payload": u.payload or {},
        "uploaded_by": str(u.uploaded_by) if u.uploaded_by else None,
        "uploaded_at": u.uploaded_at.isoformat() if u.uploaded_at else None,
        "published_at": u.published_at.isoformat() if u.published_at else None,
        "updated_at": u.updated_at.isoformat() if u.updated_at else None,
        "rag_status": (doc.status if doc is not None else None),
        "rag_error": (doc.error if doc is not None else None),
    }


def _hash_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def check_upload_duplicate(
    db: Session,
    sha256: str,
    programme_code: str | None,
) -> dict[str, Any] | None:
    """Find an existing upload with the same content hash (same programme only).

    A file is a true duplicate only if the same bytes were already uploaded and
    linked to the same programme — otherwise admins can choose Replace/Skip/Keep
    Both in the UI anyway.
    """
    if not sha256:
        return None
    q = db.query(CurriculumUpload).filter(CurriculumUpload.sha256 == sha256)
    if programme_code:
        q = q.filter(
            func.lower(func.coalesce(CurriculumUpload.programme_code, ""))
            == str(programme_code).lower().strip(" .-")
        )
    row = q.order_by(CurriculumUpload.uploaded_at.desc()).first()
    return curriculum_upload_view(row) if row else None


def find_upload_row(db: Session, upload_id: str) -> CurriculumUpload | None:
    try:
        uid = uuid.UUID(str(upload_id))
    except (ValueError, TypeError):
        return None
    return db.query(CurriculumUpload).filter(CurriculumUpload.id == uid).first()


def save_curriculum_upload(
    db: Session,
    file_bytes: bytes,
    original_filename: str,
    uploaded_by: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store the file, run the parser, and create a `draft` CurriculumUpload row.

    File bytes are written to the uploads/curriculum dir; never trusted from
    the client. Parsing failures set parse_status="failed" (still draft).
    """
    from app.catalogue.parser.detect import detect_level, detect_programme, detect_scheme
    from app.catalogue.parser.extract import extract_curriculum
    from app.catalogue.parser.readers import (
        CUR_EXTENSIONS,
        FormatNotSupportedError,
        read_curriculum_document,
    )
    from app.utils.files import sanitize_filename

    import datetime as _dt

    original = str(original_filename or "")
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in CUR_EXTENSIONS:
        raise ValueError(
            f"Unsupported curriculum format '.{ext}'. Allowed: {', '.join(CUR_EXTENSIONS)}"
        )
    if not file_bytes:
        raise ValueError("Empty file")
    max_bytes = 25 * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise ValueError("File too large (max 25 MB)")

    stored_name = sanitize_filename(original)
    storage_dir = _curriculum_storage()
    dest = storage_dir / stored_name
    dest.write_bytes(file_bytes)
    dest_path = str(dest)

    try:
        dt = read_curriculum_document(dest_path)
        pages = dt.pages
        tables = dt.tables
        warnings = list(dt.warnings)
        parse_ok = True
    except FormatNotSupportedError as exc:
        pages, tables, warnings, parse_ok = [], [], [str(exc)], False
    except Exception as exc:  # pragma: no cover - malformed file
        pages, tables, warnings, parse_ok = [], [], [f"Parsing failed: {exc}"], False

    text = "\n".join((p.get("text") or "") for p in pages)
    hints: dict[str, Any] = {}
    if metadata:
        hints["scheme"] = metadata.get("academic_scheme") or metadata.get("scheme")
        hints["programme_name"] = metadata.get("programme_name")
        hints["programme_code"] = metadata.get("programme_code")
        hints["level"] = metadata.get("level")
        hints["description"] = metadata.get("description")
        hints["title"] = metadata.get("title")

    if parse_ok:
        try:
            scheme_detect = detect_scheme(text, db=db)
            prog_detect = detect_programme(text, db=db)
            hints["scheme"] = hints.get("scheme") or (scheme_detect if scheme_detect.get("matched") else None)
            hints["programme_name"] = hints.get("programme_name") or prog_detect.get("name")
            hints["programme_code"] = hints.get("programme_code") or prog_detect.get("code")
            hints["level"] = hints.get("level") or prog_detect.get("level")
            if prog_detect.get("existing"):
                try:
                    hints["programme_id"] = str(prog_detect["existing"])
                except Exception:
                    pass
            payload = extract_curriculum(pages, tables=tables, hints=hints)
            warnings.extend(payload.get("warnings") or [])
            payload_ok = True
        except Exception as exc:  # pragma: no cover
            payload = {"programme": {}, "semesters": [], "minors": [],
                       "outcomes": [], "warnings": [f"Extraction failed: {exc}"]}
            parse_ok, payload_ok = False, False
    else:
        payload = {"programme": {}, "semesters": [], "minors": [],
                   "outcomes": [], "warnings": warnings}
        payload_ok = False

    prog = payload.get("programme") or {}
    scheme = payload.get("scheme") or {}
    upload = CurriculumUpload(
        filename=original,
        stored_filename=stored_name,
        file_type=ext,
        file_size=len(file_bytes),
        sha256=_hash_bytes(file_bytes),
        status="draft",
        parse_status="ok" if parse_ok and payload_ok else "partial",
        warnings=warnings[:40],
        payload=payload,
        programme_name=prog.get("name"),
        programme_code=prog.get("code"),
        scheme_name=scheme.get("name") if isinstance(scheme, dict) else None,
        scheme_code=scheme.get("code") if isinstance(scheme, dict) else None,
        level=prog.get("level"),
        academic_session=payload.get("academic_session"),
        revision=payload.get("revision"),
        uploaded_by=_as_uuid(uploaded_by) if uploaded_by else None,
        uploaded_at=_dt.datetime.now(_dt.timezone.utc),
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return curriculum_upload_view(upload)


def get_curriculum_uploads(
    db: Session,
    programme_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    q = db.query(CurriculumUpload)
    if programme_id:
        q = q.filter(CurriculumUpload.programme_id == _as_uuid(programme_id))
    if status:
        q = q.filter(CurriculumUpload.status == status)
    rows = q.order_by(CurriculumUpload.uploaded_at.desc()).all()
    out = []
    for u in rows:
        linked = None
        if u.document_id:
            try:
                from app.models import Document
                linked = db.query(Document).filter(Document.id == u.document_id).first()
            except Exception:
                linked = None
        out.append(curriculum_upload_view(u, doc=linked))
    return out


def get_curriculum_upload(db: Session, upload_id: str) -> dict[str, Any] | None:
    u = find_upload_row(db, upload_id)
    if not u:
        return None
    linked = None
    if u.document_id:
        try:
            from app.models import Document
            linked = db.query(Document).filter(Document.id == u.document_id).first()
        except Exception:
            linked = None
    return curriculum_upload_view(u, doc=linked)


def update_curriculum_upload(
    db: Session,
    upload_id: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """Rewrite the editable review fields of an upload (draft workflow gate)."""
    u = find_upload_row(db, upload_id)
    if not u:
        return None
    was_active = u.status == "active"
    if "payload" in data and isinstance(data["payload"], dict):
        u.payload = data["payload"]
        prog = data["payload"].get("programme") or {}
        scheme = data["payload"].get("scheme") or {}
        if isinstance(scheme, dict):
            u.scheme_name = scheme.get("name")
            u.scheme_code = scheme.get("code")
        u.programme_name = prog.get("name") or u.programme_name
        u.programme_code = prog.get("code") or u.programme_code
        u.level = prog.get("level") or u.level
        u.academic_session = data["payload"].get("academic_session") or u.academic_session
        u.revision = data["payload"].get("revision") or u.revision
        u.warnings = data["payload"].get("warnings") or u.warnings or []
        u.parse_status = "ok"
    for field in ("programme_name", "programme_code", "level", "revision",
                  "academic_session", "scheme_name", "scheme_code"):
        if field in data:
            setattr(u, field, data[field])
    if "scheme_id" in data:
        u.scheme_id = _as_uuid(data["scheme_id"]) if data["scheme_id"] else None
    if "programme_id" in data:
        u.programme_id = _as_uuid(data["programme_id"]) if data["programme_id"] else None
    db.commit()
    db.refresh(u)
    # Editing an already-published curriculum must keep the live copy in sync —
    # re-materialize the payload so the structured rows and RAG vectors are
    # rebuilt from the edited payload. If that fails, drop the upload back to
    # draft so it is never silently out-of-date.
    if was_active:
        try:
            apply_curriculum_payload(db, u)
        except Exception as exc:
            _log.error("Re-materialization failed for %s: %s", u.id, exc)
            db.rollback()
            u = find_upload_row(db, upload_id)
            if u is not None:
                u.status = "draft"
                db.commit()
            if u is not None:
                return curriculum_upload_view(u)
            return None
    return curriculum_upload_view(u)


def publish_curriculum_upload(db: Session, upload_id: str) -> dict[str, Any] | None:
    """Publish a reviewed upload → Active (one Active per programme enforced).

    On publish the structured payload is materialized into the catalogue DB
    (Programme / ProgrammeSubject / MinorDiscipline / LearningOutcome /
    CurriculumDocument) so the uploaded curriculum becomes the primary
    academic source, and a RAG Document with metadata is indexed so generic
    retrieval can also surface it.
    """
    u = find_upload_row(db, upload_id)
    if not u:
        return None
    if u.status == "active":
        return curriculum_upload_view(u)

    # Materialize the structured payload FIRST — the upload only becomes the
    # active primary source if the materialization actually succeeded. The
    # previous active version(s) stay live until the new one is ready.
    try:
        apply_curriculum_payload(db, u)
    except ValueError:
        raise
    except Exception as exc:
        _log.error("Curriculum materialization failed for %s: %s", u.id, exc)
        db.rollback()
        raise ValueError(
            "Could not materialize this curriculum (parse/embedding failure). "
            "Fix the warnings in review and try again."
        ) from exc

    # Archive the previous active version(s) for this programme.
    others = (
        db.query(CurriculumUpload)
        .filter(CurriculumUpload.id != u.id)
        .filter(CurriculumUpload.status == "active")
    )
    if u.programme_id:
        others = others.filter(CurriculumUpload.programme_id == u.programme_id)
    else:
        others = others.filter(
            func.lower(CurriculumUpload.programme_code) == (u.programme_code or "").lower()
        )
    for row in others.all():
        row.status = "archived"
        _archive_upload_rag(db, row)
    u.status = "active"
    import datetime as _dt
    u.published_at = _dt.datetime.now(_dt.timezone.utc)
    db.commit()
    db.refresh(u)
    return curriculum_upload_view(u, doc=_document_row(db, u))


def activate_curriculum_upload(db: Session, upload_id: str) -> dict[str, Any] | None:
    return publish_curriculum_upload(db, upload_id)


def archive_curriculum_upload(db: Session, upload_id: str) -> dict[str, Any] | None:
    u = find_upload_row(db, upload_id)
    if not u:
        return None
    u.status = "archived"
    db.commit()
    db.refresh(u)
    return curriculum_upload_view(u)


def delete_curriculum_upload(db: Session, upload_id: str) -> bool:
    u = find_upload_row(db, upload_id)
    if not u:
        return False
    if u.status == "active":
        raise ValueError("Active curriculum cannot be deleted — archive it first")
    stored = u.stored_filename
    db.delete(u)
    db.commit()
    if stored:
        try:
            base = _curriculum_storage()
            (base / stored).unlink(missing_ok=True)
        except OSError:
            pass
    return True


def download_curriculum_upload(db: Session, upload_id: str) -> tuple[str, str] | None:
    """Return (stored_file_path, original_filename) for a stored upload."""
    u = find_upload_row(db, upload_id)
    if not u or not u.stored_filename:
        return None
    base = _curriculum_storage()
    path = base / u.stored_filename
    if not path.exists():
        return None
    return str(path), u.filename


# ---------------------------------------------------------------------------
# Publish materialization — uploaded curriculum as the primary academic source
# ---------------------------------------------------------------------------


def get_active_curriculum_upload(
    db: Session,
    programme_code: str | None = None,
    programme_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the Active upload for a programme, if any (primary source gate)."""
    q = db.query(CurriculumUpload).filter(CurriculumUpload.status == "active")
    if programme_id:
        q = q.filter(CurriculumUpload.programme_id == _as_uuid(programme_id))
    elif programme_code:
        q = q.filter(
            func.lower(func.coalesce(CurriculumUpload.programme_code, ""))
            == str(programme_code).lower().strip()
        )
    row = q.order_by(CurriculumUpload.published_at.desc()).first()
    return curriculum_upload_view(row) if row else None


def curriculum_subject_search(
    db: Session | None,
    programme_code: str,
    query: str,
) -> list[dict[str, Any]] | None:
    """Search the active upload's payload for subjects by code or name.

    Returns a list of matching subject dicts (with programme context), or
    None when no active upload exists.
    """
    session, own = _session(db)
    try:
        active = get_active_curriculum_upload(session, programme_code=programme_code)
        if not active:
            return None
        payload = active.get("payload") or {}
        q = str(query or "").strip().lower()
        code_q = None
        if q:
            m = re.search(r"([A-Z]{1,5}\s*[-/]?\s*\d{2,5})", q, re.IGNORECASE)
            if m:
                code_q = re.sub(r"\s+", "", m.group(1)).upper()
        hits: list[dict[str, Any]] = []
        for sem in payload.get("semesters") or []:
            for s in sem.get("subjects") or []:
                code = str(s.get("code") or "").strip()
                name = str(s.get("name") or "").strip()
                if code_q:
                    if code_q == re.sub(r"\s+", "", code).upper():
                        hit = dict(s)
                        hit["_semester"] = sem.get("number")
                        hits.append(hit)
                elif q and (q in code.lower() or q in name.lower()):
                    hit = dict(s)
                    hit["_semester"] = sem.get("number")
                    hits.append(hit)
        return hits if hits else None
    finally:
        _close(session, own)


def apply_curriculum_payload(db: Session, upload: CurriculumUpload) -> dict[str, Any]:
    """Materialize a published upload's payload into the structured catalogue.

    Upserts (by subject code + name) so existing IDs and relationships are
    preserved; only changed fields are written. Also indexes a RAG Document
    with metadata so generic retrieval can surface the uploaded curriculum.
    """
    payload = upload.payload or {}
    prog_block = payload.get("programme") or {}
    pname = prog_block.get("name") or upload.programme_name
    pcode = prog_block.get("code") or upload.programme_code
    if not pname and not pcode:
        raise ValueError("Published upload has no programme name/code — cannot materialize")

    programme = _resolve_or_create_programme(db, pcode, pname, prog_block, upload)
    _apply_programme_fields(db, programme, prog_block, upload)

    _apply_semesters(db, programme.id, payload.get("semesters") or [])
    _apply_minors(db, programme.id, payload.get("minors") or [], payload.get("semesters") or [])
    _apply_outcomes(db, programme.id, payload.get("outcomes") or [])

    doc_id = _index_upload_rag(db, upload, programme, payload)
    upload.programme_id = programme.id
    upload.programme_code = programme.code
    upload.programme_name = programme.name
    if doc_id:
        upload.document_id = doc_id
    db.commit()
    db.refresh(upload)
    return {
        "programme_id": str(programme.id),
        "programme_code": programme.code,
        "subjects": db.query(ProgrammeSubject)
        .filter(ProgrammeSubject.programme_id == programme.id)
        .count(),
        "document_id": str(doc_id) if doc_id else None,
    }


def _resolve_or_create_programme(
    db: Session,
    code: str | None,
    name: str | None,
    block: dict[str, Any],
    upload: CurriculumUpload,
) -> Programme:
    prog: Programme | None = None
    if code:
        prog = db.query(Programme).filter(func.lower(Programme.code) == str(code).strip().lower()).first()
    if prog is None and name:
        prog = db.query(Programme).filter(func.lower(Programme.name) == str(name).strip().lower()).first()
    if prog is None:
        prog = Programme(
            name=name or f"{code} Programme",
            code=(code or _safe_code(name)),
        )
        db.add(prog)
        db.flush()
    if code and not prog.code:
        prog.code = code
    return prog


def _safe_code(name: str | None) -> str:
    base = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    return (base[:20] + "_" + uuid.uuid4().hex[:6]) if base else "prog_" + uuid.uuid4().hex[:6]


def _apply_programme_fields(
    db: Session,
    prog: Programme,
    block: dict[str, Any],
    upload: CurriculumUpload,
) -> None:
    """Write only the changed payload fields (preserving untouched ones)."""
    level = upload.level or block.get("level")
    if level and level in ("ug", "pg", "phd", "integrated"):
        category = (
            db.query(ProgrammeCategory).filter(ProgrammeCategory.level_label == level).first()
        )
        if category:
            prog.category_id = category.id
    if block.get("duration_years") is not None:
        prog.duration_years = int(block["duration_years"] or 0) or None
    if block.get("total_credits") is not None:
        prog.total_credits = int(block["total_credits"] or 0) or None
    if block.get("eligibility"):
        prog.eligibility = str(block["eligibility"]).strip()
    if block.get("fee_structure"):
        prog.fee_structure = block["fee_structure"]
    if block.get("major_disciplines"):
        prog.major_disciplines = [str(d) for d in block["major_disciplines"]]
    if block.get("description"):
        prog.description = str(block["description"]).strip()

    if upload.scheme_id:
        prog.scheme_id = upload.scheme_id
    else:
        scheme = resolve_academic_scheme(upload.scheme_code or upload.scheme_name or "", db=db)
        if scheme:
            prog.scheme_id = _as_uuid(scheme["id"])
    if upload.level:
        prog.academic_scheme = upload.scheme_code or prog.academic_scheme


def _apply_semesters(db: Session, programme_id, semesters: list[Any]) -> None:
    """Upsert ProgrammeSubject rows by programme+category+semester+name (ID-preserving)."""
    _as_uuid(programme_id)
    for sem in semesters:
        sem_no = _to_int(sem.get("number"))
        for s in sem.get("subjects") or []:
            sname = (s.get("name") or "").strip()
            if not sname:
                continue
            cat = (s.get("category") or "major").strip().lower()
            existing = (
                db.query(ProgrammeSubject)
                .filter(
                    ProgrammeSubject.programme_id == _as_uuid(programme_id),
                    func.lower(ProgrammeSubject.subject_name) == sname.lower(),
                    ProgrammeSubject.semester == sem_no,
                    ProgrammeSubject.category == cat,
                )
                .first()
            )
            if existing is None:
                existing = ProgrammeSubject(
                    programme_id=_as_uuid(programme_id),
                    category=cat,
                    semester=sem_no,
                    subject_name=sname,
                    subject_code=(s.get("code") or None),
                    credits=_to_int(s.get("credits")),
                    hours=_to_int(s.get("hours")),
                )
                db.add(existing)
            else:
                if s.get("code") and not existing.subject_code:
                    existing.subject_code = s.get("code")
                if s.get("credits") is not None:
                    existing.credits = _to_int(s.get("credits"))
                if s.get("hours") is not None:
                    existing.hours = _to_int(s.get("hours"))
    db.flush()


def _apply_minors(
    db: Session,
    programme_id,
    minors: list[Any],
    semesters: list[Any],
) -> None:
    """Upsert MinorDiscipline rows by name and link minor subjects to them."""
    from app.catalogue.models import MinorDiscipline as _MD

    uid = _as_uuid(programme_id)
    for mn in minors:
        mname = (mn.get("name") or "").strip()
        if not mname:
            continue
        md = db.query(_MD).filter(_MD.programme_id == uid, func.lower(_MD.name) == mname.lower()).first()
        if md is None:
            md = _MD(programme_id=uid, name=mname)
            db.add(md)
            db.flush()
        for s in mn.get("subjects") or []:
            sname = (s.get("name") or "").strip()
            if not sname:
                continue
            row = (
                db.query(ProgrammeSubject)
                .filter(
                    ProgrammeSubject.programme_id == uid,
                    func.lower(ProgrammeSubject.subject_name) == sname.lower(),
                )
                .first()
            )
            if row:
                row.minor_discipline_id = md.id
    # Any leftover minor-categorised semester subjects without a discipline get
    # attached to the first created discipline (payload minors share subject cells).
    first_md = (
        db.query(_MD).filter(_MD.programme_id == uid).order_by(_MD.name).first()
    )
    if first_md is not None:
        for sem in semesters:
            for s in sem.get("subjects") or []:
                if (s.get("category") or "").strip().lower() == "minor":
                    sname = (s.get("name") or "").strip()
                    if sname:
                        row = (
                            db.query(ProgrammeSubject)
                            .filter(
                                ProgrammeSubject.programme_id == uid,
                                func.lower(ProgrammeSubject.subject_name) == sname.lower(),
                                ProgrammeSubject.minor_discipline_id.is_(None),
                            )
                            .first()
                        )
                        if row:
                            row.minor_discipline_id = first_md.id
    db.flush()


def _apply_outcomes(db: Session, programme_id, outcomes: list[Any]) -> None:
    """Append new learning outcomes only (existing text keeps its ID)."""
    uid = _as_uuid(programme_id)
    existing = {
        lo.outcome_text.strip().lower()
        for lo in db.query(LearningOutcome).filter(LearningOutcome.programme_id == uid).all()
    }
    pos = (
        db.query(func.max(LearningOutcome.position))
        .filter(LearningOutcome.programme_id == uid)
        .scalar()
    ) or 0
    for text in outcomes:
        t = str(text or "").strip()
        if not t or t.lower() in existing:
            continue
        db.add(LearningOutcome(programme_id=uid, outcome_text=t[:1200], position=pos))
        pos += 1
        existing.add(t.lower())
    db.flush()


def _index_upload_rag(
    db: Session,
    upload: CurriculumUpload,
    programme: Programme,
    payload: dict[str, Any],
) -> str | None:
    """Create/refresh the linked RAG Document with metadata + structured chunks.

    Chunks carry academic_scheme / programme / semester metadata so retrieval
    filters to the right scheme. Returns the Document id (or None on failure
    with the file still usable through the structured tables).
    """
    try:
        from app.ingest.chunker import chunk_pages
        from app.ingest.embed import embed_documents
        from app.ingest.store import add_chunks_with_embeddings, delete_document as delete_document_vector
    except Exception:
        return None

    scheme = upload.scheme_code
    if not scheme and isinstance(payload.get("scheme"), dict):
        scheme = (payload["scheme"] or {}).get("code")
    scheme = scheme or None
    pages = _payload_pages(payload)
    chunks = chunk_pages(pages)
    if not chunks:
        return None

    doc = None
    if upload.document_id:
        try:
            from app.models import Document
            doc = db.query(Document).filter(Document.id == upload.document_id).first()
        except Exception:
            doc = None
    if doc is None:
        from app.models import Document
        doc = Document(
            title=payload.get("title") or f"{programme.name} Curriculum",
            filename=upload.filename or "curriculum",
            original_filename=upload.filename,
            file_type=upload.file_type or "pdf",
            file_size=upload.file_size,
            sha256=upload.sha256,
            status="processing",
            document_type="curriculum",
            programme=(programme.code or "").lower()[:50],
            academic_scheme=scheme or None,
        )
        db.add(doc)
        db.flush()
    else:
        doc.status = "processing"
        doc.document_type = "curriculum"
        doc.programme = (programme.code or "").lower()[:50]
        doc.academic_scheme = scheme or doc.academic_scheme
        db.flush()

    text_list = [c["content"] for c in chunks]
    extra = {"academic_scheme": scheme} if scheme else {}
    extra["document_type"] = "curriculum"
    extra["programme"] = (programme.code or "").lower()[:50]
    try:
        embeddings = embed_documents(text_list)
        # Remove any vectors previously linked to this document so a re-publish/
        # re-index never collides with stale chunk IDs or leaves outdated rows.
        try:
            delete_document_vector(str(doc.id))
        except Exception:
            pass
        add_chunks_with_embeddings(
            str(doc.id), doc.title, chunks, embeddings, extra_metadata=extra
        )
        doc.status = "ready"
        doc.error = None
    except Exception:
        # Ollama/embedding unavailable — keep the file row but no vectors;
        # structured catalogue tables remain the primary source. Surface the
        # degradation instead of silently reporting a clean "ready".
        doc.status = "ready"
        doc.error = "RAG indexing degraded: embeddings unavailable (structured data live)."
    doc.chunk_count = len(chunks)
    db.flush()
    return str(doc.id)


def _payload_pages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn the structured payload into a page-like text list for chunking."""
    out: list[dict[str, Any]] = []
    prog = payload.get("programme") or {}
    lines: list[str] = []
    if payload.get("title"):
        lines.append(payload["title"])
    lines.append(f"Programme: {prog.get('name') or ''} ({prog.get('code') or ''})")
    if payload.get("academic_session"):
        lines.append(f"Academic Session: {payload['academic_session']}")
    if prog.get("eligibility"):
        lines.append(f"Eligibility: {prog['eligibility']}")
    if prog.get("duration_years"):
        lines.append(f"Duration: {prog['duration_years']} years")
    if prog.get("total_credits"):
        lines.append(f"Total Credits: {prog['total_credits']}")
    if prog.get("fee_structure"):
        try:
            fee_lines = [
                f"Fee Item: {f.get('name') or ''} — {f.get('amount') or ''}"
                for f in prog["fee_structure"]
            ]
            if fee_lines:
                lines.extend(["Fee Structure:", *fee_lines])
        except (TypeError, AttributeError):
            pass
    if lines:
        out.append({"page": 1, "text": "\n".join(lines)})
    for sem in payload.get("semesters") or []:
        sem_lines = [f"Semester {sem.get('number')}"]
        for s in sem.get("subjects") or []:
            bits = [f"- {s.get('name') or ''}"]
            if s.get("code"):
                bits.append(f"[{s['code']}]")
            if s.get("category"):
                bits.append(f"({s['category']})")
            if s.get("credits") is not None:
                bits.append(f"{s['credits']} credits")
            if s.get("hours") is not None:
                bits.append(f"{s['hours']} hours")
            sem_lines.append(" ".join(bits))
        out.append({"page": len(out) + 1, "text": "\n".join(sem_lines)})
    minors = payload.get("minors") or []
    if minors:
        minor_lines = ["Minors & Specializations"]
        for m in minors:
            subs = [s.get("name") or "" for s in (m.get("subjects") or [])]
            minor_lines.append(f"- {m.get('name') or ''}" + (f": {', '.join(subs)}" if subs else ""))
        out.append({"page": len(out) + 1, "text": "\n".join(minor_lines)})
    outcomes = payload.get("outcomes") or []
    if outcomes:
        out.append({
            "page": len(out) + 1,
            "text": "Learning Outcomes\n" + "\n".join(f"- {o}" for o in outcomes if str(o or "").strip()),
        })
    return out


def _archive_upload_rag(db: Session, upload: CurriculumUpload) -> None:
    """Remove RAG vectors of a superseded upload so only the active stays primary."""
    if not upload.document_id:
        return
    try:
        from app.ingest.store import delete_document as _delete_vectors
        _delete_vectors(str(upload.document_id))
    except Exception:
        pass


def _document_row(db: Session, upload: CurriculumUpload):
    if not upload.document_id:
        return None
    try:
        from app.models import Document
        return db.query(Document).filter(Document.id == upload.document_id).first()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None


def _as_uuid(value: Any):
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))