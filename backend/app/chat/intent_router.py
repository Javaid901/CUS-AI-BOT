"""
backend/app/chat/intent_router.py

Intent router + structured navigation for the CUS AI Assistant.

Classifies each user message as:
  - "broad"   → a top-level navigation intent (admission, courses, fee, etc.)
  - "select"  → an option selection from a previous structured response
  - "specific" → a free-form factual question that should go to RAG
  - "unknown" → nothing matched (also goes to RAG)

For broad/select intents, returns a structured JSON response with
options or detail fields, rendered by the frontend as chips/cards.
"""

from __future__ import annotations

import re
from typing import Any

from app.utils.logging import log

# ---------------------------------------------------------------------------
# 1.  Intent classification
# ---------------------------------------------------------------------------

# Broad keywords map to a category id and a human label.
_BROAD_KEYWORDS: dict[str, str] = {
    "admission": "admissions",
    "admissions": "admissions",
    "ug": "ug",
    "pg": "pg",
    "phd": "phd",
    "integrated": "integrated",
    "dyd": "dyd",
    "courses": "courses",
    "fee": "fee",
    "fee structure": "fee",
    "fees": "fee",
    "results": "results",
    "result": "results",
    "datesheet": "datesheet",
    "date sheet": "datesheet",
    "syllabus": "syllabus",
    "scholarship": "scholarships",
    "scholarships": "scholarships",
    "previous papers": "previous_papers",
    "previous paper": "previous_papers",
    "notices": "notices",
    "notice": "notices",
    "downloads": "downloads",
    "download": "downloads",
    "hostel": "hostel",
    "examination": "examination",
    "exam": "examination",
    "departments": "departments",
    "department": "departments",
    "colleges": "colleges",
    "college": "colleges",
    "contact": "contact",
}

# Question-word prefixes → specific factual query, not broad navigation.
_SPECIFIC_PREFIXES = (
    "what", "whats", "what is", "what are", "what was", "what were",
    "when", "when is", "when are", "when was", "when were",
    "where", "where is", "where are",
    "how", "how is", "how are", "how do", "how can", "how to",
    "who", "who is", "who are",
    "which", "which is", "which are",
    "why", "why is", "why are",
    "can", "could", "would", "will", "is", "are", "do", "does",
    "tell me", "show me", "list", "explain", "describe",
)


def classify(message: str) -> tuple[str, str | None]:
    """
    Returns (intent_type, category).
    
    intent_type: "broad" | "select" | "specific" | "unknown"
    category:    the matched category id, or None

    Uses semantic intent classification first, falling back to keyword
    matching when confidence is low. This ensures semantically equivalent
    questions like "Available programs" and "What can I study?" both
    resolve to the same "courses" intent.
    """
    text = message.strip().lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    words = text.split()

    # ---- Step 1: Specific question starters (what, how, etc.) ----
    # Check BEFORE semantic classifier so factual questions ("what is the
    # mission?") go to RAG rather than being misclassified as navigation.
    for prefix in _SPECIFIC_PREFIXES:
        if text.startswith(prefix):
            return "specific", None

    # ---- Step 2: Semantic classification for navigation intents ----
    # Only runs for messages that don't start with a question word.
    try:
        from app.orchestrator.intent_classifier import classify_broad
        cat, confidence, debug = classify_broad(text)
        if cat is not None:
            log.info(
                "Semantic intent: '%s' -> '%s' (conf=%.3f, time=%.1fms)",
                text[:40], cat, confidence, debug.get("elapsed_ms", 0),
            )
            return "broad", cat
    except Exception:
        log.warning("Semantic classifier failed, falling back to keyword", exc_info=True)

    # ---- Step 3: Broad keywords (exact match or single word) ----
    if text in _BROAD_KEYWORDS:
        return "broad", _BROAD_KEYWORDS[text]
    if len(words) == 1 and words[0] in _BROAD_KEYWORDS:
        return "broad", _BROAD_KEYWORDS[words[0]]

    return "specific", None


def is_option_selection(message: str) -> bool:
    """Check if the message looks like a known structured option ID."""
    text = message.strip().lower()
    # Build known IDs dynamically from the navigation tree.
    return text in _ALL_KNOWN_IDS


# ---------------------------------------------------------------------------
# 1b.  Navigation data tree
# ---------------------------------------------------------------------------

_PROGRAMMES: dict[str, list[dict[str, str]]] = {
    "ug": [
        {"id": "ba", "label": "BA (Bachelor of Arts)"},
        {"id": "bsc", "label": "B.Sc (Bachelor of Science)"},
        {"id": "bcom", "label": "B.Com (Bachelor of Commerce)"},
        {"id": "bba", "label": "BBA (Bachelor of Business Administration)"},
        {"id": "bca", "label": "BCA (Bachelor of Computer Applications)"},
        {"id": "btech", "label": "B.Tech (Bachelor of Technology)"},
        {"id": "bed", "label": "B.Ed (Bachelor of Education)"},
    ],
    "pg": [
        {"id": "ma", "label": "MA (Master of Arts)"},
        {"id": "msc", "label": "M.Sc (Master of Science)"},
        {"id": "mcom", "label": "M.Com (Master of Commerce)"},
        {"id": "mba", "label": "MBA (Master of Business Administration)"},
        {"id": "mca", "label": "MCA (Master of Computer Applications)"},
        {"id": "med", "label": "M.Ed (Master of Education)"},
    ],
    "integrated": [
        {"id": "integrated_ba_ma", "label": "BA + MA (5 Years)"},
        {"id": "integrated_bsc_msc", "label": "B.Sc + M.Sc (5 Years)"},
        {"id": "integrated_bba_mba", "label": "BBA + MBA (4 Years)"},
        {"id": "integrated_bca_mca", "label": "BCA + MCA (5 Years)"},
        {"id": "integrated_bed_med", "label": "B.Ed-M.Ed (3 Years)"},
    ],
}

_PROGRAMME_DETAILS: dict[str, dict[str, Any]] = {
    "ba": {
        "title": "BA (Bachelor of Arts)",
        "fields": [
            {"label": "Duration", "value": "3 Years"},
            {"label": "Eligibility", "value": "10+2 from a recognized board in relevant stream"},
            {"label": "Admission Mode", "value": "CUET UG / Centralized Admission Portal (jkadmissions.in)"},
            {"label": "Fee", "value": "Approx. Rs 3,500 per year"},
            {"label": "Documents Required", "value": "10th & 12th marksheets, ID proof, category certificate (if applicable), passport-size photo"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
            {"id": "prospectus", "label": "Open Prospectus"},
        ],
    },
    "bsc": {
        "title": "B.Sc (Bachelor of Science)",
        "fields": [
            {"label": "Duration", "value": "3 Years"},
            {"label": "Eligibility", "value": "10+2 with Science stream (PCM/PCB)"},
            {"label": "Streams", "value": "Medical, Non-Medical"},
            {"label": "Admission Mode", "value": "CUET UG / Centralized Admission Portal"},
            {"label": "Fee", "value": "Approx. Rs 4,500 per year"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
        ],
    },
    "bcom": {
        "title": "B.Com (Bachelor of Commerce)",
        "fields": [
            {"label": "Duration", "value": "3 Years"},
            {"label": "Eligibility", "value": "10+2 from a recognized board"},
            {"label": "Admission Mode", "value": "CUET UG / Centralized Admission Portal"},
            {"label": "Fee", "value": "Approx. Rs 3,500 per year"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
        ],
    },
    "bba": {
        "title": "BBA (Bachelor of Business Administration)",
        "fields": [
            {"label": "Duration", "value": "3 Years"},
            {"label": "Eligibility", "value": "10+2 from a recognized board"},
            {"label": "Admission Mode", "value": "CUET UG / Centralized Admission Portal"},
            {"label": "Fee", "value": "Approx. Rs 10,500 per year"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
        ],
    },
    "bca": {
        "title": "BCA (Bachelor of Computer Applications)",
        "fields": [
            {"label": "Duration", "value": "3 Years"},
            {"label": "Eligibility", "value": "10+2 with Mathematics as a subject"},
            {"label": "Admission Mode", "value": "CUET UG / Centralized Admission Portal"},
            {"label": "Fee", "value": "Approx. Rs 10,500 per year"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
        ],
    },
    "btech": {
        "title": "B.Tech (Bachelor of Technology)",
        "fields": [
            {"label": "Duration", "value": "4 Years"},
            {"label": "Eligibility", "value": "10+2 with Physics, Chemistry, Mathematics"},
            {"label": "Admission Mode", "value": "JEE Main / University Entrance"},
            {"label": "Specializations", "value": "Computer Science, Civil, Mechanical, Biomedical, IT"},
            {"label": "Fee", "value": "Approx. Rs 19,900 total"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
            {"id": "prospectus", "label": "Open Prospectus"},
        ],
    },
    "bed": {
        "title": "B.Ed (Bachelor of Education)",
        "fields": [
            {"label": "Duration", "value": "2 Years"},
            {"label": "Eligibility", "value": "Graduation from a recognized university"},
            {"label": "Admission Mode", "value": "CUET / University Entrance"},
            {"label": "Fee", "value": "Approx. Rs 10,000 per year"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
        ],
    },
    "ma": {
        "title": "MA (Master of Arts)",
        "fields": [
            {"label": "Duration", "value": "2 Years"},
            {"label": "Eligibility", "value": "Bachelor's degree in relevant subject"},
            {"label": "Admission Mode", "value": "CUET PG"},
            {"label": "Fee", "value": "Approx. Rs 5,500 per year"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
        ],
    },
    "msc": {
        "title": "M.Sc (Master of Science)",
        "fields": [
            {"label": "Duration", "value": "2 Years"},
            {"label": "Eligibility", "value": "B.Sc in relevant subject"},
            {"label": "Admission Mode", "value": "CUET PG"},
            {"label": "Fee", "value": "Approx. Rs 6,500 per year"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
        ],
    },
    "mcom": {
        "title": "M.Com (Master of Commerce)",
        "fields": [
            {"label": "Duration", "value": "2 Years"},
            {"label": "Eligibility", "value": "B.Com or equivalent"},
            {"label": "Admission Mode", "value": "CUET PG"},
            {"label": "Fee", "value": "Approx. Rs 5,500 per year"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
        ],
    },
    "mba": {
        "title": "MBA (Master of Business Administration)",
        "fields": [
            {"label": "Duration", "value": "2 Years"},
            {"label": "Eligibility", "value": "Any bachelor's degree"},
            {"label": "Admission Mode", "value": "CUET PG / University Entrance"},
            {"label": "Fee", "value": "Approx. Rs 15,000 per year"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
        ],
    },
    "mca": {
        "title": "MCA (Master of Computer Applications)",
        "fields": [
            {"label": "Duration", "value": "2 Years"},
            {"label": "Eligibility", "value": "BCA or bachelor's with Mathematics"},
            {"label": "Admission Mode", "value": "CUET PG"},
            {"label": "Fee", "value": "Approx. Rs 15,000 per year"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
        ],
    },
    "med": {
        "title": "M.Ed (Master of Education)",
        "fields": [
            {"label": "Duration", "value": "2 Years"},
            {"label": "Eligibility", "value": "B.Ed or equivalent"},
            {"label": "Admission Mode", "value": "CUET PG"},
            {"label": "Fee", "value": "Approx. Rs 10,000 per year"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
        ],
    },
    "phd": {
        "title": "PhD Programmes",
        "fields": [
            {"label": "Duration", "value": "Minimum 3 Years"},
            {"label": "Eligibility", "value": "Master's degree with minimum 55% marks (50% for reserved categories)"},
            {"label": "Admission Mode", "value": "University Entrance Exam + Interview / UGC NET / CSIR NET / JRF"},
            {"label": "Fee", "value": "Varies by programme"},
        ],
        "actions": [
            {"id": "fee", "label": "View Fee Structure"},
            {"id": "dates", "label": "Important Dates"},
        ],
    },
}

_TOPICS: dict[str, dict[str, Any]] = {
    "fee": {
        "title": "Fee Structure",
        "message": "Select a programme level to view fee details.",
        "options": [
            {"id": "ug", "label": "UG programmes"},
            {"id": "pg", "label": "PG programmes"},
            {"id": "integrated", "label": "Integrated programmes"},
            {"id": "phd", "label": "PhD"},
        ],
    },
    "results": {
        "title": "Results",
        "message": "Select a result category.",
        "options": [
            {"id": "nep_results", "label": "NEP Results"},
            {"id": "ug_results", "label": "UG Results"},
            {"id": "pg_results", "label": "PG Results"},
            {"id": "integrated_results", "label": "Integrated/Professional Results"},
            {"id": "bed_results", "label": "B.Ed Results"},
            {"id": "btech_results", "label": "B.Tech Results"},
        ],
    },
    "datesheet": {
        "title": "Date Sheet / Examination Schedule",
        "message": "Select a category for schedule information.",
        "options": [
            {"id": "ug_dates", "label": "UG Examination Schedule"},
            {"id": "pg_dates", "label": "PG Examination Schedule"},
            {"id": "btech_dates", "label": "B.Tech Date Sheet"},
            {"id": "bed_dates", "label": "B.Ed Date Sheet"},
        ],
    },
    "scholarships": {
        "title": "Scholarships",
        "message": "Cluster University students can apply for scholarships through:",
        "options": [
            {"id": "nsp", "label": "National Scholarship Portal (NSP)"},
            {"id": "post_matric", "label": "Post Matric Scholarship (J&K)"},
            {"id": "merit", "label": "Merit-cum-Means Scholarships"},
            {"id": "ugc_fellowship", "label": "UGC Fellowships (Research)"},
        ],
    },
    "previous_papers": {
        "title": "Previous Year Entrance Papers",
        "message": "Select a subject to download previous year papers.",
        "options": [
            {"id": "computer_apps", "label": "Computer Applications"},
            {"id": "biochemistry", "label": "Biochemistry"},
            {"id": "botany", "label": "Botany"},
            {"id": "business_admin", "label": "Business Administration"},
            {"id": "chemistry", "label": "Chemistry"},
            {"id": "commerce", "label": "Commerce"},
            {"id": "economics", "label": "Economics"},
            {"id": "english", "label": "English"},
            {"id": "environmental_science", "label": "Environmental Science"},
            {"id": "geography", "label": "Geography"},
            {"id": "it", "label": "Information Technology"},
            {"id": "journalism", "label": "Journalism & Mass Communication"},
            {"id": "physics", "label": "Physics"},
            {"id": "political_science", "label": "Political Science"},
            {"id": "zoology", "label": "Zoology"},
        ],
    },
    "notices": {
        "title": "Notices & Notifications",
        "message": "Select a category of notices.",
        "options": [
            {"id": "general", "label": "General Notices"},
            {"id": "examination", "label": "Examination Notices"},
            {"id": "admission", "label": "Admission Notices"},
            {"id": "jobs", "label": "Job Notifications"},
        ],
    },
    "downloads": {
        "title": "Downloads",
        "message": "Select what you would like to download.",
        "options": [
            {"id": "about_us", "label": "About Us"},
            {"id": "migration", "label": "Migration Certificate"},
            {"id": "directory", "label": "University Directory"},
            {"id": "hostel", "label": "Hostel Information"},
            {"id": "bulletin", "label": "Bulletin of Information"},
            {"id": "statutes", "label": "Statutes and Act"},
            {"id": "nep_regulations", "label": "NEP-2020 Draft Regulations"},
            {"id": "anti_ragging", "label": "Anti-Ragging Affidavit"},
            {"id": "abc_guidelines", "label": "ABC Registration Guidelines"},
        ],
    },
    "hostel": {
        "title": "Hostel",
        "message": "Hostel information is available in the Downloads section. Contact the university for current availability and fee details.",
        "options": [
            {"id": "downloads", "label": "Go to Downloads"},
        ],
    },
    "examination": {
        "title": "Examination Services",
        "message": "Select the service you need.",
        "options": [
            {"id": "schedule", "label": "Examination Schedule"},
            {"id": "results", "label": "Results"},
            {"id": "model_papers", "label": "Model Papers"},
            {"id": "fee_structure_exam", "label": "Exam Fee Structure"},
            {"id": "division_improvement", "label": "Division Improvement"},
        ],
    },
    "departments": {
        "title": "Academic Departments",
        "message": "Cluster University has the following PG departments.",
        "options": [
            {"id": "dept_science", "label": "Science Departments"},
            {"id": "dept_arts", "label": "Arts & Humanities"},
            {"id": "dept_commerce", "label": "Commerce & Management"},
            {"id": "dept_education", "label": "Education"},
            {"id": "dept_engineering", "label": "Engineering & Technology"},
        ],
    },
    "colleges": {
        "title": "Constituent Colleges",
        "message": "Select a college to learn more.",
        "options": [
            {"id": "gcw_ma_road", "label": "GCW M.A. Road"},
            {"id": "amar_singh", "label": "Amar Singh College"},
            {"id": "sp_college", "label": "S.P. College"},
            {"id": "iase", "label": "IASE Srinagar"},
            {"id": "bemina", "label": "GDC Bemina"},
            {"id": "anantnag", "label": "GDC Anantnag"},
            {"id": "pulwama", "label": "GDC Pulwama"},
            {"id": "kulgam", "label": "GDC Kulgam"},
        ],
    },
}

_COLLEGE_DETAILS: dict[str, dict[str, Any]] = {
    "gcw_ma_road": {
        "title": "Government College for Women M.A. Road",
        "fields": [
            {"label": "Located In", "value": "Srinagar"},
            {"label": "Type", "value": "Constituent College"},
            {"label": "Established", "value": "1975"},
            {"label": "NAAC Grade", "value": "A"},
            {"label": "Principal", "value": "Prof. (Dr.) Shama Kouser"},
        ],
    },
    "amar_singh": {
        "title": "Amar Singh College",
        "fields": [
            {"label": "Located In", "value": "Srinagar"},
            {"label": "Type", "value": "Constituent College"},
            {"label": "Established", "value": "1889"},
            {"label": "NAAC Grade", "value": "A"},
        ],
    },
    "sp_college": {
        "title": "S.P. College",
        "fields": [
            {"label": "Located In", "value": "Srinagar"},
            {"label": "Type", "value": "Constituent College"},
            {"label": "Established", "value": "1942"},
            {"label": "NAAC Grade", "value": "A"},
        ],
    },
    "iase": {
        "title": "Institute of Advanced Studies in Education",
        "fields": [
            {"label": "Located In", "value": "Srinagar"},
            {"label": "Type", "value": "Constituent College"},
            {"label": "Established", "value": "1960"},
            {"label": "NAAC Grade", "value": "A"},
        ],
    },
    "bemina": {
        "title": "GDC Bemina",
        "fields": [
            {"label": "Located In", "value": "Bemina, Srinagar"},
            {"label": "Type", "value": "Constituent College"},
            {"label": "Established", "value": "2013"},
            {"label": "NAAC Grade", "value": "B+"},
            {"label": "Streams", "value": "Science, Humanities"},
        ],
    },
    "anantnag": {
        "title": "GDC Anantnag",
        "fields": [
            {"label": "Located In", "value": "Anantnag, South Kashmir"},
            {"label": "Type", "value": "Constituent College"},
            {"label": "Established", "value": "1999"},
            {"label": "NAAC Grade", "value": "B"},
        ],
    },
    "pulwama": {
        "title": "GDC Pulwama",
        "fields": [
            {"label": "Located In", "value": "Pulwama"},
            {"label": "Type", "value": "Constituent College"},
            {"label": "Established", "value": "2005"},
            {"label": "NAAC Grade", "value": "B"},
        ],
    },
    "kulgam": {
        "title": "GDC Kulgam",
        "fields": [
            {"label": "Located In", "value": "Kulgam"},
            {"label": "Type", "value": "Constituent College"},
            {"label": "Established", "value": "2006"},
            {"label": "NAAC Grade", "value": "B"},
        ],
    },
}

# ---------------------------------------------------------------------------
# 3.  Navigation state and response builder
# ---------------------------------------------------------------------------

# In-memory conversation state per chat_id.
# We store a simple path list: e.g. ["admissions", "ug"] means the user
# is browsing UG programmes under Admissions.
import threading

_nav_state: dict[str, list[str]] = {}
_nav_lock = threading.Lock()


def get_nav_path(chat_id: str) -> list[str]:
    with _nav_lock:
        return list(_nav_state.get(chat_id, []))


def set_nav_path(chat_id: str, path: list[str]) -> None:
    with _nav_lock:
        _nav_state[chat_id] = list(path)


def clear_nav(chat_id: str) -> None:
    with _nav_lock:
        _nav_state.pop(chat_id, None)


def advance_path(chat_id: str, selection: str) -> list[str]:
    """Append a selection to the navigation path."""
    with _nav_lock:
        path = _nav_state.get(chat_id, [])
        path = list(path)
        path.append(selection)
        _nav_state[chat_id] = path
        return path


# Top-level welcome options (shown at start or after a clear).
WELCOME_OPTIONS: dict[str, Any] = {
    "type": "options",
    "title": "How can I help you?",
    "message": "I can help you with admissions, courses, fee details, exam schedules, and more. Select a topic below or type your question.",
    "options": [
        {"id": "admissions", "label": "Admissions"},
        {"id": "courses", "label": "Courses"},
        {"id": "fee", "label": "Fee Structure"},
        {"id": "results", "label": "Results"},
        {"id": "datesheet", "label": "Date Sheet"},
        {"id": "scholarships", "label": "Scholarships"},
        {"id": "colleges", "label": "Colleges"},
        {"id": "examination", "label": "Examinations"},
        {"id": "contact", "label": "Contact Info"},
    ],
}


def _build_known_ids() -> set[str]:
    ids = set()
    ids.update(_BROAD_KEYWORDS.keys())
    ids.add("back")
    for programmes in _PROGRAMMES.values():
        for p in programmes:
            ids.add(p["id"])
    ids.update(_PROGRAMME_DETAILS.keys())
    ids.update(_COLLEGE_DETAILS.keys())
    # Also register gdc_ prefixed IDs for consistency with college/data.py
    for cid in ["gdc_anantnag", "gdc_pulwama", "gdc_kulgam"]:
        ids.add(cid)
    for topic in _TOPICS.values():
        for opt in topic.get("options", []):
            ids.add(opt["id"])
    for opt in WELCOME_OPTIONS.get("options", []):
        ids.add(opt["id"])
    return ids


# Pre-computed set of every navigable option ID for fast lookup.
_ALL_KNOWN_IDS: set[str] = _build_known_ids()


def get_broad_response(category: str) -> dict[str, Any]:
    """Return a structured response for a broad navigation category."""
    # Normalise.
    cat = category.strip().lower()

    if cat == "admissions":
        return {
            "type": "options",
            "title": "Admissions",
            "message": "What type of programme are you interested in?",
            "options": [
                {"id": "ug", "label": "Undergraduate (UG)"},
                {"id": "pg", "label": "Postgraduate (PG)"},
                {"id": "phd", "label": "PhD"},
                {"id": "integrated", "label": "Integrated Programmes"},
                {"id": "dyd", "label": "Design Your Degree (DYD)"},
            ],
        }
    if cat == "ug":
        return {
            "type": "options",
            "title": "UG Programmes",
            "message": "Select a programme to see details.",
            "options": _PROGRAMMES["ug"],
        }
    if cat == "pg":
        return {
            "type": "options",
            "title": "PG Programmes",
            "message": "Select a programme to see details.",
            "options": _PROGRAMMES["pg"],
        }
    if cat == "integrated":
        return {
            "type": "options",
            "title": "Integrated Programmes",
            "message": "Select an integrated programme.",
            "options": _PROGRAMMES["integrated"],
        }
    if cat in _TOPICS:
        t = _TOPICS[cat]
        return {"type": "options", "title": t["title"], "message": t["message"], "options": t["options"]}
    if cat == "courses":
        return {
            "type": "options",
            "title": "Courses",
            "message": "Select a programme level to see available courses.",
            "options": [
                {"id": "ug", "label": "UG Courses"},
                {"id": "pg", "label": "PG Courses"},
                {"id": "integrated", "label": "Integrated Courses"},
            ],
        }
    if cat == "contact":
        return {
            "type": "detail",
            "title": "Contact Information",
            "fields": [
                {"label": "Address", "value": "Cluster University of Srinagar, Gogji-Bagh, Srinagar, Jammu & Kashmir - 190008"},
                {"label": "Phone", "value": "0194-2311340"},
                {"label": "Email", "value": "info@cusrinagar.edu.in"},
                {"label": "Website", "value": "www.cusrinagar.edu.in"},
                {"label": "Anti-Ragging Helpline", "value": "1800-180-5522"},
            ],
        }

    # Fallback to the RAG pipeline.
    return {"type": "rag"}


def get_selection_response(chat_id: str, selection: str) -> dict[str, Any]:
    """Handle a user's option selection, advance the nav path."""
    # Treat "back" as popping the last level.
    if selection == "back":
        path = _nav_state.get(chat_id, [])
        if path:
            path.pop()
            _nav_state[chat_id] = path
        # Show the parent level.
        if path:
            return get_broad_response(path[-1])
        return WELCOME_OPTIONS

    # Check if this is a known programme detail.
    if selection in _PROGRAMME_DETAILS:
        advance_path(chat_id, selection)
        detail = _PROGRAMME_DETAILS[selection]
        return {"type": "detail", "title": detail["title"], "fields": detail["fields"], "actions": detail["actions"]}

    # Check college details.
    if selection in _COLLEGE_DETAILS:
        advance_path(chat_id, selection)
        detail = _COLLEGE_DETAILS[selection]
        return {"type": "detail", "title": detail["title"], "fields": detail["fields"]}

    # If it matches a broad category, treat as navigation.
    if selection in _BROAD_KEYWORDS:
        advance_path(chat_id, selection)
        return get_broad_response(selection)

    # Check if it matches a programme level.
    if selection in _PROGRAMMES:
        advance_path(chat_id, selection)
        return get_broad_response(selection)

    # Check if it matches a known topic.
    if selection in _TOPICS:
        advance_path(chat_id, selection)
        return get_broad_response(selection)

    # Fallback: RAG.
    return {"type": "rag"}
