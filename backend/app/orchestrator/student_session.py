"""
backend/app/orchestrator/student_session.py

Centralized Student Session Manager — the single reusable component behind the
session-based student portal.

Responsibilities:
  - Parse student credentials from a chat message (multi-format tolerance)
  - Provide the authenticated session lifecycle (authenticate → explore →
    logout / expiry) stored on ConversationState + ConversationContext
  - Resolve the student's dynamic semester list from backend data
  - Detect portal entry / logout / semester / service requests with spelling
    tolerance

Security rules (MUST follow; mirrors ServiceAuthState):
      - Passwords/secret tokens are NEVER stored on state or context
      - The opaque session token lives only in memory (context.service_session
        + per-service ServiceAuthState) and is destroyed on logout/expiry
      - Responses never expose credentials; summaries are masked
"""

from __future__ import annotations

import re
import time
from typing import Any

# ---------------------------------------------------------------------------
# Session lifecycle constants
# ---------------------------------------------------------------------------

STUDENT_SESSION_HOURS = 24  # matches StudentSession expires_at used at login

# Services whose result changes per semester — mirror of the frontend
# _SEMESTER_SERVICES list so the backend drives semester selection too.
SEMESTER_DEPENDENT_SERVICES: frozenset[str] = frozenset(
    {
        "results",
        "admit_card",
        "exam_form",
        "attendance",
        "registration",
    }
)

# Phrases that open the student portal (entry points). Keep in sync with the
# spelling-tolerant matcher below (also matches fuzzy variants).
ENTRY_PHRASES: tuple[str, ...] = (
    "student services",
    "student service",
    "student portal",
    "student login",
    "student dashboard",
    "student account",
    "my account",
    "online services",
    "services for students",
    "student section",
    "student facilities",
    "existing student",
    "i am a student",
    "i am a existing student",
    "current student",
    "online student services",
    "portal services",
)

_LOGOUT_PHRASES: tuple[str, ...] = (
    "logout",
    "log out",
    "log off",
    "logoff",
    "sign out",
    "signout",
    "end session",
    "exit student portal",
    "exit portal",
    "clear student session",
    "clear session",
    "deactivate session",
)

# Explicitly tolerated misspellings → canonical service id. The fuzzy matcher
# below generalizes this for anything we have not enumerated.
_MISSPELLINGS: dict[str, str] = {
    "attandance": "attendance",
    "attendence": "attendance",
    "reseults": "results",
    "reslts": "results",
    "rsults": "results",
    "resilt": "results",
    "regstration": "registration",
    "regstratoin": "registration",
    "regsitration": "registration",
    "trancript": "transcript",
    "transript": "transcript",
    "xeroxx copy": "xerox_copy",
    "xery": "xerox_copy",
    "semster": "semester_admission",
    "semester addmission": "semester_admission",
    "admisison form": "semester_admission",
    "profile": "profile",
    "helpdesk": "helpdesk",
}

_SUBJECT_PHRASES: tuple[str, ...] = (
    "my subjects",
    "subjects",
    "subject list",
    "semester subjects",
)

# ---------------------------------------------------------------------------
# Credential parsing (multi-format tolerance)
# ---------------------------------------------------------------------------

# "reg: CUS-2023-0001", "ID: CUS1", "roll number: 23-45", "Registration No 123456"
_CRED_LABEL_RE = re.compile(
    r"\b(?:registration\s*(?:no\.?|number)?|roll\s*(?:no\.?|number)?|student\s*(?:no\.?|id)?|reg\s*no\.?|uid|id)\s*[:=]?\s*([A-Za-z0-9][A-Za-z0-9._\-/]{2,})",
    re.IGNORECASE,
)
_PASS_LABEL_RE = re.compile(
    r"\b(?:password|pass|pwd|pin)\s*[:=]\s*(\S+)",
    re.IGNORECASE,
)


def _looks_like_reg_no(value: str) -> bool:
    """A reg number must be non-empty, contain a digit and not be a password."""
    v = value.strip()
    if len(v) < 4:
        return False
    return bool(re.search(r"\d", v))


def parse_credentials(message: str) -> tuple[str, str] | None:
    """Parse a free‐text credential submission into (reg_no, password).

    Supported layouts:
      - "CUS20230001||pass123"        (frontend auth form payload)
      - "ID: CUS-2023-0001 Password: pass123"   (labeled)
      - multiline:  "CUS-2023-0001\\npass123"
      - space separated: "CUS-2023-0001 pass123"

    Returns (reg_no, password) or None when the message cannot be parsed.
    The password is returned only to the caller within this request — it is
    NEVER stored.
    """
    if not message or not message.strip():
        return None
    text = message.strip()

    # 1) Pipe-delimited frontend payload: value1||value2
    if "||" in text:
        parts = text.split("||", 1)
        reg = parts[0].strip()
        pwd = parts[1].strip() if len(parts) > 1 else ""
        if reg and pwd:
            return reg, pwd
        return None

    # 2) Labeled form ("ID: x Password: y"). Regexes are anchored to labels so
    #    stray words are not mistaken for credentials.
    m_reg = _CRED_LABEL_RE.search(text)
    m_pwd = _PASS_LABEL_RE.search(text)
    if m_reg and m_pwd:
        reg = m_reg.group(1).strip()
        if _looks_like_reg_no(reg):
            return reg, m_pwd.group(1).strip().rstrip(".,;")
        return None

    # 3) Line based (newlines) — first line reg, second line password.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 2 and _looks_like_reg_no(lines[0]):
        return lines[0], lines[1]

    # 4) Space separated — first token reg, remainder password.
    tokens = text.split()
    if len(tokens) >= 2 and _looks_like_reg_no(tokens[0]):
        return tokens[0], " ".join(tokens[1:])

    return None


def has_credential_shape(message: str) -> bool:
    """True when this message is shaped like a credential bundle (used to
    scrub it from query/context/audit before it is shown back to the user)."""
    return parse_credentials(message) is not None


# ---------------------------------------------------------------------------
# Semester parsing (numbers, ordinals, roman numerals like "Semester V")
# ---------------------------------------------------------------------------

_ROMAN = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
}
_SEM_ROMAN_RE = re.compile(
    r"\b(?:sem(?:ester)?[ ._-]*|s\.?[ ._-]*)(i|ii|iii|iv|v|vi|vii|viii|ix|x)\s*$",
    re.IGNORECASE,
)


def extract_semester(text: str) -> int | None:
    """Resolve a semester to a number. Supports:
      * "sem 4" / "4th semester" / "fourth semester" (via extractor)
      * "semester V" / "sem v" (roman numerals)
      * bare numbers ("5", "sem 5")
    Returns None when no unambiguous semester is mentioned.
    """
    if not text:
        return None
    from app.orchestrator.extractor import _extract_semester

    num, _word = _extract_semester(text)
    if num is not None:
        return int(num)

    lowered = text.strip().lower().rstrip(".,;:)")

    # Roman numeral following "sem"/"semester".
    m = _SEM_ROMAN_RE.search(lowered)
    if m:
        return _ROMAN[m.group(1).lower()]

    # Ordinal / cardinal words: first .. eighth, three .. eight.
    for word, nval in (
        ("first", 1), ("second", 2), ("third", 3), ("fourth", 4),
        ("fifth", 5), ("sixth", 6), ("seventh", 7), ("eighth", 8),
        ("one", 1), ("two", 2), ("three", 3), ("four", 4),
        ("five", 5), ("six", 6), ("seven", 7), ("eight", 8),
    ):
        if word in lowered:
            return nval

    # Bare digits anywhere ("3", "sem 3", "3rd").
    digits = re.findall(r"\b(\d{1,2})\b", lowered)
    for d in digits:
        val = int(d)
        if 1 <= val <= 20:
            return val
    return None


# ---------------------------------------------------------------------------
# Intents: portal entry / logout / service detection with spelling tolerance
# ---------------------------------------------------------------------------


def _levenshtein(a: str, b: str) -> int:
    """Classic Levenshtein distance (bounded by a small threshold)."""
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    if abs(len(a) - len(b)) > 3:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(
                    prev[j] + 1,
                    cur[j - 1] + 1,
                    prev[j - 1] + (ca != cb),
                )
            )
        prev = cur
    return prev[-1]


def _tol(phrase_len: int) -> int:
    return 2 if phrase_len >= 10 else 1


def _fuzzy_phrase(text: str, phrase: str, tol: int | None = None) -> bool:
    """True if `phrase` matches text, allowing small per-token typos (≤1 edit)
    across a contiguous window."""
    text_words = text.split()
    phrase_words = phrase.split()
    limit = tol if tol is not None else _tol(len(phrase))
    if not text_words:
        return False
    # Windows must start at index 0 — a phrase matching from the first word
    # (e.g. a typo in its first token) was previously never checked.
    for start in range(max(0, len(text_words) - len(phrase_words) + 1)):
        window = text_words[start : start + len(phrase_words)]
        if len(window) != len(phrase_words):
            continue
        total = sum(
            _levenshtein(a, b) for a, b in zip(window, phrase_words)
        )
        if total <= limit:
            return True
    return False


def is_portal_entry(message: str) -> bool:
    """True when the message asks to open the student portal."""
    text = message.strip().lower()
    for phrase in ENTRY_PHRASES:
        if phrase in text or _fuzzy_phrase(text, phrase):
            return True
    return False


def is_logout_request(message: str) -> bool:
    """True when the user explicitly asks to sign out of the portal."""
    text = message.strip().lower()
    return any(p in text for p in _LOGOUT_PHRASES)


def is_subjects_request(message: str) -> bool:
    """True when the (authenticated) user asks about their own subjects.

    Programme-qualified queries ("BCA subjects") are left to the public
    catalogue flow, not the student session.
    """
    text = " ".join(message.strip().lower().split())
    if not text:
        return False
    try:
        from app.orchestrator.extractor import extract_entities
        if extract_entities(message).programme:
            return False
    except Exception:
        pass
    if "subject" not in text:
        return False
    # Short, self-referential subject queries ("subjects", "my subjects",
    # "which subjects do I have") belong to the student session.
    return len(text.split()) <= 5 and not text.startswith(("how many", "total ", "list of all"))


def fuzzy_service_match(message: str) -> str | None:
    """Spelling-tolerant service intent matching.

    Returns a service id when the message clearly refers to one of the
    registered student services even with typos ("attandance", "reslts").
    """
    text = " ".join(message.strip().lower().split())
    if not text:
        return None

    # Explicit misspelling dictionary (fast path, deterministic).
    for miss, service in _MISSPELLINGS.items():
        if miss in text:
            return service

    # Fuzzy over canonical keywords (exclude trivial tokens to avoid noise).
    from app.orchestrator.extractor import SERVICE_PATTERNS, SERVICE_KEYWORDS

    for phrase in SERVICE_PATTERNS:
        if phrase in text:
            return SERVICE_KEYWORDS[phrase]
        if len(phrase) >= 4 and _fuzzy_phrase(text, phrase):
            return SERVICE_KEYWORDS[phrase]
    return None


def exact_student_service(message: str) -> str | None:
    """Exact match for a service id / display name (used when logged in so
    the "fee" chip etc. route to the service instead of generic navigation)."""
    from app.services.registry import SERVICE_NAMES, get_connector_by_display

    text = " ".join(message.strip().lower().split())
    if not text:
        return None
    if text in SERVICE_NAMES:
        return text
    conn = get_connector_by_display(text)
    if conn is not None:
        return conn.name
    # "fee receipt" style would already map via fuzzy; keep exactness here.
    return None


def portal_menu_selectable(message: str) -> bool:
    """Whether the message is a plain menu/display text (used to decide if we
    should proceed to service routing when we cannot identify a service)."""
    return exact_student_service(message) is not None


# ---------------------------------------------------------------------------
# Session lifecycle (single source of truth)
# ---------------------------------------------------------------------------


def has_session(state) -> bool:
    return getattr(state, "student_reg_no", None) is not None


def session_expired(state) -> bool:
    expiry = getattr(state, "student_session_expiry", None)
    if expiry is None:
        expiry = getattr(state.context, "student_session_expiry", None)
    if not expiry:
        return False
    return time.time() > float(expiry)


def valid_session(state) -> bool:
    return has_session(state) and not session_expired(state)


def semester_required(service: str) -> bool:
    return service in SEMESTER_DEPENDENT_SERVICES


def resolve_semester_list(state, db=None, student_id: str | None = None) -> list[int]:
    """Backend-driven semester list for a student.

    Start from the Student record's current_semester and extend with the
    distinct semesters present in the demo results/admit-card tables.
    """
    sems: set[int] = set()
    current = getattr(state, "student_semester", None)
    if current:
        sems.update(range(1, int(current) + 1))

    if student_id is None:
        student_id = getattr(state.context, "student_id", None) or getattr(state, "student_id", None)
    if student_id and db is not None:
        try:
            from app.models.demo_models import StudentResult

            rows = (
                db.query(StudentResult.semester)
                .filter(StudentResult.student_id == student_id)
                .all()
            )
            sems.update(r[0] for r in rows if r[0])
        except Exception:
            pass

    ordered = sorted(int(s) for s in sems if isinstance(s, int))
    if not ordered:
        ordered = [1]
    return ordered


def set_session(state, student, session_token: str, session_expiry_ts: float, db=None) -> None:
    """Persist an authenticated student session across state + context.

    Only the opaque token and expiry are stored — never the password.
    """
    ctx = state.context

    state.student_reg_no = student.reg_no
    state.student_name = student.name
    state.student_programme = student.programme
    state.student_semester = student.current_semester
    state.student_academic_scheme = student.academic_scheme
    state.student_college = student.college
    state.student_batch = student.batch
    state.student_login_timestamp = time.time()
    state.student_session_expiry = session_expiry_ts
    state.current_semester = student.current_semester
    # Pass db (ctx.student_id is only set below, so the explicit id too) so the
    # semester list covers every semester present in the results tables rather
    # than only 1..current_semester.
    state.semester_list = resolve_semester_list(state, db=db, student_id=str(student.id))

    ctx.authenticated = True
    ctx.student_id = str(student.id)
    ctx.student_college = student.college
    ctx.student_batch = student.batch
    ctx.student_session_token = session_token
    ctx.student_login_timestamp = state.student_login_timestamp
    ctx.student_session_expiry = session_expiry_ts
    ctx.semester_list = list(state.semester_list)
    ctx.current_semester = state.current_semester
    if student.academic_scheme:
        ctx.academic_scheme = student.academic_scheme


def clear_session(state) -> None:
    """Erase the entire student session (logout / expiry / eviction)."""
    _PRIVATE_FIELDS = (
        "student_reg_no",
        "student_name",
        "student_programme",
        "student_semester",
        "student_academic_scheme",
        "student_college",
        "student_batch",
        "student_login_timestamp",
        "student_session_expiry",
        "student_session_id",
        "current_semester",
        "semester_list",
    )
    for f in _PRIVATE_FIELDS:
        if hasattr(state, f):
            setattr(state, f, None)
    state.semester_list = []

    ctx = state.context
    ctx.authenticated = False
    for f in (
        "student_id",
        "student_college",
        "student_batch",
        "student_session_token",
        "student_login_timestamp",
        "student_session_expiry",
        "semester_list",
        "current_semester",
    ):
        if hasattr(ctx, f):
            setattr(ctx, f, None if f != "semester_list" else [])

    # Per-service auth states are tied to the identity — destroy their tokens.
    state.service_auth.clear()

    state.service_context = None
    state.pending_service = None
    state.pending_action = None
    state.pending_query = None
    state.pending_params = {}


def session_summary(state) -> dict | None:
    """Masked public summary of the live session.

    Includes NO credentials and NO token. Used by the frontend profile card.
    """
    if not has_session(state):
        return None
    expiry = getattr(state, "student_session_expiry", None)
    return {
        "authenticated": True,
        "reg_no": state.student_reg_no,
        "name": state.student_name,
        "programme": state.student_programme,
        "semester": state.current_semester or state.student_semester,
        "academic_scheme": state.student_academic_scheme,
        "college": getattr(state, "student_college", None),
        "batch": state.student_batch,
        "semesters": list(state.semester_list or []),
        "session_expires": expiry,
    }


def portal_menu_payload(context: dict | None = None, message: str = "") -> dict:
    """The Student Services menu event (same services as the frontend)."""
    from app.services.registry import get_service_options

    return {
        "type": "options",
        "title": "Student Services",
        "message": message or "Select a service to continue:",
        "options": get_service_options(),
        "context": context or {},
    }