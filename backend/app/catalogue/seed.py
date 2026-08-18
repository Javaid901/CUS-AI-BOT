"""
backend/app/catalogue/seed.py

Academic Catalogue demo seed data.

Runs on startup when DEMO_MODE is enabled. Provides a realistic set of
academic schemes, programmes, subjects, minor disciplines and shared
course pools so the chatbot has structured data to answer programme /
curriculum / semester / credits / outcomes / fee / eligibility queries
without manual entry first.

`ensure_schemes(db)` is idempotent and runs on every startup: it creates
the default schemes when missing and backfills `programmes.scheme_id` for
rows that predate the schemes table.

`seed_catalogue(db)` is also safe to run on every startup: demo programmes
that are missing (e.g. databases created before eligibility / fee data or
the Traditional scheme existed) are created or backfilled WITHOUT touching
rows the admin has edited. Returns the number of programmes (re)created.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

_log = logging.getLogger("cus")

MAJOR = "major"
MINOR_CAT = "minor"
VAC = "vac"
SEC = "sec"
AEC = "aec"

_SCHEME_NEP = "nep2020"
_SCHEME_TRADITIONAL = "traditional"

_UG_CAT_NAME = "Undergraduate"
_PG_CAT_NAME = "Postgraduate"
_PHD_CAT_NAME = "Doctoral"

# Default schemes created when the table is empty (idempotent).
_DEFAULT_SCHEMES = [
    {
        "name": "Traditional Curriculum",
        "code": _SCHEME_TRADITIONAL,
        "description": "The conventional fixed-curriculum scheme followed before NEP 2020.",
        "sort_order": 1,
    },
    {
        "name": "NEP 2020 Curriculum",
        "code": _SCHEME_NEP,
        "description": "National Education Policy 2020 — multi-disciplinary, flexible credit framework.",
        "sort_order": 2,
    },
]

# Legacy academic_scheme string -> default scheme code (used only for backfill).
_LEGACY_SCHEME_MAP = {
    "nep": _SCHEME_NEP,
    "nep2020": _SCHEME_NEP,
    "cbcs": _SCHEME_TRADITIONAL,
    "traditional": _SCHEME_TRADITIONAL,
}


def _fee(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"label": label, "value": value} for label, value in pairs]


# ---------------------------------------------------------------------------
# Demo catalogue data (referenced by `seed_catalogue` on every startup).
# Each spec uses `scheme_code`; the concrete `scheme_id` is resolved at seed
# time from `ensure_schemes(...)` so the constant stays DB-independent.
# ---------------------------------------------------------------------------

_DEMO_CATALOGUE = [
    {
        "name": "Bachelor of Computer Applications",
        "code": "BCA",
        "degree_level": "Bachelor",
        "scheme_code": _SCHEME_NEP,
        "academic_scheme": _SCHEME_NEP,
        "duration_years": 3,
        "total_credits": 160,
        "category": "ug",
        "major_disciplines": ["Computer Science", "Software Engineering", "Data Science"],
        "eligibility": (
            "Pass in 10+2 (or equivalent) with Mathematics / Computer Science / "
            "Information Practice as one of the subjects, with at least 50% aggregate marks."
        ),
        "fee_structure": _fee(
            ("Admission Fee (one-time)", "Rs. 5,000"),
            ("Annual Tuition Fee", "Rs. 45,000"),
            ("Examination Fee (per year)", "Rs. 2,500"),
            ("Caution Deposit (refundable)", "Rs. 3,000"),
        ),
        "description": (
            "A three-year programme building strong foundations in programming, software "
            "development, databases, networking and modern application engineering under the "
            "NEP 2020 multi-disciplinary credit framework."
        ),
    },
    {
        "name": "Bachelor of Business Administration",
        "code": "BBA",
        "degree_level": "Bachelor",
        "scheme_code": _SCHEME_NEP,
        "academic_scheme": _SCHEME_NEP,
        "duration_years": 3,
        "total_credits": 160,
        "category": "ug",
        "major_disciplines": ["Management", "Marketing", "Finance"],
        "eligibility": (
            "Pass in 10+2 (or equivalent) in any stream with at least 50% aggregate marks; "
            "admission through merit or university entrance test."
        ),
        "fee_structure": _fee(
            ("Admission Fee (one-time)", "Rs. 5,000"),
            ("Annual Tuition Fee", "Rs. 50,000"),
            ("Examination Fee (per year)", "Rs. 2,500"),
        ),
        "description": (
            "A three-year management programme covering business fundamentals, organisational "
            "behaviour, marketing, finance and entrepreneurship with industry internships."
        ),
    },
    {
        "name": "Bachelor of Arts (English)",
        "code": "BA English",
        "degree_level": "Bachelor",
        "scheme_code": _SCHEME_NEP,
        "academic_scheme": _SCHEME_NEP,
        "duration_years": 3,
        "total_credits": 160,
        "category": "ug",
        "major_disciplines": ["English Literature", "Linguistics", "Creative Writing"],
        "eligibility": "Pass in 10+2 (or equivalent) in any stream with at least 45% aggregate marks.",
        "fee_structure": _fee(
            ("Admission Fee (one-time)", "Rs. 3,000"),
            ("Annual Tuition Fee", "Rs. 25,000"),
            ("Examination Fee (per year)", "Rs. 2,000"),
        ),
        "description": (
            "A humanities programme focused on literature, critical theory, linguistics and "
            "communication, delivered through the NEP 2020 flexible credit system."
        ),
    },
    {
        "name": "Bachelor of Commerce",
        "code": "B.Com",
        "degree_level": "Bachelor",
        "scheme_code": _SCHEME_NEP,
        "academic_scheme": _SCHEME_NEP,
        "duration_years": 3,
        "total_credits": 160,
        "category": "ug",
        "major_disciplines": ["Accountancy", "Corporate Law", "Taxation"],
        "eligibility": "Pass in 10+2 (or equivalent) in Commerce stream with at least 50% aggregate marks.",
        "fee_structure": _fee(
            ("Admission Fee (one-time)", "Rs. 3,000"),
            ("Annual Tuition Fee", "Rs. 30,000"),
            ("Examination Fee (per year)", "Rs. 2,000"),
        ),
        "description": (
            "A commerce programme covering accountancy, corporate law, taxation and economics "
            "with hands-on practical training and industry-aligned electives."
        ),
    },
    {
        "name": "Master of Computer Applications",
        "code": "MCA",
        "degree_level": "Master",
        "scheme_code": _SCHEME_NEP,
        "academic_scheme": _SCHEME_NEP,
        "duration_years": 2,
        "total_credits": 120,
        "category": "pg",
        "major_disciplines": ["Advanced Computing", "Applied Data Science"],
        "eligibility": (
            "Bachelor's degree with Mathematics / Statistics / Computer Science at graduation "
            "level, with at least 50% aggregate marks."
        ),
        "fee_structure": _fee(
            ("Admission Fee (one-time)", "Rs. 6,000"),
            ("Annual Tuition Fee", "Rs. 60,000"),
            ("Examination Fee (per year)", "Rs. 3,000"),
        ),
        "description": (
            "A two-year postgraduate programme in advanced computer applications with research "
            "project work and industry specialisations under NEP 2020."
        ),
    },
    {
        "name": "Master of Commerce",
        "code": "M.Com",
        "degree_level": "Master",
        "scheme_code": _SCHEME_NEP,
        "academic_scheme": _SCHEME_NEP,
        "duration_years": 2,
        "total_credits": 120,
        "category": "pg",
        "major_disciplines": ["Advanced Accountancy", "Financial Management"],
        "eligibility": "Bachelor's degree in Commerce (B.Com) with at least 50% aggregate marks.",
        "fee_structure": _fee(
            ("Admission Fee (one-time)", "Rs. 4,000"),
            ("Annual Tuition Fee", "Rs. 40,000"),
            ("Examination Fee (per year)", "Rs. 2,500"),
        ),
        "description": (
            "A postgraduate commerce programme centred on advanced accountancy, financial markets "
            "and business research methods."
        ),
    },
    {
        "name": "Doctor of Philosophy (Computer Science)",
        "code": "PhD CS",
        "degree_level": "Doctorate",
        "scheme_code": _SCHEME_NEP,
        "academic_scheme": _SCHEME_NEP,
        "duration_years": 3,
        "total_credits": 120,
        "category": "phd",
        "major_disciplines": ["Artificial Intelligence", "Cyber Security"],
        "eligibility": (
            "Master's degree in Computer Science / MCA / M.Sc (CS) with at least 55% marks, "
            "plus qualification in the university entrance examination."
        ),
        "fee_structure": _fee(
            ("Admission Fee (one-time)", "Rs. 10,000"),
            ("Annual Tuition Fee", "Rs. 50,000"),
            ("Thesis / Registration Fee (per year)", "Rs. 5,000"),
        ),
        "description": (
            "Doctoral research programme in computer science requiring coursework, comprehensive "
            "examinations and an original dissertation."
        ),
    },
    {
        "name": "Bachelor of Science",
        "code": "B.Sc",
        "degree_level": "Bachelor",
        "scheme_code": _SCHEME_TRADITIONAL,
        "academic_scheme": _SCHEME_TRADITIONAL,
        "duration_years": 3,
        "total_credits": 120,
        "category": "ug",
        "major_disciplines": ["Physics", "Chemistry", "Mathematics"],
        "eligibility": (
            "Pass in 10+2 (or equivalent) in Science stream with Physics, Chemistry and "
            "Mathematics, with at least 50% aggregate marks."
        ),
        "fee_structure": _fee(
            ("Admission Fee (one-time)", "Rs. 3,000"),
            ("Annual Tuition Fee", "Rs. 22,000"),
            ("Examination Fee (per year)", "Rs. 2,000"),
        ),
        "description": (
            "A conventional three-year science programme following the Traditional Curriculum "
            "with a fixed sequence of theory and practical papers."
        ),
    },
    {
        "name": "Master of Business Administration",
        "code": "MBA",
        "degree_level": "Master",
        "scheme_code": _SCHEME_TRADITIONAL,
        "academic_scheme": _SCHEME_TRADITIONAL,
        "duration_years": 2,
        "total_credits": 100,
        "category": "pg",
        "major_disciplines": ["General Management", "Human Resources", "Finance"],
        "eligibility": (
            "Bachelor's degree in any discipline with at least 50% aggregate marks, plus a "
            "valid score in the university management entrance test (CAT / MAT accepted)."
        ),
        "fee_structure": _fee(
            ("Admission Fee (one-time)", "Rs. 8,000"),
            ("Annual Tuition Fee", "Rs. 75,000"),
            ("Examination Fee (per year)", "Rs. 3,000"),
        ),
        "description": (
            "A two-year postgraduate management programme under the Traditional Curriculum "
            "with specialisations in finance, marketing and human resources."
        ),
    },
]

_DEMO_SUBJECTS: dict[str, list[tuple]] = {
    "BCA": [
        (1, MAJOR, "Programming in C", "CA101", 4),
        (1, MAJOR, "Discrete Mathematics", "CA102", 4),
        (1, MAJOR, "Computer Fundamentals", "CA103", 3),
        (1, SEC, "Digital Marketing Fundamentals", "SEC001", 3),
        (1, AEC, "Communication Skills", "AEC001", 2),
        (2, MAJOR, "Object Oriented Programming", "CA201", 4),
        (2, MAJOR, "Data Structures", "CA202", 4),
        (2, MAJOR, "Database Management Systems", "CA203", 4),
        (2, VAC, "Human Values and Ethics", "VAC001", 2),
        (2, SEC, "Data Analysis with Spreadsheets", "SEC002", 3),
        (3, MAJOR, "Operating Systems", "CA301", 4),
        (3, MAJOR, "Web Application Development", "CA302", 4),
        (3, MAJOR, "Software Engineering Principles", "CA303", 4),
        (3, AEC, "Effective Presentation Skills", "AEC003", 2),
    ],
    "BBA": [
        (1, MAJOR, "Principles of Management", "BB101", 4),
        (1, MAJOR, "Business Organisation", "BB102", 4),
        (1, MAJOR, "Business Mathematics", "BB103", 4),
        (1, SEC, "Digital Marketing Fundamentals", "SEC001", 3),
        (1, AEC, "Communication Skills", "AEC001", 2),
        (2, MAJOR, "Financial Accounting", "BB204", 4),
        (2, MAJOR, "Marketing Management", "BB205", 4),
        (2, MAJOR, "Organisational Behaviour", "BB206", 4),
        (2, VAC, "Entrepreneurship Essentials", "VAC003", 2),
        (3, MAJOR, "Human Resource Management", "BB307", 4),
        (3, MAJOR, "Financial Management", "BB308", 4),
        (3, MAJOR, "Business Research Methods", "BB309", 4),
        (3, VAC, "Environmental Sustainability", "VAC002", 2),
    ],
    "BA English": [
        (1, MAJOR, "Introduction to Literature", "EN101", 4),
        (1, MAJOR, "Literary Criticism", "EN102", 4),
        (1, MAJOR, "English Phonetics", "EN103", 3),
        (1, SEC, "Digital Marketing Fundamentals", "SEC001", 3),
        (1, AEC, "Critical Reading & Writing", "AEC002", 2),
        (2, MAJOR, "British Literature", "EN204", 4),
        (2, MAJOR, "Modern World Literature", "EN205", 4),
        (2, MAJOR, "Linguistics", "EN206", 4),
        (2, VAC, "Environmental Sustainability", "VAC002", 2),
        (3, MAJOR, "Post-Colonial Studies", "EN307", 4),
        (3, MAJOR, "Media & Communication", "EN308", 4),
        (3, AEC, "Effective Presentation Skills", "AEC003", 2),
    ],
    "B.Com": [
        (1, MAJOR, "Financial Accounting", "BC101", 4),
        (1, MAJOR, "Business Mathematics", "BC102", 4),
        (1, MAJOR, "Business Communication", "BC103", 3),
        (1, SEC, "Data Analysis with Spreadsheets", "SEC002", 3),
        (1, VAC, "Human Values and Ethics", "VAC001", 2),
        (2, MAJOR, "Corporate Accounting", "BC204", 4),
        (2, MAJOR, "Cost Accounting", "BC205", 4),
        (2, MAJOR, "Business Law", "BC206", 4),
        (2, SEC, "Digital Marketing Fundamentals", "SEC001", 3),
        (3, MAJOR, "Taxation Laws", "BC307", 4),
        (3, MAJOR, "Auditing", "BC308", 4),
        (3, AEC, "Effective Presentation Skills", "AEC003", 2),
    ],
    "MCA": [
        (1, MAJOR, "Advanced Data Structures", "MCA101", 4),
        (1, MAJOR, "Advanced Databases", "MCA102", 4),
        (1, MAJOR, "Machine Learning", "MCA103", 4),
        (2, MAJOR, "Cloud Computing", "MCA204", 4),
        (2, MAJOR, "Computer Networks", "MCA205", 4),
        (2, MAJOR, "Research Methodology", "MCA206", 3),
    ],
    "M.Com": [
        (1, MAJOR, "Advanced Financial Accounting", "MC101", 4),
        (1, MAJOR, "Financial Markets", "MC102", 4),
        (1, MAJOR, "Management Accounting", "MC103", 4),
        (2, MAJOR, "Business Research Methods", "MC204", 4),
        (2, MAJOR, "Strategic Financial Management", "MC205", 4),
        (2, MAJOR, "Income Tax Law & Practice", "MC206", 4),
    ],
    "PhD CS": [
        (1, MAJOR, "Research Methodology", "PH101", 4),
        (1, MAJOR, "Advanced Topics in CS", "PH102", 4),
    ],
    "B.Sc": [
        (1, MAJOR, "Classical Mechanics", "BS101", 4),
        (1, MAJOR, "Organic Chemistry", "BS102", 4),
        (1, MAJOR, "Calculus", "BS103", 4),
        (2, MAJOR, "Electromagnetism", "BS204", 4),
        (2, MAJOR, "Physical Chemistry", "BS205", 4),
        (2, MAJOR, "Linear Algebra", "BS206", 4),
        (3, MAJOR, "Quantum Mechanics", "BS307", 4),
        (3, MAJOR, "Inorganic Chemistry", "BS308", 4),
        (3, MAJOR, "Real Analysis", "BS309", 4),
    ],
    "MBA": [
        (1, MAJOR, "Principles of Management", "MB101", 4),
        (1, MAJOR, "Managerial Economics", "MB102", 4),
        (1, MAJOR, "Organisational Behaviour", "MB103", 4),
        (2, MAJOR, "Financial Management", "MB204", 4),
        (2, MAJOR, "Marketing Management", "MB205", 4),
        (2, MAJOR, "Human Resource Management", "MB206", 4),
    ],
}

_DEMO_MINOR_POOLS: dict[str, list[tuple]] = {
    "BCA": [
        ("Data Analytics", [(1, MAJOR, "Data Tools & Visualisation", "MD101", 3)]),
    ],
    "BBA": [
        ("Entrepreneurship", [(1, MAJOR, "Startup Finance", "MD201", 3)]),
    ],
    "MBA": [
        ("Marketing Analytics", [(1, MAJOR, "Marketing Research", "MD301", 3)]),
    ],
}

_DEMO_OUTCOMES: dict[str, list[str]] = {
    "BCA": [
        "Design and implement software solutions using modern programming paradigms.",
        "Apply data structures and algorithms to solve computational problems.",
        "Build maintainable web applications and database-backed systems.",
    ],
    "BBA": [
        "Evaluate business environments and formulate coordinated management strategies.",
        "Apply financial and marketing principles to operational decision-making.",
        "Lead teams using contemporary organisational behaviour concepts.",
    ],
    "MBA": [
        "Analyse business problems and formulate strategy using management frameworks.",
        "Apply financial, marketing and HR principles to real-world decision-making.",
        "Communicate and lead effectively in a professional management environment.",
    ],
}

# Map of demo scheme code -> created category when seeding from scratch.
_DEMO_CATEGORY_LABELS = {"ug": _UG_CAT_NAME, "pg": _PG_CAT_NAME, "phd": _PHD_CAT_NAME}


def ensure_schemes(db: Session) -> dict[str, str]:
    """Idempotent: create default schemes + backfill programme.scheme_id.

    Returns a mapping of scheme code -> scheme id. Safe to call on every
    startup (existing schemes and linked programmes are left untouched).
    """
    from app.catalogue import service

    schemes = service.list_academic_schemes(db=db)
    by_code = {s["code"].lower(): s["id"] for s in schemes}
    if not by_code:
        for spec in _DEFAULT_SCHEMES:
            created = service.create_academic_scheme(db, dict(spec))
            by_code[created["code"].lower()] = created["id"]
        _log.info("Seeded %d default academic schemes", len(by_code))

    programmes = service.list_programmes(db=db)
    for prog in programmes:
        if prog.get("scheme_id"):
            continue
        scheme_code = _LEGACY_SCHEME_MAP.get((prog.get("academic_scheme") or "").lower())
        if scheme_code and scheme_code in by_code:
            service.update_programme(db, prog["id"], {"scheme_id": by_code[scheme_code]})
    return by_code


def _category_by_label(db: Session, label: str | None) -> str | None:
    """Return existing category id for a level label, creating it if missing."""
    from app.catalogue import service

    if not label:
        return None
    name = _DEMO_CATEGORY_LABELS.get(label)
    for cat in service.list_categories(db=db):
        if cat.get("level_label") == label or (name and cat.get("name") == name):
            return cat["id"]
    return service.create_category(db, {"name": name or label, "level_label": label, "sort_order": 1})["id"]


def seed_catalogue(db: Session) -> int:
    """Seed demo catalogue data; creates missing demos + backfills gaps.

    Runs `ensure_schemes` on every call. Demo programmes are created when
    missing and existing demo rows are backfilled with eligibility / fee /
    scheme data they may predate. Admin-edited rows are left untouched
    (only empty fields are filled). Returns the number of programmes
    (re)created.
    """
    from app.catalogue import service

    by_scheme = ensure_schemes(db)
    existing = {p["code"]: p for p in service.list_programmes(db=db)}

    programmes = {}
    for spec in _DEMO_CATALOGUE:
        code = spec["code"]
        if code in existing:
            programmes[code] = _backfill_demo_programme(db, existing[code], spec, by_scheme)
        else:
            programmes[code] = service.create_programme(db, _resolve_spec(db, spec, by_scheme))

    if not programmes:
        _log.info("Demo catalogue already up to date")
        return 0

    _seed_shared_pool_if_empty(db)
    for code, prog in programmes.items():
        _seed_subjects_if_empty(db, code, prog["id"])
        _seed_minor_pool_if_empty(db, code, prog["id"])
        _seed_outcomes_if_empty(db, code, prog["id"])

    _log.info("Seeded %d catalogue programmes", len(programmes))
    return len(programmes)


def _resolve_spec(db: Session, spec: dict, by_scheme: dict[str, str]) -> dict:
    """Translate a `_DEMO_CATALOGUE` spec into a create_programme payload."""
    resolved = {k: v for k, v in spec.items() if k not in ("scheme_code", "category")}
    resolved["scheme_id"] = by_scheme[spec["scheme_code"]]
    cat_id = _category_by_label(db, spec.get("category"))
    if cat_id:
        resolved["category_id"] = cat_id
    return resolved


def _backfill_demo_programme(db: Session, prog: dict, spec: dict, by_scheme: dict[str, str]) -> dict:
    """Fill empty eligibility/fee/scheme/category on a demo programme row.

    Only touches empty fields so admin edits are preserved. Returns the
    (possibly updated) programme view.
    """
    from app.catalogue import service

    updates: dict = {}
    if not prog.get("eligibility") and spec.get("eligibility"):
        updates["eligibility"] = spec["eligibility"]
    if not prog.get("fee_structure") and spec.get("fee_structure"):
        updates["fee_structure"] = spec["fee_structure"]
    if not prog.get("scheme_id") and spec.get("scheme_code") in by_scheme:
        updates["scheme_id"] = by_scheme[spec["scheme_code"]]
    if not prog.get("category_id") and spec.get("category"):
        cat_id = _category_by_label(db, spec["category"])
        if cat_id:
            updates["category_id"] = cat_id
    if not prog.get("major_disciplines") and spec.get("major_disciplines"):
        updates["major_disciplines"] = spec["major_disciplines"]
    if not prog.get("description") and spec.get("description"):
        updates["description"] = spec["description"]
    if updates:
        return service.update_programme(db, prog["id"], updates) or prog
    return prog


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _seed_shared_pool_if_empty(db: Session) -> None:
    """Seed VAC / SEC / AEC pools only when the pools are empty."""
    from app.catalogue import service

    if service.get_category_subjects(VAC):
        return
    _seed_shared_pool(db, VAC, [
        ("VAC001", "Human Values and Ethics", 2),
        ("VAC002", "Environmental Sustainability", 2),
        ("VAC003", "Entrepreneurship Essentials", 2),
    ])
    _seed_shared_pool(db, SEC, [
        ("SEC001", "Digital Marketing Fundamentals", 3),
        ("SEC002", "Data Analysis with Spreadsheets", 3),
        ("SEC003", "Basics of Web Development", 3),
    ])
    _seed_shared_pool(db, AEC, [
        ("AEC001", "Communication Skills", 2),
        ("AEC002", "Critical Reading & Writing", 2),
        ("AEC003", "Effective Presentation Skills", 2),
    ])


def _seed_subjects_if_empty(db: Session, code: str, programme_id: str) -> None:
    """Seed programme subjects only when the programme has none yet."""
    from app.catalogue import service

    if service.get_subjects(programme_id=programme_id):
        return
    rows = _DEMO_SUBJECTS.get(code)
    if rows:
        _seed_programme_subjects(db, programme_id, rows)


def _seed_minor_pool_if_empty(db: Session, code: str, programme_id: str) -> None:
    """Seed minor discipline pools only when the programme has none."""
    from app.catalogue import service

    if service.get_minor_disciplines(programme_id):
        return
    rows = _DEMO_MINOR_POOLS.get(code)
    if rows:
        _seed_minor_pool(db, programme_id, rows)


def _seed_outcomes_if_empty(db: Session, code: str, programme_id: str) -> None:
    """Seed learning outcomes only when the programme has none."""
    from app.catalogue import service

    if service.get_learning_outcomes(programme_id):
        return
    texts = _DEMO_OUTCOMES.get(code)
    if texts:
        _seed_outcomes(db, programme_id, texts)


def _seed_shared_pool(db: Session, category: str, rows) -> None:
    """Programme-wide courses (VAC / SEC / AEC) with no owning programme."""
    from app.catalogue import service

    for code, name, credits in rows:
        service.add_subject(
            db,
            programme_id=None,
            data={
                "category": category,
                "semester": None,
                "subject_code": code,
                "subject_name": name,
                "credits": credits,
                "hours": credits * 15,
            },
        )


def _seed_programme_subjects(db: Session, programme_id: str, rows) -> None:
    from app.catalogue import service

    for semester, category, name, code, credits in rows:
        service.add_subject(
            db,
            programme_id=programme_id,
            data={
                "category": category,
                "semester": semester,
                "subject_code": code,
                "subject_name": name,
                "credits": credits,
                "hours": credits * 15,
            },
        )


def _seed_minor_pool(db: Session, programme_id: str, groups) -> None:
    """Create minor disciplines and bind their subjects to each minor."""
    from app.catalogue import service

    for minor_name, subject_rows in groups:
        minor = service.add_minor(db, programme_id, {"name": minor_name, "description": f"{minor_name} minor pool"})
        minor_id = minor["id"]
        for semester, category, name, code, credits in subject_rows:
            service.add_subject(
                db,
                programme_id=programme_id,
                data={
                    "category": MINOR_CAT,
                    "minor_discipline_id": minor_id,
                    "semester": semester,
                    "subject_code": code,
                    "subject_name": name,
                    "credits": credits,
                    "hours": credits * 15,
                },
            )


def _seed_outcomes(db: Session, programme_id: str, texts) -> None:
    from app.catalogue import service

    service.replace_outcomes(db, programme_id, texts)