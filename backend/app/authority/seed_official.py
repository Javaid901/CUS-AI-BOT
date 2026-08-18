"""
backend/app/authority/seed_official.py

Idempotent import of VERIFIED official Cluster University of Srinagar
authorities, sourced from the official website:

  * Directory page      https://www.cusrinagar.edu.in/Home/Contacts
  * Administration page https://www.cusrinagar.edu.in/Home/Administration

Rules (per Phase 2 spec section 16):
  * match existing records by authority name (case-insensitive)
  * create only what is missing — never duplicate, never delete
  * fill a contact field ONLY when it is empty or a placeholder
    (e.g. example.com / example.edu), otherwise keep the admin's value
  * skip records whose contact details could not be verified on the
    official site; list them in the report instead of inventing values.

Run via POST /api/admin/authorities/import-official (Super Admin)
or directly:  run_official_import(db)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.authority.repository import list_all as repo_list_all
from app.authority.schemas import AuthorityCreate, AuthorityUpdate
from app.authority.service import authority_service
from app.utils.logging import log

OFFICIAL_SITE = "https://www.cusrinagar.edu.in"
DIRECTORY_URL = f"{OFFICIAL_SITE}/Home/Contacts"
ADMIN_URL = f"{OFFICIAL_SITE}/Home/Administration"

# ---------------------------------------------------------------------------
# Grievance categories (small, real, DB-driven — extend via Super Admin UI)
# ---------------------------------------------------------------------------
OFFICIAL_CATEGORIES = [
    {"name": "Academic Affairs", "slug": "academic-affairs", "description": "Admissions, curriculum, academics, departments"},
    {"name": "Examinations", "slug": "examinations", "description": "Exams, results, transcripts, certificates"},
    {"name": "Administration", "slug": "administration", "description": "Registrar, general administration, RTI"},
    {"name": "Student Welfare", "slug": "student-welfare", "description": "Student welfare, mentorship, counselling"},
    {"name": "Finance & Fees", "slug": "finance", "description": "Fees, scholarships, accounts"},
    {"name": "IT & Technical", "slug": "it", "description": "Portal, student zone, technical support"},
]

# ---------------------------------------------------------------------------
# Verified official authorities. email is REQUIRED (verified) — records whose
# contacts could not be verified appear in OFFICIAL_SKIPPED below.
# ---------------------------------------------------------------------------
OFFICIAL_AUTHORITIES: list[dict[str, Any]] = [
    {
        "authority_name": "Registrar",
        "department_name": "Registrar Office",
        "designation": "Registrar",
        "email": "registrar@cusrinagar.edu.in",
        "category": "Administration",
        "keywords": ["registrar", "verification", "documents", "certificates", "administrative"],
        "services_offered": ["General administration", "Official correspondence", "Certificates verification"],
        "description": "Registrar — Cluster University of Srinagar (official directory).",
    },
    {
        "authority_name": "Controller of Examinations",
        "department_name": "Controller of Examinations",
        "designation": "Controller of Examinations",
        "email": "controller@cusrinagar.edu.in",
        "category": "Examinations",
        "keywords": ["examination", "results", "datesheet", "transcript", "degree", "certificates",
                     "admit card", "hall ticket", "exam form", "examination form"],
        "services_offered": ["Examination scheduling", "Results", "Degree & transcript issuance",
                             "Admit card & hall ticket issuance"],
        "description": "Controller of Examinations — Cluster University of Srinagar (official directory).",
    },
    {
        "authority_name": "Dean Academic Affairs",
        "department_name": "Academic Section",
        "designation": "Dean Academic Affairs",
        "email": "dean.aa@cusrinagar.edu.in",
        "category": "Academic Affairs",
        "keywords": ["academic", "curriculum", "admissions", "syllabus", "course"],
        "services_offered": ["Academic programmes", "Curriculum guidance"],
        "description": "Dean Academic Affairs — Cluster University of Srinagar (official directory).",
    },
    {
        "authority_name": "Dean Social Sciences",
        "department_name": "School of Social Sciences",
        "designation": "Dean Social Sciences",
        "email": "dean.ssc@cusrinagar.edu.in",
        "category": "Academic Affairs",
        "keywords": ["social sciences", "dean", "school"],
        "services_offered": ["School of Social Sciences"],
        "description": "Dean Social Sciences — Cluster University of Srinagar (official directory).",
    },
    {
        "authority_name": "Dean Engineering & Technology",
        "department_name": "School of Engineering & Technology",
        "designation": "Dean Engineering & Technology",
        "email": "deanengg@cusrinagar.edu.in",
        "category": "Academic Affairs",
        "keywords": ["engineering", "technology", "b.tech", "btech"],
        "services_offered": ["School of Engineering & Technology"],
        "description": "Dean Engineering & Technology — Cluster University of Srinagar (official directory).",
    },
    {
        "authority_name": "Dean Science",
        "department_name": "School of Sciences",
        "designation": "Dean Science",
        "email": "dean.sc@cusrinagar.edu.in",
        "category": "Academic Affairs",
        "keywords": ["science", "sciences"],
        "services_offered": ["School of Sciences"],
        "description": "Dean Science — Cluster University of Srinagar (official directory).",
    },
    {
        "authority_name": "Dean Humanities & Liberal Arts",
        "department_name": "School of Humanities & Liberal Arts",
        "designation": "Dean Humanities & Liberal Arts",
        "email": "dean.hla@cusrinagar.edu.in",
        "category": "Academic Affairs",
        "keywords": ["humanities", "liberal arts"],
        "services_offered": ["School of Humanities & Liberal Arts"],
        "description": "Dean Humanities & Liberal Arts — Cluster University of Srinagar (official directory).",
    },
    {
        "authority_name": "Dean Commerce & Management",
        "department_name": "School of Commerce & Management",
        "designation": "Dean Commerce & Management",
        "email": "dean.cm@cusrinagar.edu.in",
        "category": "Academic Affairs",
        "keywords": ["commerce", "management", "bba", "bcom"],
        "services_offered": ["School of Commerce & Management"],
        "description": "Dean Commerce & Management — Cluster University of Srinagar (official directory).",
    },
    {
        "authority_name": "Dean Students Welfare",
        "department_name": "Student Welfare",
        "designation": "Dean Students Welfare",
        "email": "dean.dsw@cusrinagar.edu.in",
        "category": "Student Welfare",
        "keywords": ["student welfare", "hostel", "scholarship", "mentor"],
        "services_offered": ["Student welfare", "Mentorship"],
        "description": "Dean Students Welfare — Cluster University of Srinagar (official directory).",
    },
]

# Official pages show these offices but publish no reliable machine-readable
# email on the directory page; per "do not invent", they stay unseeded.
OFFICIAL_SKIPPED = [
    ("Vice Chancellor", "no email published on official Administration page"),
    ("Chancellor / Pro-Chancellor", "no email published"),
    ("Dean Teacher Education", "official page shows mismatched email (dean.et vs dean.edu)"),
    ("Finance Officer (FA/CAO)", "no email published on official Administration page"),
]

_PLACEHOLDER_HINTS = ("example.", "example.edu", "example.com", "@gmail.com", "@gmail")


def _is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    return any(h in value.lower() for h in _PLACEHOLDER_HINTS)


def _category_map(db: Session) -> dict[str, str]:
    """Create missing official categories; return {name: category_id}."""
    mapping: dict[str, str] = {}
    for cat in OFFICIAL_CATEGORIES:
        match = authority_service.get_category_by_slug(db, cat["slug"])
        if match:
            mapping[cat["name"]] = match["id"]
            continue
        try:
            created = authority_service.create_category(db, cat["name"], description=cat.get("description"), slug=cat["slug"])
            mapping[cat["name"]] = created["id"]
        except ValueError:
            # race-visible duplicate handled elsewhere; fetch again
            match = authority_service.get_category_by_slug(db, cat["slug"])
            if match:
                mapping[cat["name"]] = match["id"]
    return mapping


def run_official_import(db: Session) -> dict[str, Any]:
    """Idempotent official authorities import. Safe to rerun any number of times."""
    cat_map = _category_map(db)
    existing = repo_list_all(db)
    by_name = {a["authority_name"].strip().lower(): a for a in existing}

    created = 0
    updated = 0
    for spec in OFFICIAL_AUTHORITIES:
        cat_id = cat_map.get(spec["category"])
        row = by_name.get(spec["authority_name"].strip().lower())
        if row is None:
            payload = AuthorityCreate(
                department_name=spec["department_name"],
                authority_name=spec["authority_name"],
                designation=spec["designation"],
                email=spec["email"],
                phone=spec.get("phone") or "0194-2311256",
                website=OFFICIAL_SITE,
                category_id=cat_id,
                source_kind="official",
                keywords=spec["keywords"],
                services_offered=spec["services_offered"],
                description=spec.get("description"),
            )
            authority_service.create(db, payload)
            created += 1
            continue

        patch: dict[str, Any] = {}
        if _is_placeholder(row.get("email")):
            patch["email"] = spec["email"]
        if _is_placeholder(row.get("phone")):
            patch["phone"] = "0194-2311256"
        if _is_placeholder(row.get("website")):
            patch["website"] = OFFICIAL_SITE
        if not row.get("category_id") and cat_id:
            patch["category_id"] = cat_id
        if not row.get("source_kind") or row.get("source_kind") == "manual":
            patch["source_kind"] = "official"
        # Official-sourced rows are defined by the official seed: refresh the
        # search surface (keywords / services) when the seed changes, without
        # touching admin-entered contact details. Compare first so re-runs of
        # an unchanged seed stay true no-ops.
        if row.get("source_kind") == "official":
            if (row.get("keywords") or []) != spec["keywords"]:
                patch["keywords"] = spec["keywords"]
            if (row.get("services_offered") or []) != spec["services_offered"]:
                patch["services_offered"] = spec["services_offered"]
        if patch:
            authority_service.update(db, row["id"], AuthorityUpdate(**patch))
            updated += 1

    authority_service.refresh_cache(db)
    log.info("Official authority import: %d created, %d updated", created, updated)
    return {
        "created": created,
        "updated": updated,
        "total": len(repo_list_all(db)),
        "skipped_unverified": [name for name, reason in OFFICIAL_SKIPPED],
        "source": DIRECTORY_URL,
        "source_admin_page": ADMIN_URL,
    }