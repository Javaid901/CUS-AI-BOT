"""
backend/app/catalogue/routes.py

Admin API for the Academic Catalogue.

  GET    /api/admin/catalogue/stats                          -> summary counts
  GET    /api/admin/catalogue/schemes                     -> list academic schemes
  POST   /api/admin/catalogue/schemes                     -> create scheme
  PUT    /api/admin/catalogue/schemes/{sid}               -> update scheme
  DELETE /api/admin/catalogue/schemes/{sid}               -> delete scheme
  GET    /api/admin/catalogue/categories                  -> list categories
  POST   /api/admin/catalogue/categories                  -> create category
  PUT    /api/admin/catalogue/categories/{cid}            -> update category
  DELETE /api/admin/catalogue/categories/{cid}            -> delete category
  GET    /api/admin/catalogue/programmes                  -> list programmes (filters)
  GET    /api/admin/catalogue/programmes/{pid}            -> programme detail
  POST   /api/admin/catalogue/programmes                  -> create programme
  PUT    /api/admin/catalogue/programmes/{pid}            -> update programme
  DELETE /api/admin/catalogue/programmes/{pid}            -> delete programme
  GET    /api/admin/catalogue/programmes/{pid}/subjects   -> list subjects
  POST   /api/admin/catalogue/programmes/{pid}/subjects   -> add subject
  PUT    /api/admin/catalogue/subjects/{sid}              -> update subject
  DELETE /api/admin/catalogue/subjects/{sid}              -> delete subject
  GET    /api/admin/catalogue/programmes/{pid}/minors     -> list minors
  POST   /api/admin/catalogue/programmes/{pid}/minors     -> add minor
  PUT    /api/admin/catalogue/minors/{mid}                -> update minor
  DELETE /api/admin/catalogue/minors/{mid}                -> delete minor
  GET    /api/admin/catalogue/programmes/{pid}/outcomes   -> list outcomes
  PUT    /api/admin/catalogue/programmes/{pid}/outcomes   -> replace outcomes
  GET    /api/admin/catalogue/programmes/{pid}/documents  -> curriculum docs
  POST   /api/admin/catalogue/programmes/{pid}/documents  -> link an indexed document
  DELETE /api/admin/catalogue/documents/{cid}             -> unlink curriculum doc
"""

from __future__ import annotations

from typing import Any

from app.auth.security import require_admin
from app.config import settings
from app.database import get_db
from app.models import Document, User
from app.utils.logging import audit
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.catalogue import service

router = APIRouter(tags=["admin-catalogue"])
_prefix = f"{settings.API_PREFIX}/admin/catalogue"
_protected = Depends(require_admin)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CategoryIn(BaseModel):
    name: str
    level_label: str = "ug"
    sort_order: int = 0


class SchemeIn(BaseModel):
    name: str
    code: str | None = None
    description: str | None = None
    sort_order: int = 0
    is_active: bool = True


class ProgrammeIn(BaseModel):
    name: str
    code: str
    category_id: str | None = None
    scheme_id: str | None = None
    degree_level: str | None = None
    academic_scheme: str | None = None
    duration_years: int | None = None
    total_credits: int | None = None
    eligibility: str | None = None
    fee_structure: list[dict[str, str]] | None = None
    major_disciplines: list[str] = []
    description: str | None = None


class ProgrammeUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    category_id: str | None = None
    scheme_id: str | None = None
    degree_level: str | None = None
    academic_scheme: str | None = None
    duration_years: int | None = None
    total_credits: int | None = None
    eligibility: str | None = None
    fee_structure: list[dict[str, str]] | None = None
    major_disciplines: list[str] | None = None
    description: str | None = None


class SubjectIn(BaseModel):
    category: str = "major"
    semester: int | None = None
    subject_code: str | None = None
    subject_name: str
    credits: int | None = None
    hours: int | None = None
    minor_discipline_id: str | None = None


class SubjectUpdate(BaseModel):
    category: str | None = None
    semester: int | None = None
    subject_code: str | None = None
    subject_name: str | None = None
    credits: int | None = None
    hours: int | None = None
    minor_discipline_id: str | None = None


class MinorIn(BaseModel):
    name: str
    description: str | None = None


class OutcomesIn(BaseModel):
    outcomes: list[str]


class CurriculumLinkIn(BaseModel):
    document_id: str


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@router.get(f"{_prefix}/stats")
def catalogue_stats(db: Session = Depends(get_db), current: User = _protected):
    stats = {
        "schemes": len(service.list_academic_schemes(db=db)),
        "categories": len(service.list_categories(db=db)),
        "programmes": len(service.list_programmes(db=db)),
        "subjects": len(service.get_subjects(db=db)),
    }
    return {"ok": True, "stats": stats}


# ---------------------------------------------------------------------------
# Academic schemes
# ---------------------------------------------------------------------------


@router.get(f"{_prefix}/schemes")
def list_schemes(db: Session = Depends(get_db), current: User = _protected):
    return service.list_academic_schemes(db=db)


@router.post(f"{_prefix}/schemes")
def create_scheme(data: SchemeIn, db: Session = Depends(get_db), current: User = _protected):
    scheme = service.create_academic_scheme(db, data.model_dump())
    audit(db, "catalogue_scheme_created", actor_id=str(current.id), actor_role=current.role, target=scheme["code"])
    return scheme


@router.put(f"{_prefix}/schemes/{{sid}}")
def update_scheme(sid: str, data: SchemeIn, db: Session = Depends(get_db), current: User = _protected):
    scheme = service.update_academic_scheme(db, sid, data.model_dump())
    if not scheme:
        raise HTTPException(status_code=404, detail="Academic scheme not found")
    return scheme


@router.delete(f"{_prefix}/schemes/{{sid}}")
def delete_scheme(sid: str, db: Session = Depends(get_db), current: User = _protected):
    try:
        deleted = service.delete_academic_scheme(db, sid)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail="Academic scheme not found")
    return {"ok": "Deleted"}


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@router.get(f"{_prefix}/categories")
def list_categories(db: Session = Depends(get_db), current: User = _protected):
    return service.list_categories(db=db)


@router.post(f"{_prefix}/categories")
def create_category(data: CategoryIn, db: Session = Depends(get_db), current: User = _protected):
    result = service.create_category(db, data.model_dump())
    audit(db, "catalogue_category_created", actor_id=str(current.id), actor_role=current.role, target=result["name"])
    return result


@router.put(f"{_prefix}/categories/{{cid}}")
def update_category(cid: str, data: CategoryIn, db: Session = Depends(get_db), current: User = _protected):
    result = service.update_category(db, cid, data.model_dump())
    if not result:
        raise HTTPException(status_code=404, detail="Category not found")
    return result


@router.delete(f"{_prefix}/categories/{{cid}}")
def delete_category(cid: str, db: Session = Depends(get_db), current: User = _protected):
    if not service.delete_category(db, cid):
        raise HTTPException(status_code=404, detail="Category not found")
    return {"message": "Deleted"}


# ---------------------------------------------------------------------------
# Programmes
# ---------------------------------------------------------------------------


@router.get(f"{_prefix}/programmes")
def list_programmes(
    level: str | None = None,
    scheme: str | None = None,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    return service.list_programmes(level=level, scheme=scheme, db=db)


@router.get(f"{_prefix}/programmes/{{pid}}")
def get_programme(pid: str, db: Session = Depends(get_db), current: User = _protected):
    prog = service.programme_by_id(pid, db=db)
    if not prog:
        raise HTTPException(status_code=404, detail="Programme not found")
    prog["subjects"] = service.get_subjects(programme_id=pid, db=db)
    prog["minors"] = service.get_minor_disciplines(pid, db=db)
    prog["outcomes"] = service.get_learning_outcomes(pid, db=db)
    prog["documents"] = service.get_curriculum_documents(pid, db=db)
    return prog


@router.post(f"{_prefix}/programmes")
def create_programme(data: ProgrammeIn, db: Session = Depends(get_db), current: User = _protected):
    prog = service.create_programme(db, data.model_dump())
    audit(db, "catalogue_programme_created", actor_id=str(current.id), actor_role=current.role, target=prog["code"])
    return prog


@router.put(f"{_prefix}/programmes/{{pid}}")
def update_programme(pid: str, data: ProgrammeUpdate, db: Session = Depends(get_db), current: User = _protected):
    payload = {k: v for k, v in data.model_dump().items() if v is not None}
    prog = service.update_programme(db, pid, payload)
    if not prog:
        raise HTTPException(status_code=404, detail="Programme not found")
    return prog


@router.delete(f"{_prefix}/programmes/{{pid}}")
def delete_programme(pid: str, db: Session = Depends(get_db), current: User = _protected):
    if not service.delete_programme(db, pid):
        raise HTTPException(status_code=404, detail="Programme not found")
    return {"ok": "Deleted"}


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------


@router.get(f"{_prefix}/programmes/{{pid}}/subjects")
def list_programme_subjects(
    pid: str,
    category: str | None = None,
    semester: int | None = None,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    return service.get_subjects(programme_id=pid, category=category, semester=semester, db=db)


@router.post(f"{_prefix}/programmes/{{pid}}/subjects")
def add_subject(pid: str, data: SubjectIn, db: Session = Depends(get_db), current: User = _protected):
    subject = service.add_subject(db, pid, data.model_dump())
    audit(db, "catalogue_subject_created", actor_id=str(current.id), actor_role=current.role, target=subject["subject_name"])
    return subject


@router.put(f"{_prefix}/subjects/{{sid}}")
def update_subject(sid: str, data: SubjectUpdate, db: Session = Depends(get_db), current: User = _protected):
    payload = {k: v for k, v in data.model_dump().items() if v is not None}
    subject = service.update_subject(db, sid, payload)
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


@router.delete(f"{_prefix}/subjects/{{sid}}")
def delete_subject(sid: str, db: Session = Depends(get_db), current: User = _protected):
    if not service.delete_subject(db, sid):
        raise HTTPException(status_code=404, detail="Subject not found")
    return {"ok": "Deleted"}


# ---------------------------------------------------------------------------
# Minors
# ---------------------------------------------------------------------------


@router.get(f"{_prefix}/programmes/{{pid}}/minors")
def list_minors(pid: str, db: Session = Depends(get_db), current: User = _protected):
    return service.get_minor_disciplines(pid, db=db)


@router.post(f"{_prefix}/programmes/{{pid}}/minors")
def add_minor(pid: str, data: MinorIn, db: Session = Depends(get_db), current: User = _protected):
    minor = service.add_minor(db, pid, data.model_dump())
    audit(db, "catalogue_minor_created", actor_id=str(current.id), actor_role=current.role, target=minor["name"])
    return minor


@router.put(f"{_prefix}/minors/{{mid}}")
def update_minor(mid: str, data: MinorIn, db: Session = Depends(get_db), current: User = _protected):
    payload = {k: v for k, v in data.model_dump().items() if v is not None}
    minor = service.update_minor(db, mid, payload)
    if not minor:
        raise HTTPException(status_code=404, detail="Minor not found")
    return minor


@router.delete(f"{_prefix}/minors/{{mid}}")
def delete_minor(mid: str, db: Session = Depends(get_db), current: User = _protected):
    if not service.delete_minor(db, mid):
        raise HTTPException(status_code=404, detail="Minor not found")
    return {"ok": "Deleted"}


# ---------------------------------------------------------------------------
# Learning outcomes
# ---------------------------------------------------------------------------


@router.get(f"{_prefix}/programmes/{{pid}}/outcomes")
def list_outcomes(pid: str, db: Session = Depends(get_db), current: User = _protected):
    return service.get_learning_outcomes(pid, db=db)


@router.put(f"{_prefix}/programmes/{{pid}}/outcomes")
def replace_outcomes(pid: str, data: OutcomesIn, db: Session = Depends(get_db), current: User = _protected):
    result = service.replace_outcomes(db, pid, data.outcomes)
    audit(db, "catalogue_outcomes_updated", actor_id=str(current.id), actor_role=current.role, target=pid)
    return result


# ---------------------------------------------------------------------------
# Curriculum documents
# ---------------------------------------------------------------------------


@router.get(f"{_prefix}/programmes/{{pid}}/documents")
def list_curriculum_documents(pid: str, db: Session = Depends(get_db), current: User = _protected):
    return service.get_curriculum_documents(pid, db=db)


@router.post(f"{_prefix}/programmes/{{pid}}/documents")
def link_curriculum_document(
    pid: str,
    data: CurriculumLinkIn,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    doc = db.query(Document).filter(Document.id == _as_uuid(data.document_id)).first() if _as_uuid(data.document_id) else None
    if not doc:
        raise HTTPException(status_code=404, detail="Parent document not found")
    result = service.add_curriculum_document(db, pid, data.document_id, filename=doc.filename or str(doc.id))
    return result


@router.delete(f"{_prefix}/documents/{{did}}")
def delete_curriculum_document(did: str, db: Session = Depends(get_db), current: User = _protected):
    if not service.delete_curriculum_document(db, did):
        raise HTTPException(status_code=404, detail="Curriculum document not found")
    return {"ok": "Deleted"}


# ---------------------------------------------------------------------------
# Curriculum uploads (draft -> review -> publish lifecycle)
# ---------------------------------------------------------------------------


class CurriculumUploadUpdate(BaseModel):
    payload: dict[str, Any] | None = None
    programme_name: str | None = None
    programme_code: str | None = None
    level: str | None = None
    revision: str | None = None
    academic_session: str | None = None
    scheme_name: str | None = None
    scheme_code: str | None = None
    scheme_id: str | None = None
    programme_id: str | None = None


@router.get(f"{_prefix}/uploads")
def list_uploads(
    status: str | None = None,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    return service.get_curriculum_uploads(db, status=status)


@router.post(f"{_prefix}/uploads")
async def upload_curriculum(
    file: UploadFile = File(...),
    programme_id: str | None = Form(None),
    programme_name: str | None = Form(None),
    programme_code: str | None = Form(None),
    level: str | None = Form(None),
    replace_duplicate: str | None = Form(None),
    db: Session = Depends(get_db),
    current: User = _protected,
):
    raw = await file.read()
    metadata: dict[str, Any] = {}
    if programme_id:
        metadata["programme_id"] = programme_id
    if programme_name:
        metadata["programme_name"] = programme_name
    if programme_code:
        metadata["programme_code"] = programme_code
    if level:
        metadata["level"] = level

    import hashlib
    sha = hashlib.sha256(raw).hexdigest()
    existing = service.check_upload_duplicate(
        db, sha, programme_code=metadata.get("programme_code")
    )
    if existing and str(replace_duplicate) != "replace":
        raise HTTPException(
            status_code=409,
            detail={
                "duplicate": True,
                "existing": existing,
                "hint": "Send replace_duplicate=replace to archive the old copy and upload anyway.",
            },
        )

    try:
        upload = service.save_curriculum_upload(
            db,
            raw,
            file.filename or "curriculum.pdf",
            uploaded_by=str(current.id),
            metadata=metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if existing and str(replace_duplicate) == "replace":
        # Only archive the superseded copy once the replacement is stored.
        service.archive_curriculum_upload(db, existing["id"])
    audit(
        db,
        "catalogue_curriculum_uploaded",
        actor_id=str(current.id),
        actor_role=current.role,
        target=upload.get("filename"),
    )
    return {"ok": True, "upload": upload}


@router.get(f"{_prefix}/uploads/{{uid}}")
def get_upload(uid: str, db: Session = Depends(get_db), current: User = _protected):
    upload = service.get_curriculum_upload(db, uid)
    if not upload:
        raise HTTPException(status_code=404, detail="Curriculum upload not found")
    return upload


@router.put(f"{_prefix}/uploads/{{uid}}")
def update_upload(
    uid: str,
    data: CurriculumUploadUpdate,
    db: Session = Depends(get_db),
    current: User = _protected,
):
    payload = {k: v for k, v in data.model_dump().items() if v is not None}
    upload = service.update_curriculum_upload(db, uid, payload)
    if not upload:
        raise HTTPException(status_code=404, detail="Curriculum upload not found")
    return upload


@router.post(f"{_prefix}/uploads/{{uid}}/publish")
def publish_upload(uid: str, db: Session = Depends(get_db), current: User = _protected):
    try:
        upload = service.publish_curriculum_upload(db, uid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not upload:
        raise HTTPException(status_code=404, detail="Curriculum upload not found")
    audit(db, "catalogue_curriculum_published", actor_id=str(current.id), actor_role=current.role, target=uid)
    return upload


@router.post(f"{_prefix}/uploads/{{uid}}/archive")
def archive_upload(uid: str, db: Session = Depends(get_db), current: User = _protected):
    upload = service.archive_curriculum_upload(db, uid)
    if not upload:
        raise HTTPException(status_code=404, detail="Curriculum upload not found")
    return upload


@router.delete(f"{_prefix}/uploads/{{uid}}")
def delete_upload(uid: str, db: Session = Depends(get_db), current: User = _protected):
    try:
        deleted = service.delete_curriculum_upload(db, uid)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail="Curriculum upload not found")
    return {"ok": "Deleted"}


@router.get(f"{_prefix}/uploads/{{uid}}/download")
def download_upload(uid: str, db: Session = Depends(get_db), current: User = _protected):
    result = service.download_curriculum_upload(db, uid)
    if not result:
        raise HTTPException(status_code=404, detail="Curriculum file not found")
    path, original = result
    return FileResponse(path, filename=original)


def _as_uuid(value: Any):
    import uuid
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None