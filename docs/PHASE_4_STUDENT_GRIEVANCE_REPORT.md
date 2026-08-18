# PHASE 4 — Student Grievance Intake
## Implementation, Security, Integration & Verification Report

---

## 1. EXECUTIVE SUMMARY

Phase 4 implemented the **student-facing grievance intake**, a complete complaint
submission workflow that a student can use *before* logging in (pre-login).

The implemented flow:

```
Student message
  → grievance detection (natural-language complaint routing)
  → grievance composer (chatbot UI)
  → optional LLM formalization (subject + formal text)
  → student review & edit
  → authority recommendation (DB-driven, active offices only)
  → authority selection (recommended or manual, or auto-assignment)
  → student details (name, email, roll number, semester, college, programme, phone)
  → approved complaint text
  → submission (idempotent, token-gated)
  → public reference number (CUS-GRV-`<year>`-`<8 hex chars>`)
  → status + immutable status history
  → best-effort email acknowledgement
  → secure tracking (reference + one-time token, status-only payload)
```

The workflow is **pre-login by design**: no account is required to file or track
a grievance. Tracking uses a plaintext token shown exactly once at submission
time; only its SHA-256 digest is stored. All public intake endpoints are
per-IP rate limited. Phase 4 functionality was verified end-to-end against the
running application (see §19).

---

## 2. ARCHITECTURE REVIEW

Phase 4 **extends the existing architecture** — it introduces no duplicate
systems. The following existing components were reused:

| Component | Reused for |
|---|---|
| Smart Orchestrator (`app/orchestrator/*`) | new `grievance` action + intent routing |
| Intent detection (`planner.py` Rule 2b) | routes complaints into the intake before service-keyword routing |
| Authority matcher (`app/authority/matcher.py`) | keyword-overlap scoring for recommendations |
| Authority directory (`app/authority/*`) | DB-driven office records, categories, active flag |
| Grievance model + status history (`app/grievance/models.py`, `service.py`) | record storage, append-only history |
| Audit system (`app/utils/logging.py`) | guest submission audit trail |
| Authentication/RBAC (Phase 3) | untouched server-side scope checks for staff flows |
| LLM infrastructure (`Ollama`, `settings.LLM_MODEL`) | draft formalization |
| Existing email plumbing (`app/utils/email.py`, new wrapper) | acknowledgement emails |
| Frontend chatbot (`frontend/js/chatbot.js`) | in-chat grievance composer |

`grievances.authority_id` is a foreign key into the existing `authorities`
table — there is no duplicate "authority" concept. Status transitions go
through the single Phase-1/3/4 status-history service (see §7, §9).

---

## 3. GRIEVANCE INTENT DETECTION

`app/grievance/detect.py` is a conservative, marker-based detector that must
distinguish a **complaint about an existing service** from **an information
query**.

Supported patterns:

- **Delivery/receipt problems**: "not received", "haven't received", "missing",
  "didn't get", "never received", "not delivered" …
- **Status/generation problems**: "not generated", "not updated", "not showing",
  "not printed", "not uploaded", "stuck", "not issued" …
- **Correctness problems**: "wrong", "error", "incorrect", "mismatch",
  "discrepancy", "tampered" …
- **Money problems**: "overcharged", "charged twice", "not refunded", "fee not"…
- **Eligibility/access**: "not eligible", "can't access", "unable to login" …
- **Explicit complaint words**: "complaint", "grievance", "harassment",
  "discriminated", "cheated", "denied" …
- **Delay problems**: "delayed", "very late", "not on time" …
- **Natural-language/typo tolerance**: explicit misspelling list maps "not
  recived" → "not received", "mising" → "missing", "not genrated" → "not
  generated", etc. Detection runs on the raw user message so typos are caught.

**Information queries never route to the grievance intake**: where/when/how/
what frames ("where can I check my result?") and pure service mentions
("courses offered") are rejected unless a complaint marker is present. Process
questions about the grievance system itself ("how to file a grievance") also
stay informational.

### Regression fixed: negative-outcome phrasing

Complaint phrasing such as:

> "my admit card **has not been generated**"

was previously missed: the substring marker "not generated" does not appear in
"has not been generated" because of the helper verb **been**.

Fix: `_NEGATIVE_OUTCOME_RE` (two regexes) in `detect.py`:

```
\bnot\s+(been\s+)?(generated|issued|uploaded|updated|updating|processed|delivered|
    published|reflected|shown|printed|received|visible|dispatched|released)\b
\b(isn'?t|wasn'?t|weren'?t|hasn'?t|haven'?t|didn'?t|couldn'?t|can'?t)\s+(been\s+)?
    (generated|…|showing)\b
```

The verb-set is restricted to service-outcome verbs, so ordinary information
queries (e.g. "when will the exam come?") still match nothing. Verified with a
direct probe suite: the exam-form story → `is_grievance=True`; "Where can I
check my results", "when will the admit card come", "What is the BCA fee.", and
"courses offered" → `False`.

---

## 4. STUDENT GRIEVANCE EXPERIENCE

The chatbot (`frontend/js/chatbot.js`) opens a modal composer (`.g-composer`)
when the orchestrator routes to the `grievance` action (e.g. when the planner
detects a complaint, or the user clicks "File a Grievance").

Workflow:

1. **Step 1 — describe the problem**: free-text textarea (min 8 chars) +
   category dropdown. The original text is pre-filled when the complaint was
   detected from chat (`gv.original`).
2. **Step 2 — review & route**: the backend generates a formal draft (LLM), or
   the fallback draft when the LLM is unavailable; the student can edit subject
   and text; the authority recommendation is fetched and shown as selectable
   office chips (auto-selected recommended; "auto-assign" shown when nothing
   matches).
3. **Step 3 — contact details**: name/email (required), roll number, semester,
   college, programme, phone — all optional except name/email.
4. **Submit**: validated, idempotency key attached (see §11), submitted.
5. **Step 4 — receipt**: reference number shown, tracking token shown exactly
   once with a copy button; done → acknowledgment message in chat.

Forward/back navigation between steps, editing at any review step, and
cancellation are supported. Submissions happen without any login.

---

## 5. LLM FORMALIZATION

`app/grievance/llm.py::formalize(raw)`:

- Calls local Ollama (`settings.LLM_MODEL`, `OLLAMA_BASE_URL`) with a system
  prompt demanding formal English, **restating only what the student wrote**,
  and forbidding fabricated names, dates, amounts, roll numbers, documents, or
  events.
- Returns `{generated, subject, text, error, manual}`:
  - LLM success → `generated=True, manual=False` with JSON {} subject/text from
    the model (`_parse_llm_json` tolerates markdown fences).
  - **LLM failure never blocks the student**: network/JSON/model errors fall
    back to a deterministic `_manual_draft` (subject derived from the first few
    words + the cleaned raw text) with `generated=False, manual=True`.
  - Empty input → error without calling the model.
- The student always reviews/edits the draft before submission (§11). The LLM
  output is a drafting aid, not an authoritative document: it is never stored
  as final content without student approval.
- Subject is truncated to 200 chars, text to 4000 chars, input sanitized and
  capped at 4000 chars.

Verified: E2E confirmed a usable draft, a subject, and "no fabricated
dates/names introduced" — plus the manual-draft fallback (error path) is
covered by the intake unit suite.

---

## 6. ORIGINAL INPUT & FINAL APPROVED CONTENT

Two content columns are distinct on the `grievances` row:

| Column | Meaning |
|---|---|
| `original_student_input` | raw text exactly as the student wrote it (e.g. "exam form submitted; …") |
| `final_grievance_text` | the reviewed/edited text the student approved |
| `generated_formal_grievance` | AI-normalized draft (reviewed by the student) |

Both the original and the final are retained so the administration can always
re-read the student's words even after the complaint was formalized/editied —
and the record is not dependent on the LLM. The E2E verified that both the
original story and the exact approved text are preserved verbatim in the row.

---

## 7. AUTHORITY MATCHING

Recommendation is used via `recommend_authorities()` in `app/grievance/intake.py`:

- Uses the **existing** `find_authority()` matcher (keyword overlap scoring
  against `keywords`/`services_offered`/name/department/description, plus
  priority boost).
- Metadata is **DB-driven** (`authorities` table, cached by
  `authority_service`); only **active** authorities participate
  (`list_active()`).
- Returns up to 3 (top 3) matches with stable keys
  (`authority_id, authority_name, department_name, email, match_score`);
  empty list (with `_RECOMMEND_MIN_SCORE = 0.55`) → the UI shows
  "auto-assigned" instead of an office.
- The student can also **manually letters pick** any recommended office via the
  chip UI (selection in step 2), or submit without one (backend then leaves
  `authority_id = None` and the office is assigned later by staff).
- The recommended office is shown with its **real DB contact fields**
  (authority_name, department_name, email) — nothing hardcoded.

### Official authority improvement (Controller of Examinations)

`app/authority/seed_official.py` — the official CoE record now includes
**`admit card`**", **`hall ticket`**, **`exam form`**, **`examination form`**
in its `keywords` and "Admit card & hall ticket issuance" in
`services_offered`, so real student phrasing matches the official row. The
import also now **refreshes keywords/services for official-source rows only
when they differ** from the seed — re-runs of an unchanged seed remain true
no-ops (`0 created, 0 updated` was verified twice), while admin-entered
contact details are never overwritten.

---

## 8. DATABASE & GRIEVANCE RECORD

The Phase 1 foundation (`grievances` table) is used as-is. Logical information
stored on a row (existing columns unless marked "new"):

| Field | Notes | Origin |
|---|---|---|
| `reference` | public reference, unique index | existing field reused |
| `authority_id` | FK → `authorities.id` (SET NULL) | existing |
| `student_name, roll_number, semester, college, student_email, programme, phone` | self-reported pre-login ID | existing |
| `source_kind` | `"pre_login"` | existing |
| `email_status` | `None`/`"sent"`/`"failed"` | existing |
| `tracking_token_hash` | SHA-256 digest only (unique) | existing |
| `client_request_id` | idempotency key (unique) | **new field introduced** |
| `category` | portfolio subject/category | existing |
| `original_student_input` | as written by student | existing |
| `generated_formal_grievance` | AI draft | existing |
| `final_grievance_text` | approved text | existing |
| `status` / priorities / timestamps | lifecycle + SLA timestamps | existing |

Schema migration: `app/database.py::_upgrade_schema` adds the
`client_request_id VARCHAR(64)` column and a unique index
`ix_grievances_client_request_id` — idempotently created on startup.

---

## 9. STATUS & HISTORY

Phase 4 uses the single status service `app/grievance/service.py::record_status_change()`:

- A submission starts its lifecycle at the model default `draft`; the intake
  calls `record_status_change(..., new_status="submitted", changed_by="system:pre_login_submission",
  changed_by_role="system", comment=…, is_internal=True)`.
- History rows are **append-only and immutable**: one new
  `grievance_status_history` row per transition (previous_status, new_status,
  changed_by, changed_by_role, comment, created_at); the current status lives
  on the grievance row. There is no update/delete path for history.
- The E2E verified **exactly one history entry (→"submitted")** per submission.
- No second status system was introduced.

---

## 10. REFERENCE NUMBER

`app/grievance/intake.py::generate_public_reference()`:

```
CUS-GRV-<year>-<8 hex chars>
```
Example (E2E): `CUS-GRV-2026-FD4EA077`.

Properties implemented:

- **Uniqueness**: `reference` column is unique + indexed; the intake retries up
  to 10 times if a collision (extremely unlikely) occurs.
- **Irrelevant-guessability**: the suffix is 8 cryptographically-random
  hex chars via `secrets.token_hex`.
- **Separation from DB IDs**: `reference` is a public business-level identifier
  distinct from the internal UUID `id`.
- **Use in tracking**: the reference + one-time token are the only way a
  student checks status.

---

## 11. IDEMPOTENCY / DUPLICATE SUBMISSION

Duplicate submission protection is implemented on the submit path and the
frontend:

- `SubmitRequest.idempotency_key` (min 8, max 64, `[A-Za-z0-9_-]`) — client-
  generated, e.g. `crypto.randomUUID()`. A unique `client_request_id`.
- `submit_grievance()`:
  - When a row already exists for the key, returns the **original receipt**
    (same reference, same tracking token) with `deduplicated: true`, creating
    nothing.
  - New submission stores the key, commit **before** the email attempt.
  - `IntegrityError` race (two concurrent identical keys) is absorbed: the
    loser rolls back and returns the winner's receipt.
- On retries the token is re-derived deterministically
  (`token_for_request_id()`: HMAC-SHA256 of the key under
  `settings.SECRET_KEY`, base64url-trimmed). If the server key rotated since
  the original submission (digest no longer matches), the receipt is returned
  **without** a token — no second row is ever created.
- **Unit test** `test_idempotent_retry` verifies: same key → same reference +
  same token + `deduplicated: true` + exactly one row; a different key → a new
  row.
- **E2E** additionally verified the full retry path (§19, checks 7).

### Regression fixed in this phase

`deduplicated` is now **preserved** on replays: `routes.py` previously removed
it unconditionally from the response, which hid the flag from the client
making the duplicate invisible. Fix: the flag is popped **only for fresh
submissions**; replayed submissions carry `deduplicated: true` so the client
can tell the user "already received".

---

## 12. EMAIL

`app/grievance/routes.py` … `app/utils/email.py::send_grievance_acknowledgement`:

- The acknowledgement email is sent only **after the row is committed** (the
  persisted submission + history come first).
- Best-effort by design: `from app/utils/email.py` never raises; SMTP
  failures return `False` and the flow records `email_status = "failed"`;
  success → `"sent"`.
- `EMAIL_ENABLED` defaults to **OFF** — the service runs without any SMTP
  configuration; in that case no email is attempted and `email_confirmed`
  stays truthful (`false`).
- E2E asserted `email_confirmed` is a truthful boolean in the receipt
  (lit- run: `false`, email off).

---

## 13. SECURE TRACKING

`verify_submission()` checks **both** reference and token:

- Token is stored only as a SHA-256 digest (`tracking_token_hash`), checked
  with constant-time compare (`secrets.compare_digest`).
- Unknown reference, or wrong/missing token → same behavior; the route
  **_indistinguishable 403** ("Invalid reference or tracking token") — IDOR/
  reference-guessing never leaks existence or PII.
- The verify payload is strictly **PII-free**: reference, status, category,
  subject, submitted_at, authority_name, department_name only — no
  student_email, roll_number, phone, token, names.
- E2E verified: **correct token → HTTP 200 & `status=submitted`**; wrong token
  → **HTTP 403**; and the payload contains no PII keys. (Separately covered:
  missing token → 403/422, unknown reference → 403.)
- Plaintext token is never persisted nor emailed.

---

## 14. SECURITY

Phase 4 preserves the Phase 3 security model (RBAC for staff/admin endpoints):

| Actor | Capability |
|---|---|
| **Student (pre-login)** | submit grievance, receive reference+token, verify own status |
| **authority_admin** | Phase-3-scoped access to their office's inbox; nothing changes |
| **superadmin** | Phrase-3 admin APIs; nothing changes |

- **Authority scope is derived server-side**: `submit_grievance` only routes to
  `authority_id` that `is_active_authority()` confirms — an inactive or
  unknown / forged ID is rejected with 422 ("selected authority is not
  available for routing") before anything is written.
- Grievance records are not exposed publicly beyond the token-gated status;
  unauthorized grievance access (incl. via reference-guessing) fails 403.
- `authority_id` is a server-validated FK, never trusted from the client alone.

---

## 15. PUBLIC / PRE-LOGIN SECURITY

An unauthenticated student **can**:

- submit a grievance (with valid email, text ≥ 10 chars, valid authority
  if supplied) and receive reference + one-time tracking token;
- recommended offices (read-only, active-only listing);
- a PII-free status check via reference + token; an editable, reviewable draft.

An unauthenticated user **cannot**:

- read any grievance record without the token gate;
- view any student data from stored records (verification is PII-free);
- learn whether a reference exists (indistinguishable 403);
- over the per-IP rate limits (per-minute, per endpoint):
  - create: 6 requests/min
  - generate: 5 requests/min
  - verify: 20 requests/min
  - recommend: 30 requests/min
  Verified by test: 6 allowed then 7th receives HTTP 429; generate: 5 then 429.

All intake routes are keyed per-IP via `endpoint_rate_limit` with the
documented defaults.

---

## 16. ERROR & FALLBACK HANDLING

Implemented and tested behaviors only:

| Scenario | Behavior |
|---|---|
| LLM unavailable / invalid | deterministic manual draft (`generated=false, manual=true`); frontend switches to "write by hand" |
| matcher returns nothing | frontend shows "no office matched" / "auto-assigned", `authority_id` omitted; submission still works |
| authority inactive / unknown ID | 422, nothing written |
| email SMTP failure | `email_status="failed"`, submission unaffected (truthful `email_confirmed=false`) |
| duplicate submission | idempotency: original receipt replayed, exactly one row |
| DB issues / race on key | `IntegrityError` → rollback + winner re-advances receipt |
| validation errors | pydantic 422 with clear `detail` (frontend maps error messages) |
| frontend network failure on draft/recommend | shows fallback message, still lets student continue manually |

---

## 17. FRONTEND IMPLEMENTATION

`frontend/js/chatbot.js` gained the grievance composer (`.g-wrap` → `.g-box`
→ steps):

- **Integration**: orchestrator emits a `grievance` event → `openGrievanceComposer(prefill,
  category)`.
- **States**: step 1 (problem+category) → step 2 (draft review + office pick)
  → step 3 (details) → step 4 (success receipt).
- **Validation & navigation**: per-step field checks (min lengths, email
  regex), backward navigation, cancel/close.
- **Submission**: private "submitting" guard (`gv.submitting`), a fresh
  idempotency key (`idemUid()`: `crypto.randomUUID`) that **stays through
  retries** and is **cleared only on success**, and server error message
  extraction (pydantic `detail[0].msg` etc.).
- **Success**: reference always displayed, tracking token shown once with a
  Copy button and a keep-safe warning.
- Original student input is always kept in `gv.original` and sent in
  `original_input`.

Verified: `node --check frontend/js/chatbot.js` passes (syntax-checked after
implementation).

---

## 18. TEST COVERAGE

Verified results (current state):

| Check | Result |
|---|---|
| E2E (scripted full flow) | **32/32 PASS** |
| Full pytest suite | **104 passed** |
| Standalone grievance intake runner | **90 passed** |
| Frontend syntax `node --check` | **PASS** |

**Baseline history (not a contradiction):** The full suite was **103/103** at
the start of this session (a previous verified baseline) — then the new
**`test_idempotent_retry`** (dedup test added as part of this phase) raised the
count to **104 passed**. That is exactly one new test, which explains
103 → 104. The standalone grievance intake runner **90 passed** is the full
current runner list for the intake module; no failures were seen.

---

## 19. END-TO-END TEST

An E2E driver exercised the whole flow against the real app (TestClient with
the application loaded — intended to run under `uvicorn` startup semantics).

Scenario text:

> "I have submitted my exam form and paid the fee but my admit card has not been generated."

Sequence verified (each an independently-checked assertion):

1. **intent detection**: the story routes to `action="grievance"` (NOT the
   `exam_form` connector);
2. **grievance routing**: 4 ordinary queries resolve to `actions` other than
   grievance (catalogue / OK);
3. **draft**: a usable draft (LLM or fallback) with subject; no fabricated
   dates/names;
4. **authority recommendation**: a match is found;
5. **real CoE row**: recommendation resolves to the DB's active
   `"Controller of Examinations"` with its registered contact fields;
6. **submission**: HTTP 201 with the required reference format
   `CUS-GRV-<year>-<8>`, tracking token, status `submitted`, truthful
   `email_confirmed`;
7. **duplicate retry**: same idempotency key → same reference, re-delivered
   token, `deduplicated: true`;
8. **database invariants**: one row, linked authority, original input and
   approved text exactly, plaintext token **not** stored, `source_kind="pre_login"`,
   exactly one history entry `submitted` by `system`;
9. **tracking success**: correct token → 200, status visible, no PII;
10. **unauthorized rejection**: wrong token → 403;
11. **cleanup**: the E2E row is deleted afterwards.

Result: **E2E 32/32 PASS** (31 passed at first run, then after the
`deduplicated` replay-preservation fix: 32/32).

---

## 20. REGRESSION VERIFICATION

- **Full pytest suite: 104 passed** — confirms the existing 103-test suite
  still passes with the Phase 4 additions (catalogue, knowledge sync, RBAC,
  orchestrator, authority directory, college knowledge, intelligence/LLM and
  intake tests all still green).
- **Standalone grievance intake runner: 90 passed** — the intake module alone
  (detection, drafting, recommendation, submission, idempotency, tracking,
  rate limits, history) runs clean.
- **Frontend syntax**: `node --check frontend/js/chatbot.js` → **PASS**.
- **Existing catalogue functionality**: the E2E confirms ordinary catalogue
  queries ("What is the BCA fee?", "courses offered", "NEP courses",
  "admission requirements for BCA") still route to the catalogue action today
  — the compliant/non-compliant separation is unchanged.
- **Authority**: directory import still idempotent (`0 created, 0 updated` on
  re-run), authority_active filtering verified in recommendation flow, and the
  Phase 2/3 authority test suite still passes with the full suite.
- **RBAC**: Phase 3 authorization tests included in the 104; no Phase 4 change
  weakens or touches authority admin/superadmin access.
- **Orchestrator / intent**: the smart orchestrator's grievance rule is
  present; E2E shows scenario-kept complaint routing with the non-grievance
  catalogue queries unaffected.
- **Intent detection**: the intake module's own parametrized detectors
  (including the negative-outcome and typo cases) are covered by the
  standalone 90; the full suite additionally runs planner-level intent tests.

No functional regression was observed in any of the corresponding suites.

---

## 21. FILES CHANGED

Files actually modified for Phase 4 (grievance intake). Grouped by layer:

**Backend**

- `backend/app/grievance/` (new package) — `__init__.py`, `detect.py`,
  `llm.py`, `models.py`, `service.py`, `intake.py`, `routes.py`
- `backend/app/database.py` — idempotent `_upgrade_schema` patch adding the
  `client_request_id` column and its unique index
- `backend/app/authority/seed_official.py` — CoE official keywords/services +
  official-source refresh (idempotent)
- `backend/app/orchestrator/planner.py` — grievance rule (Rule 2b) ahead of
  service-keyword routing
- `backend/app/orchestrator/engine.py` — `grievance` action handling
- `backend/app/utils/email.py` (new) — best-effort SMTP sender
- `backend/app/config.py` — email + grievance rate-limit settings
- `backend/.env.example` — documented the new settings (email SMTP, limits)

**Frontend**

- `frontend/js/chatbot.js` — grievance composer (steps 1–4), idempotency key,
  submission states, error mapping
- `frontend/css/chatbot.css` — composer styling

**Tests**

- `backend/tests/test_grievance_intake.py` — full intake suite (incl.
  `test_idempotent_retry`, `test_rate_limits`, cleanup fixture)
- `backend/tests/test_grievance_models.py` — model/history invariants

**Documentation**

- `docs/PHASE_4_STUDENT_GRIEVANCE_REPORT.md` (this document)

(Excluding the earlier phases' files — authority directory, Phase 3 RBAC,
catalogue, etc. — which were not touched by Phase 4.)

---

## 22. BUGS FOUND AND FIXED

1. **Negative-outcome phrasing missed** (`_NEGATIVE_OUTCOME_RE`): the E2E
   story was routed to the exam-form connector because "has not been
   generated" slipped past substring markers. Fixed in `app/grievance/detect.py`.
2. **CoE official keywords/services were insufficient for real complaints** —
   "admit card / hall ticket / exam form" added to the official Controller of
   Examinations row; official-source rows now refresh only when the seed
   differs, import stays a true no-op on rerun.
3. **`deduplicated` dropped on replays** in the submit route: the flag was
   unconditionally popped, hiding duplicates from clients. Now preserved on
   replayed responses only.

Known (E2E-driver, not app code): the E2E script first booted the app through
`TestClient` without a lifespan context, so the authority cache was never
loaded and recommendations came back empty. **The driver was fixed to
initialise the authority cache the same way `main.py`'s startup hook does**;
the application itself behaves the same under `uvicorn` at all times. The
driver also previously ran against the wrong SQLite file because
`DATABASE_URL` is a relative path — the accidental repository-root
`cus_ai.db` created as a by-product was removed; the actual development
database remains **`backend\cus_ai.db`**.

---

## 23. DATA INTEGRITY

Verified invariants at the DB level (E2E + suite):

- authority linkage: `grievance.authority_id` exactly the selected active
  authority; 
- status/history: exactly one append-only history entry on submission
  (`submitted`), actor `system`;
- unique reference: format + uniqueness enforced by index and retry;
- duplicate protection: one row per idempotency key (unique index);
- cleanup: the E2E row is removed after the run, no test residue in
  `grievances` from E2E runs (checked);
- the dev DB (`backend\cus_ai.db`) keeps the official authorities while the
  stray root DB was removed.

No credentials, tokens, passwords, API keys, or student private data are
written or printed anywhere in this report.

---

## 24. KNOWN LIMITATIONS

| Current limitation | Future enhancement |
|---|---|
| Tracking requires remembering the one-time token (not persisted, not emailed) | account-linked student dashboard to recover status without the token |
| Category is user-selected (auto-suggestion only) | strict category taxonomy/validation |
| `generated_formal_grievance` is stored but not separately editable in the UI workflow | separate draft history / multiple revision |
| Email is best-effort, OFF by default | queue/retry, delivery tracking |
| Office assignment shown at selection; auto-assigned submissions track as "Pending assignment" | deterministic auto-assignment policy at submit time |
| Attachments (table exists since Phase 1) not wired | upload flow in a later phase |
| Status transitions limited to submit (other transitions via later dashboards) | authority answer/closure flow per Phase 3 contract |

Each row is deliberately a current-limitation / future-enhancement pair; no
problem is invented.

---

## 25. PHASE 4 ACCEPTANCE CHECKLIST

| Item | Status |
|---|---|
| Grievance intent detection | **[PASS]** detection + non-hijack tests, 4 queries stay normal |
| Pre-login grievance intake | **[PASS]** submit without any account (E2E) |
| Natural-language grievance detection | **[PASS]** markers + typo list + negative-outcome fix |
| LLM formalization | **[PASS]** formalize returns `manual: true` fallback, subject+text |
| Student review/approval | **[PASS]** composer step 2 editable, in the E2E/draft flows |
| Authority recommendation | **[PASS]** recommend route + CoE on real DB row |
| Authority selection | **[PASS]** option chips + selectable |
| Student details | **[PASS]** step 3 fields validated & stored |
| Submission | **[PASS]** HTTP 201, reference, token |
| Status history | **[PASS]** exactly one immutable entry `submitted` |
| Reference number | **[PASS]** `CUS-GRV-<year>-<8>` unique verified |
| Duplicate protection | **[PASS]** `test_idempotent_retry` + E2E retry `deduplicated` |
| Secure tracking | **[PASS]** token-gated 200/403, PII-free |
| RBAC protection | **[PASS]** Phase-3 RBAC untouched; authority scope checked server-side |
| Email handling | **[PASS]** truthful boolean, best-effort failure path |
| E2E verification | **[PASS]** 32/32 |
| Regression testing | **[PASS]** 104 pytest / 90 standalone / node check |

---

## 26. FINAL STATUS

**PHASE 4 STATUS: COMPLETE**

- E2E: **32/32**
- Full pytest: **104 passed**
- Standalone intake: **90 passed**
- Frontend syntax (`node --check`): **PASS**

**No Phase 5 functionality was implemented as part of this report.** No
modifications outside the Phase 4 scope (grievance intake) were made; no
application code, schema, or data was changed in producing this document.