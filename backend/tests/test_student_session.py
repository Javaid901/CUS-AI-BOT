"""
backend/tests/test_student_session.py

Regression battery for the session-based student portal (Student Session
Manager + engine wiring).

Covers:
  - Tolerant credential parsing (pipe-delimited, labeled, multiline)
  - Semester parsing incl. roman numerals ("Semester V")
  - Spelling-tolerant portal entry / service / logout detection
  - Secure session lifecycle (no password ever stored; masked summary)
  - Engine end-to-end: login once -> results fetch -> logout clears session
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

from app.database import SessionLocal, create_all
from app.auth.security import hash_password
from app.orchestrator.context import ConversationContext
from app.orchestrator.state import ConversationState
from app.orchestrator import student_session as ssm

import app.models  # noqa: F401  (register models before any session)
create_all()  # isolated test DB (see conftest.py) needs the full schema

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# 1. Credential parsing tolerance
# ---------------------------------------------------------------------------


def test_parse_credentials() -> None:
    print("-- parse_credentials: multi-format tolerance --")
    cases = [
        ("CUS-2023-0001||student123", ("CUS-2023-0001", "student123")),
        ("CUS20230001||pass", ("CUS20230001", "pass")),
        ("Registration No: CUS-2023-0001 Password: hello123", ("CUS-2023-0001", "hello123")),
        ("ID: 23001\nPass: secret99", ("23001", "secret99")),
        ("roll number: 23001 password: abc", ("23001", "abc")),
        ("CUS-2023-0001\nstudent123", ("CUS-2023-0001", "student123")),
        ("CUS-2023-0001 student123", ("CUS-2023-0001", "student123")),
    ]
    for raw, expected in cases:
        got = ssm.parse_credentials(raw)
        check(f"credentials [{raw!r}] -> {expected}", got == expected, f"got={got}")

    for raw in ("hello", "help me log in", "", "password123"):
        got = ssm.parse_credentials(raw)
        check(f"credentials rejected [{raw!r}]", got is None, f"got={got}")


# ---------------------------------------------------------------------------
# 2. Semester parsing
# ---------------------------------------------------------------------------


def test_extract_semester() -> None:
    print("-- semester parsing (digits, ordinals, roman) --")
    for raw, expected in [
        ("Semester 5", 5),
        ("sem 5", 5),
        ("5th semester", 5),
        ("Semester V", 5),
        ("semester iv", 4),
        ("fourth semester", 4),
        ("semester three", 3),
    ]:
        got = ssm.extract_semester(raw)
        check(f"semester [{raw!r}] -> {expected}", got == expected, f"got={got}")

    for raw in ("attendance", "my profile", "", "helpdesk"):
        check(f"no semester in [{raw!r}]", ssm.extract_semester(raw) is None)


# ---------------------------------------------------------------------------
# 3. Intent detection w/ spelling tolerance
# ---------------------------------------------------------------------------


def test_intent_detection() -> None:
    print("-- portal entry / logout / service typos --")
    for phrase in ("student portal", "Student Services", "my account", "studnt portal", "open student services plz"):
        check(f"portal entry [{phrase!r}]", ssm.is_portal_entry(phrase), f"phrase={phrase}")

    for phrase in ("logout", "sign out", "end session please", "log off"):
        check(f"logout [{phrase!r}]", ssm.is_logout_request(phrase))

    for typo, service in (
        ("show my attandance", "attendance"),
        ("check my reslts", "results"),
        ("semster admission form", "semester_admission"),
        ("regstration form", "registration"),
    ):
        got = ssm.fuzzy_service_match(typo)
        check(f"fuzzy [{typo!r}] -> {service}", got == service, f"got={got}")

    # Logged-in exact ids route to the corresponding service.
    for ident in ("fee", "admit_card", "xerox_copy", "semester_admission", "profile"):
        got = ssm.exact_student_service(ident)
        check(f"exact id [{ident!r}]", got == ident, f"got={got}")

    check("no false-positive 'fee structure'", ssm.fuzzy_service_match("what is the fee structure") is None)
    check("no false-positive admissions query", ssm.fuzzy_service_match("what is the admission process") is None)


# ---------------------------------------------------------------------------
# 4. Session lifecycle security
# ---------------------------------------------------------------------------


class _FakeStudent:
    id = uuid.uuid4()
    reg_no = "CUS-TEST-0001"
    name = "Test Student"
    programme = "bca"
    academic_scheme = "nep2020"
    college = "Test College"
    batch = "2023-2026"
    current_semester = 4


def test_session_lifecycle() -> None:
    print("-- session lifecycle: store / valid / summary / clear --")
    state = ConversationState(chat_id="ss-t")
    ssm.set_session(state, _FakeStudent, session_token="tok123", session_expiry_ts=9999999999.0)

    check("session present", ssm.has_session(state))
    check("session valid", ssm.valid_session(state))
    check("session not expired", not ssm.session_expired(state))
    check("reg no stored", state.student_reg_no == "CUS-TEST-0001")
    check("current semester remembered", state.current_semester == 4)
    check("semester list present", 4 in state.semester_list)
    check("context authenticated", state.context.authenticated is True)
    check("college stored", state.student_college == "Test College")
    check("batch stored", state.student_batch == "2023-2026")

    summary = ssm.session_summary(state)
    check("summary has reg no", summary and summary.get("reg_no") == "CUS-TEST-0001")
    check("summary NEVER leaks token/password",
          summary is not None and not any(k in summary for k in ("password", "token")))

    # Expiry
    state.student_session_expiry = 1.0
    check("session expired after ttl", ssm.session_expired(state))

    # Clear
    state.student_session_expiry = 9999999999.0
    ssm.clear_session(state)
    check("session erased", not ssm.has_session(state))
    check("context de-authenticated", state.context.authenticated is False)
    check("no token left", state.context.student_session_token is None)
    check("semester list cleared", state.semester_list == [])
    check("service auth cleared", state.service_auth == {})


# ---------------------------------------------------------------------------
# 5. Engine end-to-end (real SSE events)
# ---------------------------------------------------------------------------


def _fresh_student(db, reg_no: str):
    from app.models import Student
    s = Student(
        id=uuid.uuid4(),
        reg_no=reg_no,
        roll_no="99999",
        name="E2E Portal Student",
        father_name="F",
        mother_name="M",
        gender="Male",
        category="General",
        college="Test College",
        programme="bca",
        academic_scheme="nep2020",
        current_semester=4,
        admission_year=2023,
        batch="2023-2026",
        status="active",
        hashed_password=hash_password("student123"),
        is_active=True,
    )
    db.add(s)
    db.commit()
    return s


def _cleanup_student(db, student_id) -> None:
    from app.models import Student, StudentSession
    db.query(StudentSession).filter(StudentSession.student_id == student_id).delete()
    db.query(Student).filter(Student.id == student_id).delete()
    db.commit()


async def _ask(db, user_id, chat, msg):
    from app.orchestrator.engine import process
    events = []
    async for ev in process(db, user_id, msg, chat):
        events.append(ev)
    return events


def _titles(evs):
    return [str(e.get("title")) for e in evs if e.get("title")]


def test_engine_login_results_logout() -> None:
    db = SessionLocal()
    reg_no = f"CUS-TEST-{uuid.uuid4().hex[:6]}"
    student = None
    try:
        student = _fresh_student(db, reg_no)
        user_id = f"u-{uuid.uuid4()}"
        chat = f"chat-{uuid.uuid4()}"

        # 1) Request a service -> auth form
        evs = asyncio.run(_ask(db, user_id, chat, "results"))
        check("e2e step1 -> auth_form",
              any(e.get("type") == "auth_form" for e in evs),
              f"titles={_titles(evs)}")

        # 2) Submit credentials (pipe-delimited frontend payload)
        evs = asyncio.run(_ask(db, user_id, chat, f"{reg_no}||student123"))
        titles = _titles(evs)
        check("e2e step2 -> results detail",
              any("Examination Results" in t for t in titles), f"titles={titles}")

        # Profile chip attached to the result
        ctxs = [e.get("context", {}) for e in evs if e.get("context")]
        check("e2e step2 -> student chip attached",
              any("student" in c for c in ctxs), f"ctxs={ctxs}")

        # 3) Session now active — a second service needs NO re-auth
        from app.orchestrator.state import get_state
        state = asyncio.run(get_state(chat))
        check("e2e session active", ssm.has_session(state))
        check("e2e current semester remembered", state.current_semester == 4)

        # 4) Subjects for the logged-in student (catalogue semester_subjects)
        evs = asyncio.run(_ask(db, user_id, chat, "my subjects"))
        titles = _titles(evs)
        check("e2e step4 -> subjects (any catalogue detail)",
              len(titles) > 0, f"titles={titles}")

        # 5) Logout clears the session
        evs = asyncio.run(_ask(db, user_id, chat, "logout"))
        state2 = asyncio.run(get_state(chat))
        check("e2e step5 -> session cleared", not ssm.has_session(state2))

        # 6) After logout a service request demands re-authentication (session truly gone)
        evs = asyncio.run(_ask(db, user_id, chat, "results"))
        check("e2e step6 -> auth form again after logout",
              any(e.get("type") == "auth_form" for e in evs),
              f"titles={_titles(evs)}")

        # 7) Re-login with credentials works again
        evs = asyncio.run(_ask(db, user_id, chat, f"{reg_no}||student123"))
        titles = _titles(evs)
        check("e2e step7 -> re-login works",
              any("Examination Results" in t for t in titles), f"titles={titles}")
    finally:
        if student is not None and student.id:
            _cleanup_student(db, student.id)
        db.close()


def test_engine_portal_menu() -> None:
    db = SessionLocal()
    try:
        user_id = f"u-{uuid.uuid4()}"
        chat = f"chat-{uuid.uuid4()}"
        evs = asyncio.run(_ask(db, user_id, chat, "student portal"))
        titles = _titles(evs)
        check("e2e portal typed -> Student Services menu",
              any("Student Services" in t for t in titles), f"titles={titles}")
    finally:
        db.close()


def main() -> None:
    test_parse_credentials()
    test_extract_semester()
    test_intent_detection()
    test_session_lifecycle()
    test_engine_login_results_logout()
    test_engine_portal_menu()
    print(f"\nTotal checks: {len(PASS) + len(FAIL)} | Passed: {len(PASS)} | Failed: {len(FAIL)}")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()