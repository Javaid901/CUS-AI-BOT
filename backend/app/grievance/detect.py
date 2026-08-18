"""
backend/app/grievance/detect.py

PHASE 4 — Grievance intent detection (public intake).

Conservative, marker-based detector that separates a GRIEVANCE (a complaint
about an existing service/process: not received, missing, wrong, delayed,
charged, etc.) from an INFORMATION QUERY ("where is my result?", "when will
the admit card come?", "how to check marks").

Design rules:
  * A complaint marker ("not received", "missing", "problem with", "wrong",
    "facing issue", ...) is REQUIRED — generic service mentions ("admit card",
    "results") alone are information queries and must NOT route to the
    grievance intake (the student portal connector handles those).
  * "file a grievance/complaint" phrased as a process question ("how to file")
    without a concrete problem stays an information query.
  * Question-framed informational messages ("where/when/how/what is my ...")
    with no complaint marker are always rejected.
  * The detector runs on BOTH the raw user message and the query-cleaned text,
    so typos ("mising", "recived") still trigger via an explicit misspelling
    list without fuzzy-matching everything.
"""

from __future__ import annotations

import re

# Complaint markers, most specific first (substring match on lowercased text).
_COMPLAINT_MARKERS: list[str] = [
    # delivery / receipt problems
    "not received",
    "haven't received",
    "hasn't received",
    "have not received",
    "has not received",
    "didn't receive",
    "did not receive",
    "never received",
    "not got",
    "didn't get",
    "did not get",
    "haven't got",
    "haven't gotten",
    "missing",
    "lost my",
    "not delivered",
    # status / generation problems
    "not generated",
    "not generating",
    "not reflecting",
    "not reflected",
    "not updated",
    "not updating",
    "not showing",
    "not visible",
    "not printed",
    "not available",
    "not uploaded",
    "not working",
    "not appearing",
    "no response",
    "not replying",
    "ignoring my",
    "stuck",
    "pending for",
    "not processed",
    "not issued",
    "not dispatched",
    # correctness problems
    "problem with",
    "problem in",
    "problem is",
    "issue with",
    "issue in",
    "issue is",
    "facing issue",
    "facing problem",
    "have an issue",
    "have a problem",
    "wrong",
    "error",
    "incorrect",
    "mismatch",
    "discrepancy",
    "is tampered",
    "tampered",
    # money problems
    "deducted",
    "overcharged",
    "charged twice",
    "not refunded",
    "refund not",
    "fee not",
    "wrongly charged",
    # eligibility / access problems
    "not eligible",
    "not allowed",
    "can't access",
    "cannot access",
    "cant access",
    "unable to access",
    "unable to login",
    "can't login",
    "cannot login",
    "cant login",
    "can't log in",
    "login not working",
    "not able to",
    # explicit complaint words
    "complaint",
    "complaining",
    "grievance",
    "harassment",
    "discriminated",
    "cheated",
    "refused to",
    "denied",
    # delay problems
    "delayed",
    "delay in",
    "very late",
    "not on time",
    # Roman-Urdu / Hinglish complaint markers
    "meri complaint",
    "mujhe complaint",
    "meri shikayat",
    "mujhe shikayat",
    "complaint file karni",
    "complaint karni",
    "complaint karani",
    "complaint hai",
    "shikayat hai",
    "complaint daalni",
    "complaint deni",
    "problem hai",
    "issue hai",
    "dikkat hai",
    "mujhe problem",
    "mujhe dikkat",
    "mera problem",
    "meri problem",
    "problem aa rahi",
    "nahi aa raha",
    "nahi aaya",
    "nahi aayi",
    "nahi aata",
    "nahi ho raha",
    "nahi ho rahi",
    "nahi mila",
    "nahi mili",
    "nahi mil raha",
    "nahi khul",
    "khul nahi",
    "login nahi",
    "log in nahi",
    "refund nahi",
    "charge zyada",
    "zyada charge",
    "galat",
    "galti",
    "der ho rahi",
    "bahut der",
    "kaafi der",
    "response nahi",
    "reply nahi",
]

# Negative-outcome frames that substring markers miss because of helper verbs
# ("has NOT BEEN generated", "isn't showing", "wasn't issued"). Only verb forms
# describing a service outcome/entitlement are listed — informational queries
# ("when will the admit card come?") never match these.
_NEGATIVE_OUTCOME_RE: list[str] = [
    r"\bnot\s+(been\s+)?(generated|issued|uploaded|updated|updating|processed|delivered|published|reflected|shown|printed|received|visible|dispatched|released)\b",
    r"\b(isn'?t|wasn'?t|weren'?t|hasn'?t|haven'?t|didn'?t|couldn'?t|can'?t)\s+(been\s+)?(generated|issued|uploaded|updated|updating|processed|delivered|published|reflected|shown|printed|received|visible|dispatched|released|showing)\b",
]

# Misspellings → the canonical phrase they stand for (matches against the raw
# message, so query-cleaning is not required to catch them).
_MISSPELLED_MARKERS: dict[str, str] = {
    "havent received": "not received",
    "grivance": "grievance",
    "grivances": "grievance",
    "didnt receive": "not received",
    "didnt get": "not received",
    "not recived": "not received",
    "not receivd": "not received",
    "not recieved": "not received",
    "not receivedd": "not received",
    "mising": "missing",
    "missig": "missing",
    "mising admit": "missing",
    "prob with": "problem with",
    "problem wid": "problem with",
    "issue wid": "issue with",
    "facing prob": "facing problem",
    "facing issu": "facing issue",
    "not workng": "not working",
    "not workin": "not working",
    "dident": "not received",
    "not genrated": "not generated",
    "not refleting": "not reflected",
    "not refleted": "not reflected",
    "not upated": "not updated",
    "not updatd": "not updated",
    "stck": "stuck",
    "not showng": "not showing",
    "not showin": "not showing",
    "not aavailable": "not available",
    "cudnt": "could not",
    "not printed": "not printed",
}

# Pure informational question frames — if the message matches one of these
# patterns AND has no complaint marker, it is NOT a grievance.
_INFORMATION_FRAMES: list[tuple[str, str]] = [
    (r"\bwhen\b", "when-question"),
    (r"\bwhere\b", "where-question"),
    (r"\bhow (do|can|to|is)\b", "how-question"),
    (r"\bwhat is\b", "what-question"),
    (r"\bwhat are\b", "what-question"),
    (r"\bwill (the|my|i)\b", "will-question"),
    (r"\bdo i need\b", "do-i-need"),
    (r"\bis there a\b", "is-there-a"),
    (r"\bcan i\b", "can-i"),
    (r"\bwho\b", "who-question"),
]

# Process questions: naming the grievance system ITSELF (cell/portal/process)
# is only a process question when framed as a QUERY. "I want to file a
# complaint about my hostel" stays a grievance (it names a concrete problem).
_PROCESS_NOUNS: list[str] = [
    "grievance cell",
    "grievance portal",
    "grievance redressal",
    "grievance process",
    "complaint box",
    "complaint process",
    "filing process",
    "process of filing",
    "process for filing",
    "process to file",
    "process of grievance",
    "what is a grievance",
    "what is grievance",
    "what is a complaint",
    "what is complaint",
    "how to file",
    "how do i file",
    "how can i file",
    "how to register a grievance",
    "how to register a complaint",
    "how to submit a grievance",
]

_QUERY_FRAMES: list[str] = [
    "how to",
    "how do i",
    "how can i",
    "what is",
    "what are",
    "what's a",
    "where can i",
    "where is the",
    "tell me about",
    "tell me how",
    "explain the",
    "explain how",
    "about the",
    "is there a",
    "does the",
    "can you tell",
]

# Category auto-suggestion: topic word -> category label.
CATEGORY_HINTS: dict[str, str] = {
    "fee": "Fees & Payments",
    "fees": "Fees & Payments",
    "payment": "Fees & Payments",
    "refund": "Fees & Payments",
    "scholarship": "Scholarship",
    "hostel": "Hostel & Accommodation",
    "mess": "Hostel & Accommodation",
    "examination": "Examination & Results",
    "exam": "Examination & Results",
    "result": "Examination & Results",
    "results": "Examination & Results",
    "marksheet": "Examination & Results",
    "marks": "Examination & Results",
    "grade card": "Examination & Results",
    "admit card": "Examination & Results",
    "admission": "Admission",
    "registration": "Admission",
    "library": "Library",
    "transport": "Transport",
    "bus": "Transport",
    "canteen": "Canteen & Campus",
    "lab": "Academic / Lab",
    "internship": "Academic / Internship",
    "mentor": "Academic / Mentor",
    "teacher": "Academic / Faculty",
    "professor": "Academic / Faculty",
    "attendance": "Academic / Attendance",
    "certificate": "Certificates & Verification",
    "transcript": "Certificates & Verification",
    "degree": "Certificates & Verification",
    "migration": "Certificates & Verification",
    "bonafide": "Certificates & Verification",
    "anti-ragging": "Anti-Ragging",
    "ragging": "Anti-Ragging",
}

CATEGORY_ORDER: list[str] = [
    "Examination & Results",
    "Fees & Payments",
    "Admission",
    "Certificates & Verification",
    "Scholarship",
    "Hostel & Accommodation",
    "Library",
    "Transport",
    "Canteen & Campus",
    "Academic / Faculty",
    "Academic / Attendance",
    "Academic / Internship",
    "Academic / Lab",
    "Anti-Ragging",
    "Other",
]

GRIEVANCE_DEFAULT_CATEGORY = "Other"

# Minimum length of a grievance statement (prevent 1-2 word noise like "ok").
MIN_GRIEVANCE_WORDS = 3


def _has_complaint_marker(text: str) -> tuple[bool, str | None]:
    """Return (found, matched_marker) for explicit complaint markers."""
    for marker in _COMPLAINT_MARKERS:
        if marker in text:
            return True, marker
    return False, None


def _has_misspelled_marker(text: str) -> tuple[bool, str | None]:
    for typo, canonical in _MISSPELLED_MARKERS.items():
        if typo in text:
            return True, canonical
    return False, None


def _has_negative_outcome(text: str) -> tuple[bool, str | None]:
    """Catch negative-outcome frames with helper verbs (has not been generated,
    isn't showing, wasn't issued...). Returns (found, matched_pattern)."""
    for pattern in _NEGATIVE_OUTCOME_RE:
        if re.search(pattern, text):
            return True, pattern
    return False, None


def _is_process_question(text: str) -> bool:
    """True only when the grievance SYSTEM is discussed as a QUERY (not when
    the user names a concrete problem alongside 'complaint'/'grievance')."""
    if not any(noun in text for noun in _PROCESS_NOUNS):
        return False
    return any(frame in text for frame in _QUERY_FRAMES)


def _is_informational_frame(text: str) -> bool:
    return any(re.search(pattern, text) for pattern, _ in _INFORMATION_FRAMES)


def suggest_category(text: str) -> str:
    """Auto-suggest a grievance category from topic words (best-effort)."""
    lower = text.lower()
    for topic, category in CATEGORY_HINTS.items():
        if topic in lower:
            return category
    return GRIEVANCE_DEFAULT_CATEGORY


def detect_grievance(text: str) -> dict:
    """Detect whether a message is a grievance (vs an information query).

    Returns:
      {"is_grievance": bool, "reason": str|None, "marker": str|None,
       "category": str}
    """
    raw = (text or "").strip().lower()
    if not raw:
        return {"is_grievance": False, "reason": "empty", "marker": None,
                "category": GRIEVANCE_DEFAULT_CATEGORY}

    # 1. A bare "how to file a grievance" process question is NOT a grievance.
    if _is_process_question(raw):
        return {"is_grievance": False, "reason": "process question",
                "marker": None, "category": GRIEVANCE_DEFAULT_CATEGORY}

    found, marker = _has_complaint_marker(raw)
    if not found:
        found, marker = _has_misspelled_marker(raw)
        reason = "misspelled marker"
    else:
        reason = f"marker: {marker}"

    if not found:
        found, marker = _has_negative_outcome(raw)
        if not found:
            return {"is_grievance": False, "reason": "no complaint marker",
                    "marker": None, "category": GRIEVANCE_DEFAULT_CATEGORY}
        reason = f"negative outcome: {marker}"

    # 2. Minimum substance: a complaint needs more than two words.
    words = [w for w in re.split(r"\W+", raw) if w]
    if len(words) < MIN_GRIEVANCE_WORDS:
        return {"is_grievance": False, "reason": "too short",
                "marker": marker, "category": GRIEVANCE_DEFAULT_CATEGORY}

    # 3. Informational questions are only exempt when framed as pure queries
    #    ("when will results come?" has no marker anyway). A complaint marker
    #    inside a where/when question is still a complaint ("where is my admit
    #    card? i haven't received it"). Keep it — the marker already won.
    return {"is_grievance": True, "reason": reason, "marker": marker,
            "category": suggest_category(raw)}


__all__ = [
    "GRIEVANCE_DEFAULT_CATEGORY",
    "MIN_GRIEVANCE_WORDS",
    "detect_grievance",
    "suggest_category",
]
