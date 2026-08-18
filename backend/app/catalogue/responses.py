"""
backend/app/catalogue/responses.py

Response card builders for the academic catalogue.

All builders return payloads in the orchestrator's existing shapes
("options" / "detail"). The engine attaches the "context" breadcrumbs and
"done" events around these builders, so none of that is added here.
"""

from __future__ import annotations

from typing import Any

from app.catalogue.service import CATEGORY_LABELS, list_catalogue_programmes
from app.catalogue.knowledge import FIELD_LABELS
_LEVEL_TITLES: dict[str, str] = {
    "ug": "UG Programmes",
    "pg": "PG Programmes",
    "phd": "PhD Programmes",
    "integrated": "Integrated Programmes",
    "all": "All Programmes",
}

_SCHEME_LEVEL_OPTIONS: dict[str, str] = {
    "ug": "UG Programmes",
    "pg": "PG Programmes",
    "phd": "PhD Programmes",
    "integrated": "Integrated Programmes",
}


def _level_title(level: str | None) -> str:
    return _LEVEL_TITLES.get(level or "all", "Programmes")


def _option_for_programme(p: dict[str, Any]) -> dict[str, Any]:
    label = p.get("label") or p.get("code") or p.get("name") or "-"
    desc = p.get("category_name") or ""
    if p.get("degree_level"):
        desc = (desc + " \u00b7 " if desc else "") + str(p["degree_level"])
    return {"id": p["id"], "label": str(label), "description": desc}


def scheme_options_response(schemes: list[dict[str, Any]], level: str | None = None) -> dict[str, Any]:
    """Options card asking which academic scheme the user is interested in."""
    options = []
    for s in schemes:
        desc = s.get("description") or ""
        count = s.get("programme_count")
        if count:
            desc = (desc + " \u00b7 " if desc else "") + f"{count} programme(s)"
        options.append({"id": s["id"], "label": str(s.get("name") or "-"), "description": desc})
    title = "Academic Scheme"
    if level:
        title = f"{_SCHEME_LEVEL_OPTIONS.get(level, level.upper())} \u2014 Academic Scheme"
    return {
        "type": "options",
        "selector": "catalogue_scheme",
        "title": title,
        "message": "Which academic scheme are you interested in? Select one to browse its programmes.",
        "options": options,
    }


def level_options_response(scheme_name: str | None, levels: list[str]) -> dict[str, Any]:
    """Options card for choosing a degree level within a scheme."""
    options = [
        {"id": f"level:{level}", "label": _SCHEME_LEVEL_OPTIONS.get(level, level.upper()),
         "description": f"{_SCHEME_LEVEL_OPTIONS.get(level, level.upper())} under this scheme"}
        for level in levels
    ]
    title = f"{scheme_name or 'Academic Scheme'} \u2014 Level"
    return {
        "type": "options",
        "selector": "catalogue_level",
        "title": title,
        "message": "Which level would you like to explore?",
        "options": options,
    }


def scheme_hub_response(
    scheme: dict[str, Any],
    levels: list[str],
    counts: dict[str, int],
) -> dict[str, Any]:
    """Options card giving a scheme overview with data-driven exploration links.

    Used for bare scheme mentions ("NEP", "new education policy", "FYUGP").
    Each option id is `scheme:<op>`; the backend maps it to the matching flow.
    """
    name = scheme.get("name") or "Academic Scheme"
    code = (scheme.get("code") or "").upper()
    title = f"{name} — Overview"
    if code:
        title = f"{name} ({code}) — Overview"

    lines = [str(scheme.get("description") or "").strip()]
    prog_count = 0
    for lvl in levels:
        n = counts.get(lvl, 0)
        prog_count += n
        label = _SCHEME_LEVEL_OPTIONS.get(lvl, lvl.upper())
        lines.append(f"{label}: {n}")
    message = " \u00b7 ".join([l for l in lines if l])
    if prog_count:
        message = f"{prog_count} programme(s) across this scheme. What would you like to see?"

    options = [
        {"id": "scheme:list", "label": "Programmes",
         "description": "Browse all programmes under this scheme"},
        {"id": "scheme:major", "label": "Major Subjects",
         "description": "Subjects in the major discipline"},
        {"id": "scheme:minor", "label": "Minor Subjects",
         "description": "Minor / elective disciplines"},
        {"id": "scheme:semesters", "label": "Semester Structure",
         "description": "Subjects by semester"},
        {"id": "scheme:vac", "label": "VAC Courses",
         "description": "Value-added courses"},
        {"id": "scheme:sec", "label": "SEC Courses",
         "description": "Skill-enhancement courses"},
        {"id": "scheme:aec", "label": "AEC Courses",
         "description": "Ability-enhancement courses"},
        {"id": "scheme:credits", "label": "Credit Framework",
         "description": "Credit distribution per programme"},
        {"id": "scheme:curriculum", "label": "Curriculum",
         "description": "Curriculum documents / syllabus"},
        {"id": "scheme:outcomes", "label": "Learning Outcomes",
         "description": "Programme learning outcomes"},
    ]
    return {
        "type": "options",
        "selector": "catalogue_scheme_menu",
        "title": title,
        "message": message,
        "options": options,
    }


def programme_list_response(
    programmes: list[dict[str, Any]],
    level: str | None,
    scheme_name: str | None = None,
) -> dict[str, Any]:
    """Options card listing the matching programmes."""
    title = _level_title(level)
    if scheme_name:
        title = f"{scheme_name} \u2014 {title}"
    return {
        "type": "options",
        "selector": "catalogue_programme",
        "title": title,
        "message": "Select a programme to see its academic catalogue details.",
        "options": [_option_for_programme(p) for p in programmes],
    }


def programme_menu_response(
    prog: dict[str, Any],
    items: list[tuple[str, str]],
    scheme_name: str | None = None,
) -> dict[str, Any]:
    """Options card with the full menu for one programme."""
    name = prog.get("name") or prog.get("code") or "Programme"
    code = prog.get("code") or ""
    scheme_suffix = f" ({scheme_name})" if scheme_name else ""
    options = [
        {"id": f"menu:{op}", "label": label, "description": f"{name} \u2014 {label}"}
        for op, label in items
    ]
    return {
        "type": "options",
        "selector": "catalogue_menu",
        "title": f"{name} ({code}){scheme_suffix}",
        "message": f"Here is what I know about {name}. Choose an option to explore.",
        "options": options,
    }


def overview_response(prog: dict[str, Any]) -> dict[str, Any]:
    """Detail card for one programme."""
    name = prog.get("name") or prog.get("code") or "Programme"
    code = prog.get("code") or "-"
    fields: list[dict[str, str]] = [
        {"label": "Programme", "value": f"{name} ({code})"},
        {"label": "Level", "value": str(prog.get("degree_level") or "-")},
    ]
    if prog.get("scheme_name") or prog.get("academic_scheme"):
        fields.append({"label": "Academic Scheme", "value": str(prog.get("scheme_name") or prog["academic_scheme"])})
    if prog.get("eligibility"):
        fields.append({"label": "Eligibility", "value": str(prog["eligibility"])})
    if prog.get("fee_structure"):
        fee_total = len(prog["fee_structure"])
        fields.append({"label": "Fee Structure", "value": f"{fee_total} fee entr{'y' if fee_total == 1 else 'ies'} on file"})
    if prog.get("duration_years"):
        fields.append({"label": "Duration", "value": f"{prog['duration_years']} years"})
    if prog.get("total_credits"):
        fields.append({"label": "Total Credits", "value": str(prog["total_credits"])})
    if prog.get("category_name"):
        fields.append({"label": "Category", "value": str(prog["category_name"])})
    if prog.get("subject_count"):
        fields.append({"label": "Subjects", "value": f"{prog['subject_count']} subjects on file"})
    if prog.get("minor_count"):
        fields.append({"label": "Minor Disciplines", "value": f"{prog['minor_count']} disciplines on file"})
    disciplines = prog.get("major_disciplines") or []
    if disciplines:
        fields.append({"label": "Major Disciplines", "value": ", ".join(str(d) for d in disciplines)})
    if prog.get("description"):
        fields.append({"label": "About", "value": str(prog["description"])})
    return {"type": "detail", "title": f"{name} \u2014 Academic Catalogue", "fields": fields}


def semester_options_response(prog_name: str, semesters: list[int]) -> dict[str, Any]:
    """Options card for choosing a semester."""
    options = [
        {"id": f"semester:{sem}", "label": f"Semester {sem}", "description": f"Subjects in semester {sem}"}
        for sem in semesters
    ]
    return {
        "type": "options",
        "selector": "catalogue_semester",
        "title": f"{prog_name} \u2014 Semester",
        "message": f"Which semester's subjects would you like to see for {prog_name}?",
        "options": options,
    }


def subject_table_card(title: str, subjects: list[dict[str, Any]]) -> dict[str, Any]:
    """Detail card presenting subjects as labelled rows (code + credits)."""
    fields: list[dict[str, str]] = []
    for s in subjects:
        label = s.get("name") or "-"
        if s.get("code"):
            label = f"{s['code']} \u00b7 {label}"
        value = f"{s.get('credits') or 0} credits"
        fields.append({"label": label, "value": value})
    if not fields:
        fields = [{"label": "Subjects", "value": "No subjects recorded."}]
    return {
        "type": "detail",
        "title": title,
        "fields": fields,
        "message": _subject_summary(subjects),
    }


def _subject_summary(subjects: list[dict[str, Any]]) -> str:
    if not subjects:
        return ""
    total = sum((s.get("credits") or 0) for s in subjects)
    return f"{len(subjects)} subject(s) \u00b7 {total} credits"


def semester_subjects_response(prog_name: str, semester: int, subjects: list[dict[str, Any]]) -> dict[str, Any]:
    """Detail card for one semester's subjects, grouped by category."""
    per_category: dict[str, list[str]] = {}
    for s in subjects:
        cat = s.get("category") or "generic"
        row = "{0} \u00b7 {1} \u00b7 {2} cr".format(
            s.get("code") or "-", s.get("name") or "-", s.get("credits") or 0
        )
        per_category.setdefault(cat, []).append(row)
    fields = [
        {"label": CATEGORY_LABELS.get(cat, cat.title()), "value": "; ".join(rows)}
        for cat, rows in per_category.items()
    ]
    if not fields:
        fields = [{"label": "Subjects", "value": "No subjects recorded for this semester."}]
    return {
        "type": "detail",
        "title": f"{prog_name} \u2014 Semester {semester}",
        "fields": fields,
        "message": _subject_summary(subjects),
    }


def subject_category_response(
    programme_name: str | None, category: str, subjects: list[dict[str, Any]]
) -> dict[str, Any]:
    """Detail card for a subject category (major-route or the VAC/SEC/AEC pools)."""
    title = CATEGORY_LABELS.get(category, category.title())
    if programme_name:
        title = f"{title} \u2014 {programme_name}"
    return subject_table_card(title, subjects)


def minors_response(prog_name: str, minors: list[dict[str, Any]]) -> dict[str, Any]:
    """Options card for choosing a minor discipline."""
    options: list[dict[str, Any]] = []
    for m in minors:
        desc = f"{m.get('subject_count') or 0} subjects" if m.get("subject_count") else ""
        options.append({"id": m["id"], "label": str(m.get("name") or "-"), "description": desc})
    return {
        "type": "options",
        "selector": "catalogue_minor",
        "title": f"{prog_name} \u2014 Minor Disciplines",
        "message": f"{prog_name} offers the minor disciplines below. Choose one to see its subjects.",
        "options": options,
    }


def minor_subjects_response(minor_name: str, subjects: list[dict[str, Any]]) -> dict[str, Any]:
    return subject_table_card(f"{minor_name} \u2014 Minor Subjects", subjects)


def credits_response(prog_name: str, total: int | None, subjects: list[dict[str, Any]]) -> dict[str, Any]:
    """Detail card for the credit distribution of a programme."""
    by_cat: dict[str, int] = {}
    for s in subjects:
        cat = s.get("category") or "generic"
        by_cat[cat] = by_cat.get(cat, 0) + (s.get("credits") or 0)
    fields: list[dict[str, str]] = []
    if total:
        fields.append({"label": "Total Credits", "value": f"{total} credits"})
    for cat, crt in sorted(by_cat.items(), key=lambda kv: CATEGORY_LABELS.get(kv[0], kv[0])):
        fields.append({"label": CATEGORY_LABELS.get(cat, cat.title()), "value": f"{crt} credits"})
    if not fields:
        fields = [{"label": "Credits", "value": "No credit data recorded."}]
    return {
        "type": "detail",
        "title": f"{prog_name} \u2014 Credit Structure",
        "fields": fields,
        "message": _subject_summary(subjects),
    }


def outcomes_response(prog_name: str, outcomes: list[str]) -> dict[str, Any]:
    """Detail card for programme learning outcomes."""
    fields = [
        {"label": f"Outcome {i + 1}", "value": text or "-"}
        for i, text in enumerate(outcomes)
    ]
    if not fields:
        fields = [{"label": "Learning Outcomes", "value": "No outcomes recorded."}]
    return {"type": "detail", "title": f"{prog_name} \u2014 Learning Outcomes", "fields": fields}


def eligibility_response(prog: dict[str, Any]) -> dict[str, Any]:
    """Detail card for a programme's eligibility criteria."""
    name = prog.get("name") or prog.get("code") or "Programme"
    fields: list[dict[str, str]] = []
    if prog.get("degree_level"):
        fields.append({"label": "Level", "value": str(prog["degree_level"])})
    fields.append({"label": "Eligibility", "value": str(prog.get("eligibility") or "No eligibility criteria recorded.")})
    if prog.get("description"):
        fields.append({"label": "About", "value": str(prog["description"])})
    return {
        "type": "detail",
        "title": f"{name} \u2014 Eligibility",
        "fields": fields,
        "message": "Directly from the official academic catalogue.",
    }


def fee_response(prog: dict[str, Any]) -> dict[str, Any]:
    """Detail card for a programme's fee structure (structured catalogue data)."""
    name = prog.get("name") or prog.get("code") or "Programme"
    fields = [{"label": str(e.get("label") or "-"), "value": str(e.get("value") or "-")} for e in (prog.get("fee_structure") or [])]
    if not fields:
        fields = [{"label": "Fee Structure", "value": "No fee data recorded in the catalogue."}]
    return {
        "type": "detail",
        "title": f"{name} \u2014 Fee Structure",
        "fields": fields,
        "message": "Directly from the official academic catalogue.",
    }


def requested_response(
    prog: dict[str, Any],
    fields: list[str],
    found: dict[str, dict[str, Any]],
    missing: list[str],
) -> dict[str, Any]:
    """Detail card answering one or more granular `fields` for a programme.

    Rows carry the resolved field values (with their source); fields with no
    published value get a transparent "not published" row so the engine can
    cascade the same question into RAG (payload["missing_fields"]).
    """
    name = prog.get("name") or prog.get("code") or "Programme"

    rows: list[dict[str, str]] = []
    for field in fields:
        info = found.get(field)
        if info is None:
            continue
        label = str(info.get("label") or field.title())
        if info.get("rows"):
            rows.extend(info["rows"])
        else:
            rows.append({"label": label, "value": str(info.get("content") or "-")})
    for field in missing:
        rows.append({"label": str(field).title(), "value": "Not published in the Academic Catalogue yet."})

    single = len(fields) == 1
    title = f"{name} \u2014 {FIELD_LABELS.get(fields[0], fields[0].title())}" if single else f"{name} \u2014 Requested Information"
    sources = sorted({info.get("source") for info in found.values() if info.get("source")})
    message = "Directly from the academic catalogue."
    if missing:
        message = (
            "Here is what is published in the academic catalogue — "
            "let me check the knowledge base for the rest."
        )
    payload: dict[str, Any] = {
        "type": "detail",
        "title": title,
        "fields": rows,
        "message": message,
        "requested_fields": list(fields),
    }
    if sources and not missing:
        payload["message"] = f"From the {' and '.join(sources)}."
    payload["missing_fields"] = list(missing)
    return payload


def curriculum_response(prog: dict[str, Any], documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Detail card combining structured programme info + curriculum PDF metadata.

    Structured programme data is shown first; each linked curriculum document
    is listed with its metadata (semester, status). Semantic questions about
    the syllabus continue through RAG with the document as the source.
    """
    name = prog.get("name") or prog.get("code") or "Programme"
    fields: list[dict[str, str]] = [
        {"label": "Programme", "value": f"{name} ({prog.get('code') or '-'})"},
    ]
    if prog.get("scheme_name") or prog.get("academic_scheme"):
        fields.append({"label": "Academic Scheme", "value": str(prog.get("scheme_name") or prog["academic_scheme"])})
    if prog.get("degree_level"):
        fields.append({"label": "Level", "value": str(prog["degree_level"])})
    if prog.get("duration_years"):
        fields.append({"label": "Duration", "value": f"{prog['duration_years']} years"})
    if prog.get("total_credits"):
        fields.append({"label": "Total Credits", "value": str(prog["total_credits"])})
    for i, doc in enumerate(documents, start=1):
        label = doc.get("title") or doc.get("filename") or f"Curriculum Document {i}"
        value = ""
        if doc.get("semester"):
            value += f"Semester {doc['semester']}"
        status = doc.get("status")
        if status and status not in ("indexed", "ready"):
            value = (value + " \u00b7 " if value else "") + str(status)
        fields.append({"label": f"Document {i}", "value": (value or "Linked curriculum PDF") + f" \u00b7 {label}"})
    return {
        "type": "detail",
        "title": f"{name} \u2014 Curriculum",
        "fields": fields,
        "message": (
            "The official curriculum PDF(s) are linked above. Ask me anything specific "
            "about the syllabus and I will combine the catalogue data with the PDF content."
        ),
    }


def programme_pick_response(message: str | None = None) -> dict[str, Any]:
    """Catalogue selector for when the programme is ambiguous / unknown."""
    programmes = list_catalogue_programmes()
    options = [_option_for_programme(p) for p in programmes]
    if not options:
        options = [{"id": "back", "label": "Back", "description": "Return to the main menu"}]
    return {
        "type": "options",
        "selector": "catalogue_programme",
        "title": "Which programme?",
        "message": message or "Tell me which programme you are asking about.",
        "options": options,
    }