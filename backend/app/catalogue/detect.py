"""
backend/app/catalogue/detect.py

Rule-based academic catalogue request detection for the AI Orchestrator.

Runs as an early branch in the planner. A request is ONLY routed to the
catalogue when the matching structured data exists — otherwise detection
returns None and the existing pipeline (structured / RAG) handles the
query unchanged. This gives structured catalogue data priority over
generic document retrieval without breaking existing flows.

Each returned request dict uses an "op" + payload consumed by the engine:
  schemes / list / overview / semesters / semester_subjects / subjects /
  minors / minors_subjects / vac / sec / aec / credits / outcomes /
  curriculum / fee / eligibility / programme_pick

Scheme hierarchy ("Courses" -> scheme -> level -> programme):
  - Generic course queries ("courses", "ug courses", "programmes", ...) open
    the scheme picker (op "schemes") unless a scheme is explicitly named
    ("NEP courses", "Traditional courses") or already stored in context —
    those skip the picker and open the scheme's catalogue directly.
"""

from __future__ import annotations

import re
from typing import Any

from app.orchestrator.context import PROGRAMME_ALIASES  # noqa: F401  (alias lookup kept for API parity)

from app.catalogue.service import (  # noqa: E402
    get_category_subjects,
    get_curriculum_documents,
    get_learning_outcomes,
    get_major_subjects,
    get_minor_disciplines,
    get_semesters,
    get_subjects,
    has_programmes,
    has_schemes,
    programme_by_id,
    resolve_academic_scheme,
    resolve_programme,
)

from app.catalogue.knowledge import extract_requested_fields  # noqa: E402

# ---------------------------------------------------------------------------
# Category keywords (major / minor / VAC / SEC / AEC)
# ---------------------------------------------------------------------------

_CATEGORY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(major subjects?|major disciplines?|major discipline|major)\b", re.IGNORECASE), "major"),
    (re.compile(r"\b(minor subjects?|minor disciplines?|minor discipline|minor)\b", re.IGNORECASE), "minor"),
    (re.compile(r"\b(vac)\b|\bvalue[- ]added courses?\b|\bvalue added\b", re.IGNORECASE), "vac"),
    (re.compile(r"\b(sec)\b|\bskill[- ]enhancement courses?\b", re.IGNORECASE), "sec"),
    (re.compile(r"\b(aec)\b|\bability[- ]enhancement courses?\b", re.IGNORECASE), "aec"),
]


def detect_catalogue_category(text: str) -> str | None:
    """Detect a catalogue subject category mention (major/minor/vac/sec/aec)."""
    if not text:
        return None
    lowered = str(text).strip().lower()
    for pattern, category in _CATEGORY_PATTERNS:
        if pattern.search(lowered):
            return category
    return None


# ---------------------------------------------------------------------------
# Aspect keywords
# ---------------------------------------------------------------------------

_SEMESTER_WISE = re.compile(
    r"\bsemester[- ]?wise\b|\bsemesterwise\b|\bsemester subjects?\b"
    r"|\bsemester\b[^\n]{0,20}\b(subjects?|courses?|specializations?|syllabus)\b"
    r"|\b(subjects?|courses?|specializations?)\b[^\n]{0,20}\bsemester\b",
    re.IGNORECASE,
)
_CREDITS = re.compile(
    r"\bcredits?\b|\bcredit[ -](distribution|structure|system|break[- ]?down)\b",
    re.IGNORECASE,
)
_OUTCOMES = re.compile(
    r"\blearning outcomes?\b|\bprogramme outcomes?\b|\boutcomes?\b",
    re.IGNORECASE,
)
_CURRICULUM = re.compile(
    r"\bcurriculum\b|\bprogramme structure\b|\bcourse structure\b|\bstudy plan\b"
    r"|\bsyllabus\b|\bsyllabi\b",
    re.IGNORECASE,
)
_FEE = re.compile(
    r"\bfee structure\b|\bfees?\b|\btuition\b|\bprogramme fee\b|\bcourse fee\b"
    r"|\badmission fee\b|\bcost of the (programme|course)\b"
    r"|\bhow much\b|\bhow much does\b|\bhow much is\b|\bcharges?\b"
    r"|\bcost\b|\bpayment\b|\bprice\b|\bexpenses?\b|\bamount\b",
    re.IGNORECASE,
)
_ELIGIBILITY = re.compile(
    r"\beligibilit(y|ies)\b|\beligible\b|\badmission criteria\b|\bcriteria for admission\b"
    r"|\bwho can apply\b|\bwho is eligible\b|\bminimum qualification\b"
    r"|\bcan i apply\b|\bcan i join\b|\bam i eligible\b|\badmission requirements?\b"
    r"|\brequirements for (admission|joining|enrollment|enrolment)\b"
    r"|\bwhat are the requirements?\b|\badmission qualifications?\b",
    re.IGNORECASE,
)
# Explicit numeric semester reference ("semester 4", "sem 4", "4th semester").
_SEMESTER_NUMBER = re.compile(
    r"\b(?:sem(?:ester|ester\.)?\s*(\d{1,2})|(\d{1,2})(?:st|nd|rd|th)\s+sem)",
    re.IGNORECASE,
)
# Scheme-focused mention that should open the scheme hub (necessity: majority
# of the message is scheme words, not programme/level/semester inquiries).
_SCHEME_FOCUS = re.compile(
    r"\b(nep|nep2020|nep \d{4}|new education policy|national education policy"
    r"|fyugp|fygup|four year undergraduate programme"
    r"|traditional|conventional|cbcs|choice[- ]?based credit system"
    r"|old (curriculum|scheme))\b",
    re.IGNORECASE,
)
# A single programme scoped to a scheme: "BCA under NEP", "per NEP",
# "MSc under the traditional scheme" etc. → scheme-scoped programme overview.
_SCHEME_SCOPED = re.compile(
    r"\b(?:under|scoped under|per|as per|according to|within|through|from|in|going with)\b"
    r"[^\n]{0,25}\b"
    r"(nep\s*2020|nep|new education policy|national education policy"
    r"|fyugp|fygup|cbcs|choice[ ]?based credit system|traditional|conventional)\b"
    r"|\b(nep\s*2020|nep|new education policy|national education policy"
    r"|fyugp|fygup|cbcs|choice[ ]?based credit system|traditional|conventional)\b"
    r"[^\n]{0,25}\bprogramme\b",
    re.IGNORECASE,
)
_OVERVIEW = re.compile(
    r"\btell me about\b|\boverview of\b|\boverview\b|\bdetails of\b"
    r"|\binformation about\b|\binfo about\b|\bwhat is\b|\bwhat's\b|\bwhat are\b|\babout\b"
    r"|\bexplain\b|\bdescribe\b|\bintroduce\b|\bwhat will i learn\b|\bwhat do i learn\b",
    re.IGNORECASE,
)
# Strong "show me this programme" markers that beat a vague semantic topic.
_EXPLICIT_OVERVIEW = re.compile(
    r"\btell me about\b|\boverview( of|\b)|\bdetails?\b|"
    r"\binfo(rmation)? about\b|\bintroduce\b|\binformation\b|\babout the\b"
    r"|\bexplain\b|\bdescribe\b|\bwhat will i learn\b|\bwhat do i learn\b",
    re.IGNORECASE,
)

# "List"-type phrases.
_LIST_PHRASES = [
    "list of courses", "list of programmes", "list of programs", "list of course",
    "available courses", "available programmes", "programmes available", "courses available",
    "course catalogue", "programme catalogue", "academic catalogue", "course list",
    "programme list", "show courses", "show programmes", "courses offered",
    "programmes offered", "all courses", "all programmes", "what courses",
    "what programmes", "which courses", "which programmes", "browse courses",
    "ug courses", "ug programmes", "ug programs", "pg courses", "pg programmes",
    "pg programs", "phd courses", "phd programmes", "undergraduate courses",
    "undergraduate programmes", "postgraduate courses", "postgraduate programmes",
    "under graduate courses", "post graduate courses",
]

# A subject code / name lookup against the active uploaded curriculum
# ("what is MCA-204", "tell me about the subject C101 in BCA").
_SUBJECT_SEARCH = re.compile(
    r"\b(subject|paper|course|module)\b[^\n]{0,40}\b([A-Z]{1,5}[ ]?[-/]?[ ]?\d{2,5})\b"
    r"|\b([A-Z]{1,5}[ ]?[-/]?[ ]?\d{2,5})\b[^\n]{0,40}\b(subject|paper|course|module)\b",
    re.IGNORECASE,
)
_LEVEL_WORD_MAP: dict[str, str] = {
    "ug": "ug", "undergraduate": "ug", "under graduate": "ug", "undergrad": "ug",
    "pg": "pg", "postgraduate": "pg", "post graduate": "pg",
    "phd": "phd", "ph.d": "phd", "doctorate": "phd",
    "integrated": "integrated",
}
_LEVEL_WORDS = re.compile(
    r"\b(" + "|".join(sorted(_LEVEL_WORD_MAP, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Bare course-discovery words ("courses", "programmes", "show me courses").
_GENERIC_COURSE_WORDS = re.compile(
    r"\bcourses?\b|\bprogrammes?\b|\bprograms?\b",
    re.IGNORECASE,
)

# Field-level signals that have no dedicated op below (duration, documents,
# majors/minors lists, scheme) but still deserve catalogue routing.
_GRANULAR_GATE = re.compile(
    r"\bdur(?:ation|ations)\b|\bhow long\b|\bhow many years\b"
    r"|\bcourse length\b|\bprogramme length\b"
    r"|\bdocuments?\b|\bpaperwork\b|\brequired docs?\b"
    r"|\bmajors?\b|\bminors?\b|\bschemes?\b|\bacademic scheme\b",
    re.IGNORECASE,
)


def _is_generic_course_query(lowered: str) -> bool:
    """True for bare course-discovery phrasing ("courses", "programmes", ...)."""
    return bool(_GENERIC_COURSE_WORDS.search(lowered))


def detect_catalogue_aspect(text: str) -> str | None:
    """Detect a coarse catalogue aspect: list/overview/semesters/credits/outcomes/curriculum."""
    if not text:
        return None
    lowered = str(text).strip().lower()
    if _SEMESTER_WISE.search(lowered):
        return "semesters"
    if _CREDITS.search(lowered):
        return "credits"
    if _OUTCOMES.search(lowered):
        return "outcomes"
    if _CURRICULUM.search(lowered):
        return "curriculum"
    if _has_list_signal(lowered):
        return "list"
    if _OVERVIEW.search(lowered):
        return "overview"
    return None


def _has_list_signal(lowered: str) -> bool:
    if any(phrase and phrase in lowered for phrase in _LIST_PHRASES):
        return True
    return bool(_LEVEL_WORDS.search(lowered)) and bool(
        re.search(r"\bcourses?\b|\bprogrammes?\b|\bprograms?\b", lowered)
    )


def _level_from(lowered: str, entities: Any | None, ctx: Any | None) -> str | None:
    if entities is not None and getattr(entities, "level", None):
        return entities.level
    m = _LEVEL_WORDS.search(lowered)
    if m:
        return _LEVEL_WORD_MAP.get(m.group(0).lower())
    if ctx is not None:
        lvl = getattr(ctx, "catalogue_level", None) or getattr(ctx, "level", None)
        if lvl:
            return lvl
    return None


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def programme_overview_request(programme_id: str) -> dict[str, Any] | None:
    """Route a bare programme name ("Show BCA") to the catalogue overview.

    Returns a catalogue request when the programme exists in the catalogue,
    otherwise None (the existing programme-detail flow runs unchanged).
    """
    resolved = resolve_programme(programme_id)
    if not resolved:
        return None
    return {"op": "overview", "programme": resolved["id"], "code": resolved["code"], "name": resolved["name"]}


def detect_catalogue_request(
    text: str,
    ctx: Any | None = None,
    entities: Any | None = None,
) -> dict[str, Any] | None:
    """Detect an academic catalogue request. Returns an op dict or None.

    `ctx` and `entities` are the orchestrator's ConversationContext /
    ExtractedEntities instances (duck-typed, so tests may pass dicts/None).
    """
    if not text or len(str(text).strip()) < 3:
        return None
    lowered = str(text).strip().lower()
    category = detect_catalogue_category(lowered)

    # ---- Fast signal gate (skip DB entirely for unrelated messages) ----
    if not any(p.search(lowered) for p, _ in _CATEGORY_PATTERNS) and not (
        _SEMESTER_WISE.search(lowered)
        or _SEMESTER_NUMBER.search(lowered)
        or _CREDITS.search(lowered)
        or _OUTCOMES.search(lowered)
        or _CURRICULUM.search(lowered)
        or _FEE.search(lowered)
        or _ELIGIBILITY.search(lowered)
        or _has_list_signal(lowered)
        or _is_generic_course_query(lowered)
        or "subjects" in lowered
        or "specializations" in lowered
        or "papers" in lowered
        or "modules" in lowered
        or _OVERVIEW.search(lowered)
        or _SCHEME_FOCUS.search(lowered)
        or _GRANULAR_GATE.search(lowered)
    ):
        return None

    # College queries keep their own richer flow.
    try:
        from app.college.aliases import is_college_reference
        if is_college_reference(text):
            return None
    except Exception:
        pass

    resolved = _resolve_programme_for_message(lowered, ctx, entities)
    prog_id = resolved["id"] if resolved else None

    # ---- 0b) Granular field requests (compound / unhandled / missing data) --
    # Field-level questions route to the knowledge resolver ("requested" op):
    #   * compound asks      ("fee and eligibility of bca")
    #   * fields without a   ("how long is bca", "documents required for
    #     dedicated op         bca admission", "which scheme does bca follow")
    # A single field that already has a dedicated op below (fee / eligibility /
    # credits / subjects / outcomes / curriculum / vac / sec / aec / majors /
    # minors) falls through so those handlers stay the single source of truth
    # (their own data gates decide; without data they already fall to RAG).
    if resolved:
        granular_fields = extract_requested_fields(lowered)
        if granular_fields == ["scheme"] and _SCHEME_SCOPED.search(lowered):
            granular_fields = []
        if len(granular_fields) == 1 and granular_fields[0] not in ("duration", "scheme", "documents"):
            granular_fields = []
        if granular_fields:
            return {
                "op": "requested",
                "programme": prog_id,
                "code": resolved.get("code"),
                "name": resolved.get("name"),
                "fields": granular_fields,
                "cascade_query": str(text),
            }

    # ---- 1) Category-specific ops (major / minor / VAC / SEC / AEC) -------
    # Runs before listing so "list of VAC courses" means the VAC pool, not a
    # scheme/level picker.
    if category:
        return _category_request(category, resolved, prog_id, entities, ctx)

    # ---- 1b) Subject code lookup against an active uploaded curriculum -----
    # The uploaded payload is the primary source for published programme
    # navigation, so a subject-code lookup is answered from it directly.
    subj_match = _SUBJECT_SEARCH.search(lowered)
    if subj_match and resolved:
        from app.catalogue.service import curriculum_subject_search
        try:
            hits = curriculum_subject_search(None, resolved["code"], subj_match.group(0))
        except Exception:
            hits = None
        if hits:
            code_query = next(
                (
                    g for g in subj_match.groups()
                    if g and re.search(r"[A-Z]{1,5}", g) and re.search(r"\d{2,5}", g)
                ),
                None,
            )
            return {
                "op": "curriculum_subject_search",
                "programme": prog_id,
                "code": code_query,
            }

    # ---- 2) Semester-wise subjects ----------------------------------------
    if _SEMESTER_WISE.search(lowered):
        return _semester_request(resolved, prog_id, entities, ctx)

    # ---- 2b) Bare numeric semester ("bca semester 4", "sem 4", "4th semester") ----
    sem_num = _semester_number_from(lowered)
    if sem_num is not None:
        if resolved:
            if _has_semester_data(prog_id, sem_num):
                return {"op": "semester_subjects", "programme": prog_id, "semester": sem_num, "category": None}
            return None
        if has_programmes():
            return {"op": "programme_pick", "pending": {"op": "semester_subjects", "semester": sem_num}}
        return None

    # ---- 3) Credits --------------------------------------------------------
    if _CREDITS.search(lowered):
        if resolved:
            if _has_credit_data(prog_id):
                return {"op": "credits", "programme": prog_id}
            return None
        if has_programmes():
            return {"op": "programme_pick", "pending": {"op": "credits"}}
        return None

    # ---- 4) Learning outcomes ---------------------------------------------
    if _OUTCOMES.search(lowered):
        if resolved:
            if get_learning_outcomes(prog_id):
                return {"op": "outcomes", "programme": prog_id}
            return None
        if has_programmes():
            return {"op": "programme_pick", "pending": {"op": "outcomes"}}
        return None

    # ---- 5) Curriculum docs / syllabus ------------------------------------
    # Structured programme info + the linked curriculum PDFs have priority
    # over generic RAG; without documents the query falls through to RAG.
    if _CURRICULUM.search(lowered):
        if resolved:
            if get_curriculum_documents(prog_id):
                return {"op": "curriculum", "programme": prog_id}
            return None
        if has_programmes():
            return {"op": "programme_pick", "pending": {"op": "curriculum"}}
        return None

    # ---- 6) Fee structure (structured priority over legacy data) ----------
    if _FEE.search(lowered):
        if resolved:
            prog = programme_by_id(prog_id)
            if prog and prog.get("fee_structure"):
                return {"op": "fee", "programme": prog_id}
            return None
        return None  # no programme — legacy slot-fill asks "which programme?"

    # ---- 7) Eligibility ----------------------------------------------------
    if _ELIGIBILITY.search(lowered):
        if resolved:
            prog = programme_by_id(prog_id)
            if prog and prog.get("eligibility"):
                return {"op": "eligibility", "programme": prog_id}
            return None
        return None

    # ---- 8) Programme listing (scheme hierarchy) --------------------------
    # "Courses" / "UG Courses" / "Programmes" first determine the academic
    # scheme: an explicitly named scheme (or one already in context) opens
    # that scheme's catalogue directly; otherwise the scheme picker is shown.
    # When a specific programme is already resolved a bare "programme" word is
    # an overview request ("Explain the BCA programme"), not a course listing.
    if _has_list_signal(lowered) or (_is_generic_course_query(lowered) and not resolved):
        if resolved:
            return {"op": "subjects", "programme": prog_id, "category": None}
        level = _level_from(lowered, entities, ctx)
        scheme = _resolve_scheme_for_message(lowered, ctx)
        if scheme:
            if _scheme_has_programmes(scheme, level):
                return {
                    "op": "list",
                    "scheme": scheme["id"],
                    "scheme_name": scheme.get("name"),
                    "scheme_code": scheme.get("code"),
                    "level": level,
                }
            return None
        if has_schemes() and has_programmes():
            return {"op": "schemes", "level": level}
        return None

    # ---- 8b) Plain subject listing ("subjects in BCA") ----------------------
    if (
        (
            "subjects" in lowered
            or "courses" in lowered
            or "specializations" in lowered
            or "papers" in lowered
            or "modules" in lowered
        )
        and resolved
        and not _SEMESTER_WISE.search(lowered)
    ):
        return {"op": "subjects", "programme": prog_id, "category": None}

    # ---- 8c) Scheme hub ("I want to know about NEP" / bare scheme mention) ----
    # Opens a scheme overview with DB-driven options (programmes, major/minor,
    # semester structure, credit framework, VAC/SEC/AEC pools, curriculum,
    # learning outcomes) instead of a bare picker.
    if not resolved and _SCHEME_FOCUS.search(lowered):
        scheme = _resolve_scheme_for_message(lowered, ctx)
        if scheme and _is_scheme_focused(lowered):
            return {
                "op": "scheme",
                "scheme": scheme["id"],
                "scheme_name": scheme.get("name"),
                "scheme_code": scheme.get("code"),
            }

    # ---- 8d) Single programme under an explicit scheme ---------------------
    # "BCA under NEP" / "per NEP" — the programme is known and the user scopes
    # it to a named academic scheme: answer with the programme overview with
    # the scheme attached (engine applies scheme context for tabs / RAG).
    if resolved and _SCHEME_SCOPED.search(lowered):
        scheme = _resolve_scheme_for_message(lowered, ctx)
        return {
            "op": "overview",
            "programme": prog_id,
            "code": resolved["code"],
            "name": resolved["name"],
            "scheme": scheme["id"] if scheme else None,
            "scheme_name": scheme.get("name") if scheme else None,
            "scheme_code": scheme.get("code") if scheme else None,
        }

    # ---- 9) Overview ("tell me about BCA") --------------------------------
    if _OVERVIEW.search(lowered):
        topic = getattr(entities, "topic", None) if entities is not None else None
        if topic and not _EXPLICIT_OVERVIEW.search(lowered):
            return None  # specific facts (fee/eligibility/...) keep the existing flow
        if resolved:
            return {"op": "overview", "programme": prog_id, "code": resolved["code"], "name": resolved["name"]}
        return None

    return None


# ---------------------------------------------------------------------------
# Category / semester helpers
# ---------------------------------------------------------------------------


def _category_request(
    category: str,
    resolved: dict[str, Any] | None,
    prog_id: str | None,
    entities: Any | None,
    ctx: Any | None,
) -> dict[str, Any] | None:
    if category == "major":
        if resolved:
            if not get_major_subjects(prog_id):
                return None
            req: dict[str, Any] = {"op": "subjects", "programme": prog_id, "category": "major"}
            sem = getattr(entities, "semester", None) if entities is not None else None
            if sem is not None:
                req["semester"] = int(sem)
            return req
        if has_programmes():
            return {"op": "programme_pick", "pending": {"op": "subjects", "category": "major"}}
        return None

    if category == "minor":
        if resolved:
            if not get_minor_disciplines(prog_id):
                return None
            return {"op": "minors", "programme": prog_id}
        if has_programmes():
            return {"op": "programme_pick", "pending": {"op": "minors"}}
        return None

    # VAC / SEC / AEC — programme-specific first, then the shared pool.
    items = get_category_subjects(category, programme_id=prog_id if resolved else None)
    if not items:
        items = get_category_subjects(category)
    if not items:
        return None
    return {"op": category, "programme": prog_id if resolved else None}


def _semester_request(
    resolved: dict[str, Any] | None,
    prog_id: str | None,
    entities: Any | None,
    ctx: Any | None,
) -> dict[str, Any] | None:
    if resolved:
        sems = get_semesters(prog_id)
        if not sems:
            return None
        sem = getattr(entities, "semester", None) if entities is not None else None
        if sem is not None:
            return {"op": "semester_subjects", "programme": prog_id, "semester": int(sem), "category": None}
        return {"op": "semesters", "programme": prog_id, "category": None}
    if has_programmes():
        return {"op": "programme_pick", "pending": {"op": "semesters", "category": None}}
    return None


def _resolve_programme_for_message(lowered: str, ctx: Any | None, entities: Any | None) -> dict[str, Any] | None:
    # Full-text resolution first — catches "BA English" style references that
    # the alias extractor reduces to "ba".
    if len(lowered) >= 5:
        r = resolve_programme(lowered)
        if r:
            return r
    entity_prog = getattr(entities, "programme", None) if entities is not None else None
    ctx_refs: list[Any] = []
    if ctx is not None:
        ctx_refs = [
            getattr(ctx, "programme", None),
            getattr(ctx, "catalogue_programme", None),
            getattr(ctx, "catalogue_programme_code", None),
        ]
    for ref in ([entity_prog] + [c for c in ctx_refs if c]):
        if ref:
            r = resolve_programme(ref)
            if r:
                return r
    # Bare level mention ("phd", "ug", "pg") doesn't map to a programme code
    # directly; if exactly one programme exists at that level, treat it as the
    # resolved programme (e.g. "Am I eligible for PhD in CS?" -> PhD CS).
    if entity_prog in ("phd", "ug", "pg", "integrated"):
        try:
            from app.catalogue.service import list_programmes
            rows = list_programmes(level=entity_prog)
            if len(rows) == 1:
                return resolve_programme(rows[0]["id"])
        except Exception:
            pass
    return None


def _has_credit_data(prog_id: str) -> bool:
    prog = programme_by_id(prog_id)
    if prog and prog.get("total_credits"):
        return True
    return any((s.get("credits") or 0) > 0 for s in get_subjects(programme_id=prog_id))


def _resolve_scheme_for_message(lowered: str, ctx: Any | None) -> dict[str, Any] | None:
    """Resolve an academic scheme from the message, else from conversation context.

    An explicit mention wins; otherwise a scheme already remembered on the
    conversation (ctx.academic_scheme / catalogue_scheme) is reused so that
    "courses" after "show NEP courses" keeps the same scheme.
    """
    explicit = resolve_academic_scheme(lowered)
    if explicit:
        return explicit
    if ctx is not None:
        for attr in ("academic_scheme", "catalogue_scheme_code", "catalogue_scheme"):
            value = getattr(ctx, attr, None)
            if value:
                resolved = resolve_academic_scheme(str(value))
                if resolved:
                    return resolved
    return None


def _scheme_has_programmes(scheme: dict[str, Any], level: str | None) -> bool:
    try:
        from app.catalogue.service import list_programmes
        return bool(list_programmes(level=level, scheme=scheme.get("id")))
    except Exception:
        return False


def _semester_number_from(lowered: str) -> int | None:
    """Extract an explicit numeric semester ("sem 4", "semester 4", "4th sem")."""
    m = _SEMESTER_NUMBER.search(lowered)
    if not m:
        return None
    for group in m.groups():
        if group and group.isdigit() and int(group) in range(1, 13):
            return int(group)
    return None


def _has_semester_data(prog_id: str, semester: int) -> bool:
    try:
        return any(
            (s.get("semester") or 0) == semester
            for s in get_subjects(programme_id=prog_id)
        )
    except Exception:
        return False


def _is_scheme_focused(lowered: str) -> bool:
    """True when the message is essentially a scheme request (short + scheme words).

    Long mixed messages ("what is the difference between NEP and traditional")
    are NOT treated as a hub request — they keep the existing flows.
    """
    word_count = len(lowered.split())
    if word_count <= 4:
        return True
    if _SCHEME_FOCUS.search(lowered) and not _SEMESTER_WISE.search(lowered) \
            and not _SEMESTER_NUMBER.search(lowered) \
            and not _has_list_signal(lowered):
        # "what is nep", "tell me about the new education policy" — scheme words
        # dominate; programme/semester/level words are absent.
        words = set(re.findall(r"\b[a-z0-9]+\b", lowered))
        focus_words = words & set(re.findall(r"\b[a-z0-9]+\b", _SCHEME_FOCUS.pattern))
        if focus_words and not any(
            w in words for w in ("programme", "program", "course", "courses", "subject", "subjects")
        ):
            return True
    return False