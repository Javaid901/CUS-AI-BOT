"""
backend/app/catalogue/knowledge.py

Field-level information retrieval for the granular response assistant.

`extract_requested_fields()` maps natural-language question words ("how much",
"documents", "how long", ...) to canonical `requested_information` fields.
`resolve_information_request()` answers those fields with the most precise
published data available, in resolution order:

  1. the structured Academic Catalogue row (programme + related list rows:
     subjects, outcomes, minors, VAC/SEC/AEC pools, curriculum documents)
  2. the active uploaded & published Curriculum materialization
  3. legacy slot-fill records (fee / eligibility / duration / documents)

A field with no value in any source is returned in `missing` so the engine can
continue the same question into the hybrid RAG pipeline instead of replying
with a generic "fee data is maintained in the academic catalogue" pointer.
"""

from __future__ import annotations

import json
import re
from typing import Any

FIELD_LABELS: dict[str, str] = {
    "fee": "Fee Structure",
    "eligibility": "Eligibility",
    "duration": "Duration",
    "credits": "Total Credits",
    "scheme": "Academic Scheme",
    "subjects": "Subjects",
    "major": "Major Disciplines",
    "minor": "Minor Disciplines",
    "vac": "VAC Courses",
    "sec": "SEC Courses",
    "aec": "AEC Courses",
    "outcomes": "Learning Outcomes",
    "curriculum": "Curriculum Documents",
    "documents": "Required Documents",
}

# canonical field -> matching question phrases. Order defines field priority
# when several patterns could fire on the same text.
FIELD_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("fee", re.compile(
        r"\bfee structure\b|\bfees?\b|\btuition\b|\bprogramme fee\b|\bcourse fee\b"
        r"|\badmission fee\b|\bcost of the (programme|course)\b"
        r"|\bhow much\b|\bhow much (does|is|are|do)\b|\bcharges?\b"
        r"|\bcost\b|\bpayment\b|\bprice\b|\bexpenses?\b|\bamount\b",
        re.IGNORECASE)),
    ("eligibility", re.compile(
        r"\beligibilit(y|ies)\b|\beligible\b|\badmission criteria\b|\bcriteria for admission\b"
        r"|\bwho can apply\b|\bwho is eligible\b|\bminimum qualification\b"
        r"|\bcan i apply\b|\bcan i join\b|\bam i eligible\b|\badmission requirements?\b"
        r"|\brequirements? for (admission|joining|enrollment|enrolment)\b"
        r"|\badmission qualifications?\b",
        re.IGNORECASE)),
    ("duration", re.compile(
        r"\bdur(?:ation|ations)\b|\bhow long\b|\bhow many years\b"
        r"|\bcourse length\b|\bprogramme length\b",
        re.IGNORECASE)),
    ("credits", re.compile(
        r"\bcredits?\b|\bcredit[ -](distribution|structure|system|break[- ]?down)\b",
        re.IGNORECASE)),
    ("scheme", re.compile(
        r"\bnep\b|\bnep\s*20\d{2}\b|\bnew education policy\b|\bnational education policy\b"
        r"|\bfyugp\b|\bfygup\b|\bcbcs\b|\bchoice[- ]?based credit system\b"
        r"|\btraditional\b|\bconventional\b|\bacademic scheme\b|\bschemes?\b",
        re.IGNORECASE)),
    ("subjects", re.compile(
        r"\bsubjects?\b|\bpapers?\b|\bmodules?\b|\bcoursework\b",
        re.IGNORECASE)),
    ("major", re.compile(
        r"\bmajor subjects?\b|\bmajor disciplines?\b|\bmajors?\b",
        re.IGNORECASE)),
    ("minor", re.compile(
        r"\bminor subjects?\b|\bminor disciplines?\b|\bminors?\b",
        re.IGNORECASE)),
    ("vac", re.compile(
        r"\bvac\b|\bvalue[- ]?added courses?\b|\bvalue added\b",
        re.IGNORECASE)),
    ("sec", re.compile(
        r"\bsec\b|\bskill[- ]?enhancement courses?\b",
        re.IGNORECASE)),
    ("aec", re.compile(
        r"\baec\b|\bability[- ]?enhancement courses?\b",
        re.IGNORECASE)),
    ("outcomes", re.compile(
        r"\blearning outcomes?\b|\bprogramme outcomes?\b|\boutcomes?\b",
        re.IGNORECASE)),
    ("curriculum", re.compile(
        r"\bcurriculum\b|\bprogramme structure\b|\bcourse structure\b|\bstudy plan\b"
        r"|\bsyllabus\b|\bsyllabi\b",
        re.IGNORECASE)),
    ("documents", re.compile(
        r"\brequired documents?\b|\brequired docs?\b|\bdocuments?\b|\bpaperwork\b"
        r"|\bwhat (?:documents?|papers) (?:are|do|must)\b",
        re.IGNORECASE)),
]

_MAJOR_PHRASE = re.compile(r"\bmajor\s+(?:subjects?|disciplines?)\b", re.IGNORECASE)
_MINOR_PHRASE = re.compile(r"\bminor\s+(?:subjects?|disciplines?)\b", re.IGNORECASE)


def extract_requested_fields(text: str) -> list[str]:
    """Extract canonical requested-information fields from a question message.

    Returns an empty list when nothing field-like is being asked. "Major
    subjects" / "minor subjects" collapse to the single category listing that
    the dedicated subjects op already handles.
    """
    if not text:
        return []
    lowered = str(text).strip().lower()
    fields: list[str] = []
    for field, pattern in FIELD_PATTERNS:
        if pattern.search(lowered) and field not in fields:
            fields.append(field)
    if "major" in fields and "subjects" in fields and _MAJOR_PHRASE.search(lowered):
        fields.remove("major")
    if "minor" in fields and "subjects" in fields and _MINOR_PHRASE.search(lowered):
        fields.remove("subjects")
    return fields


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_information_request(
    db,
    prog: dict[str, Any],
    fields: list[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Resolve requested fields against published catalogue data.

    Returns ``(found, missing)`` where each found entry is
    ``{"label", "source", "content" | "rows"}``. Fields with no value in any
    tier land in ``missing``.
    """
    found: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for field in fields:
        resolver = _RESOLVERS.get(field)
        if resolver is None:
            missing.append(field)
            continue
        try:
            result = resolver(prog, db)
        except Exception:
            result = None
        if result:
            found[field] = {"label": FIELD_LABELS.get(field, field.title()), **result}
        else:
            missing.append(field)
    return found, missing


def _from_upload(db, prog: dict[str, Any], *keys: str) -> str | None:
    """Read a scalar value from the programme's active uploaded curriculum."""
    try:
        from app.catalogue.service import get_active_curriculum_upload
        upload = get_active_curriculum_upload(db, programme_code=(prog.get("code") or ""))
    except Exception:
        return None
    if not upload:
        return None
    payload = upload.get("payload") or {}
    programme_block = payload.get("programme") or {}
    for key in keys:
        for candidate in (payload.get(key), programme_block.get(key)):
            if candidate is None:
                continue
            if isinstance(candidate, (list, dict)):
                if not candidate:
                    continue
                candidate = json.dumps(candidate, ensure_ascii=False)
            text = str(candidate).strip()
            if text:
                return text
    return None


def _lookup_legacy(prog: dict[str, Any], topic: str) -> str | None:
    """Read a field from the legacy structured slot-fill records."""
    try:
        from app.orchestrator.lookup import lookup_field
        value = lookup_field((prog.get("code") or "").lower(), topic)
        return str(value).strip() if value else None
    except Exception:
        return None


def _subjects_list(db, prog: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from app.catalogue.service import get_subjects
        return get_subjects(programme_id=prog.get("id"))
    except Exception:
        return []


def _fee(prog, db):
    entries = [e for e in (prog.get("fee_structure") or []) if str(e.get("value") or "").strip()]
    if entries:
        return {"source": "Academic Catalogue",
                "content": " \u00b7 ".join(f"{e.get('label')}: {e['value']}" for e in entries)}
    value = _from_upload(db, prog, "fee_structure", "fee", "fees", "tuition_fee")
    if value:
        return {"source": "Published Curriculum", "content": value}
    value = _lookup_legacy(prog, "fee")
    if value:
        return {"source": "University Records", "content": value}
    return None


def _eligibility(prog, db):
    if prog.get("eligibility"):
        return {"source": "Academic Catalogue", "content": str(prog["eligibility"])}
    value = _from_upload(db, prog, "eligibility", "eligibility_criteria")
    if value:
        return {"source": "Published Curriculum", "content": value}
    value = _lookup_legacy(prog, "eligibility")
    if value:
        return {"source": "University Records", "content": value}
    return None


def _duration(prog, db):
    if prog.get("duration_years"):
        return {"source": "Academic Catalogue", "content": f"{prog['duration_years']} years"}
    value = _from_upload(db, prog, "duration_years", "duration")
    if value:
        return {"source": "Published Curriculum", "content": value}
    value = _lookup_legacy(prog, "duration")
    if value:
        return {"source": "University Records", "content": value}
    return None


def _credits(prog, db):
    if prog.get("total_credits"):
        return {"source": "Academic Catalogue", "content": f"{prog['total_credits']} credits"}
    value = _from_upload(db, prog, "total_credits", "credits")
    if value:
        return {"source": "Published Curriculum", "content": value}
    subjects = _subjects_list(db, prog)
    if subjects and any((s.get("credits") or 0) > 0 for s in subjects):
        total = sum((s.get("credits") or 0) for s in subjects)
        return {"source": "Academic Catalogue",
                "content": f"{total} credits across {len(subjects)} subjects"}
    return None


def _scheme(prog, db):
    name = prog.get("scheme_name") or prog.get("academic_scheme")
    if name:
        code = prog.get("scheme_code")
        return {"source": "Academic Catalogue",
                "content": f"{name} ({code})" if code else str(name)}
    value = _from_upload(db, prog, "academic_scheme", "scheme")
    if value:
        return {"source": "Published Curriculum", "content": value}
    return None


def _subjects(prog, db):
    subjects = _subjects_list(db, prog)
    if not subjects:
        return None
    rows = []
    for s in subjects[:60]:
        label = s.get("subject_code") or "-"
        name = s.get("subject_name") or s.get("name") or "-"
        if s.get("semester"):
            label = f"Sem {s['semester']} \u00b7 {label}"
        rows.append({"label": label, "value": f"{name} \u00b7 {s.get('credits') or 0} credits"})
    return {"source": "Academic Catalogue", "rows": rows,
            "content": f"{len(subjects)} subjects on file"}


def _major(prog, db):
    values = [str(d) for d in (prog.get("major_disciplines") or []) if str(d).strip()]
    if not values:
        return None
    return {"source": "Academic Catalogue", "content": ", ".join(values)}


def _minor(prog, db):
    try:
        from app.catalogue.service import get_minor_disciplines
        minors = get_minor_disciplines(prog.get("id"))
    except Exception:
        minors = []
    names = [m.get("name") for m in minors if m.get("name")]
    if not names:
        return None
    return {"source": "Academic Catalogue", "content": ", ".join(names)}


def _category_rows(category: str) -> Any:
    def build(prog, db):
        try:
            from app.catalogue.service import get_category_subjects
            rows = get_category_subjects(category, programme_id=prog.get("id"))
        except Exception:
            rows = []
        if not rows:
            return None
        items = []
        for r in rows[:30]:
            name = r.get("subject_name") or r.get("name") or "-"
            code = r.get("subject_code")
            label = f"{code} \u00b7 {name}" if code else name
            items.append({"label": label, "value": f"{r.get('credits') or 0} credits"})
        return {"source": "Academic Catalogue", "rows": items,
                "content": f"{len(rows)} courses on file"}

    return build


def _outcomes(prog, db):
    try:
        from app.catalogue.service import get_learning_outcomes
        outcomes = [o for o in get_learning_outcomes(prog.get("id")) if o]
    except Exception:
        outcomes = []
    if not outcomes:
        return None
    return {"source": "Academic Catalogue", "content": " \u00b7 ".join(outcomes[:12])}


def _curriculum(prog, db):
    try:
        from app.catalogue.service import get_curriculum_documents
        docs = get_curriculum_documents(prog.get("id"))
    except Exception:
        docs = []
    if not docs:
        return None
    rows = []
    for i, doc in enumerate(docs[:10], start=1):
        label = doc.get("title") or doc.get("filename") or f"Document {i}"
        tail = [
            f"Semester {doc['semester']}" if doc.get("semester") else None,
            str(doc["status"]) if doc.get("status") not in (None, "indexed", "ready") else None,
        ]
        rows.append({"label": label, "value": " \u00b7 ".join(x for x in tail if x) or "Linked PDF"})
    return {"source": "Academic Catalogue", "rows": rows,
            "content": f"{len(docs)} document(s) linked"}


def _documents(prog, db):
    try:
        from app.catalogue.service import get_curriculum_documents
        docs = get_curriculum_documents(prog.get("id"))
    except Exception:
        docs = []
    if docs:
        rows = []
        for i, doc in enumerate(docs[:10], start=1):
            label = doc.get("title") or doc.get("filename") or f"Document {i}"
            rows.append({"label": label, "value": "Linked curriculum PDF"})
        return {"source": "Academic Catalogue", "rows": rows, "content": "Linked curriculum documents"}
    value = _from_upload(db, prog, "documents", "admission_documents")
    if value:
        return {"source": "Published Curriculum", "content": value}
    value = _lookup_legacy(prog, "documents")
    if value:
        return {"source": "University Records", "content": value}
    return None


_RESOLVERS: dict[str, Any] = {
    "fee": _fee,
    "eligibility": _eligibility,
    "duration": _duration,
    "credits": _credits,
    "scheme": _scheme,
    "subjects": _subjects,
    "major": _major,
    "minor": _minor,
    "vac": _category_rows("vac"),
    "sec": _category_rows("sec"),
    "aec": _category_rows("aec"),
    "outcomes": _outcomes,
    "curriculum": _curriculum,
    "documents": _documents,
}