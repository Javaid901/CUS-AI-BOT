# Phase 5 — In-Chat Grievance UX, Authority Email & Verification Report

## 1. Executive Summary
Phase 5 replaces the broken full-screen grievance modal in the CUS AI chatbot with an in-chat, state-machine-driven grievance workflow that is fully pre-login ("guest") capable, authority-first, and connected end-to-end to the Phase 4 grievance backend. The new flow loads only active authorities from the DB, lets the AI compose a formal grievance (editable, with a manual fallback), collects student details, offers a final review, and submits via the existing Phase 4 endpoint. Delivery is now honest: the authority receives a real email copy (new), the student gets an acknowledgement email (existing), and each destination's outcome is recorded and shown (`email_confirmed`, `authority_email_status`). Submission and verification engines, dedup, reference numbers, RBAC, and audit were all **reused** — not rewritten. Phase 5 adds one additive nullable column, one public read-only endpoint, one email helper, and a new E2E+SMTP test. Full regression: **105 pytest passed** (104 baseline + new E2E), E2E **38/38**, `node --check` PASS.

## 2. What was broken (Phase 5 diagnosis)
- The old composer (`openGrievanceComposer`, `gv` object, `.g-wrap`/`.g-box` overlay) rendered a 580px `position:fixed` overlay appended OUTSIDE the chat (`#cusw`): the "empty popup" users saw.
- Its order was problem-first → recommend-authority → details, contradicting the task's authority-first requirement, and details came from chat inputs rather than the DB.
- Enter did nothing inside the popup: the only keydown handler lived on the chat text input, so the popup's textarea+button were functionally dead.
- No email to the authority existed at all; only a best-effort student acknowledgement.
- Notifications were not routed only-to-active authorities, and no honest per-destination email status existed.

## 3. Workflow (state machine) — new
States: `idle → authorities → authority_select → composing → review → details → final_review → submitting → success | error` (plus `generating`, `track` overlay). Governed by `GOV` in `frontend/js/chatbot.js` with a single renderer `govRender()` for every state — no scattered booleans.

Steps (stepper "Authority → Grievance → Details → Review"):
1. **Authority** — live list from `GET /api/authority/active` (DB-driven, active-only, sorted by department). Selecting an authority advances; Back returns to chat.
2. **Grievance** — optional prefill (from SSE `grievance` payload or chat chip hangover); **AI Compose** calls Phase 4 `POST /api/grievances/draft/generate` (subject + formal text, no invented facts), or **Write Myself** opens a plain textarea. Both lead to editable Review.
3. **Details** — name, roll, semester (1–8), college (dropdown from `GET /api/college/list`), email (prefilled from logged-in session when present). Client-side validation before submission; server revalidates anyway.
4. **Review** — status, authority + email, student details, full formal text (editable via Edit buttons), Copy buttons.
5. **Submit** — calls Phase 4 `POST /api/grievances` with an idempotency key; success shows reference + one-time tracking token (Copy buttons), authority, and **honest** email status for both destinations; "Track Grievance" runs the Phase 4 verify endpoint with the token; "Return to Chat" resets.

Triggers: chip "File a Grievance" → `govStart()`; SSE event `ev === "grievance"` → `govStart(prefill, category)`.

## 4. Authority loading & selection (authority-first)
- `GET /api/authority/active` (new, `app/authority/routes.py`) — public, returns ONLY active authorities from the DB via the existing `repo_list_all(active_only=True)`; response whitelists exactly `authority_id, authority_name, department_name, designation, email`.
- UI never trusts the browser for routing: the submitted payload carries only the selected `authority_id`; the backend re-looks-up the authority, verifies `active`, and rejects inactive routing with `422 "selected authority is not available for routing"` (E2E-verified).
- No authority duplication: selection list, receipt, verify response, and both emails all derive from the same DB row.

## 5. AI composition
- Reuses Phase 4 `POST /api/grievances/draft/generate` (planner → formalizer with offline fallback). Returns `subject` + `text`; E2E asserts "draft preserves facts (no invented roll/authority)".
- User may regenerate, edit the text directly in Review, or write manually; only the *user-approved final text* is stored (`final_text`), original input preserved separately (`original_input`).

## 6. Student details
- College dropdown populated from `GET /api/college/list` (existing endpoint; DB/`COLLEGE_LIST` source), semester 1–8, name/roll/email free text with client validation and server-side revalidation (invalid email → 422).
- Pre-login: all fields are user-supplied; phase 4 token mechanism + rate limits (already present) bound abuse (baseline suite still green).

## 7. Submission, reference & dedup (reused engine)
- `POST /api/grievances` unchanged: validates active authority, writes grievance + `record_status_change` history, idempotency-key dedup (unique index), reference `CUS-GRV-YYYY-XXXXXXXX`, one-time tracking token (SHA-256 digest stored), audit event.
- E2E proves: same `idempotency_key` replayed → same reference + `deduplicated` flag + exactly one row; verify with correct token 200 (no PII), wrong token 403 — fail closed.

## 8. Email — authority (new) & student (existing)
- `app/utils/email.py` refactored: private `_deliver()` (SMTP, never raises, True only on acceptance); `send_grievance_acknowledgement` delegates to it; **new** `send_grievance_to_authority(auth_email, ref, category, text, student, auth_name)`.
- `app/grievance/intake.py` on submit (commit-first, best-effort, NEVER blocks saving): student ack → `email_status`; authority email → **new** `authority_email_status` (`sent`/`failed`/`unavailable`).
- Receipt and verify responses carry both statuses; the success screen shows them honestly (E2E covers `sent` with SMTP on, `failed` with SMTP off, plus invalid-email 422).
- Settings all env-driven (default OFF): `EMAIL_ENABLED, SMTP_HOST, SMTP_PORT, SMTP_STARTTLS, SMTP_USER, SMTP_PASSWORD, MAIL_FROM, MAIL_FROM_NAME`.

## 9. Schema & migration (additive only)
- `app/grievance/models.py`: `authority_email_status` (nullable String(20)) added.
- `app/database.py _upgrade_schema()`: grievances entry gains `"authority_email_status": "VARCHAR(20)"` — same idempotent migration pattern as Phase 4; existing rows unaffected.

## 10. Backend endpoints — reused vs new
| Endpoint | Status |
|---|---|
| `GET /api/authority/active` | **NEW** (public, active-only, whitelisted fields) |
| `POST /api/grievances/draft/generate` | reused |
| `POST /api/grievances` | reused (authority email hooked inside intake; contract identical) |
| `GET /api/grievances/{ref}/verify?token=` | reused |
| `GET /api/categories` / `GET /api/college/list` | reused |
| `/api/admin/authorities/{id}/toggle` | reused (tests) |

## 11. Frontend implementation
- `frontend/js/chatbot.js` (~2176 lines): old modal composer deleted wholesale (`openGrievanceComposer`, `buildGComposer`, `closeGComposer`, `setGSteps`, `renderGStep`, `gBodyStep*`, `onGEventClick`, `gvBodyStep4`, `onGFooClick`, `gv` object, `.g-wrap/.g-box` appending in `#cusw`).
- `GOV` state machine + renderer + actions (`authority, back, ai-write, manual, accept-draft, to-review, edit-details, edit-grievance, submit, retry, done, track, cancel, cancel-yes/no, copy`).
- Chat-native rendering: panel appended inside the chat log (`addMsg("bot", '<div class="gpanel" id="cus-gpanel">')`).
- Keyboard: textarea Enter = newline; Ctrl/Cmd+Enter or button = primary action; single-line inputs Enter = primary; cancel confirmation layer; global keydown ignores the chat input.
- State preserved across Back (fields, draft text, selected authority).
- `frontend/css/chatbot.css`: `.g-*` modal block replaced with in-chat panel styles (`.gpanel .gtitle .gsteps/.gseg .gauth-list .gfield .ggrid .gbtn .gconf .grev .gsucc .gs-ref .gs-token .gs-copy …`) + `@media (max-width:480px)` rules for `.gpanel/.ggrid/.gauth-list/.gbtn`.

## 12. Test coverage (new)
`backend/tests/test_grievance_workflow_e2e_email.py` — sequential E2E with an **in-process loopback SMTP server** (real `smtpd` capture):
- `/api/authority/active` 200, non-empty (10), whitelisted keys only, emails present; deactivated authority hidden; inactive → 422 at submission with honest message.
- draft/generate 200 + subject/text + fact preservation; submit 201 with correct reference format, tracking token, authority association, `email_confirmed`, `authority_email_status="sent"`, status `submitted`.
- REAL captured emails: authority mail contains reference + grievance text + student details; student ack contains reference.
- Idempotency replay → same reference + `deduplicated` + 1 row; verify token 200/none-PII; wrong token 403.
- SMTP off → still 201, `email_confirmed=False`, `authority_email_status="failed"`; invalid email → 422; short text → 422.
- Cleans its rows, restores toggled authorities, restores settings in `finally`; also collected by pytest as `test_grievance_workflow_e2e_email` (and runnable standalone: 38/38).

## 13. Regression
- `python -m pytest tests/ -q` → **105 passed** (104 baseline + E2E).
- `node --check frontend/js/chatbot.js` → PASS; `node --check frontend/js/config.js` → PASS.
- No changes to intake/verify/dedup/audit/RBAC tests; the DB and dev users are untouched.

## 14. Bugs found & fixed during the phase
1. E2E toggled the target authority off and never restored it before submission → 422; restructured toggle/restore around the exclusion and inactive-submission checks.
2. E2E console crash: `→` in check labels unencodable on Windows cp1252 → ASCII-safe `check()` printing.
3. E2E read the API error from `.detail` only; app wraps errors as `{"error":{"message":…}}` → fallback extraction.

## 15. Known limitations
- Emails are real-time best-effort; there is no retry queue (delivery failure is recorded honestly instead of blocking).
- Tracking is per-grievance (reference + token), not per-destination delivery tracking.
- Inactive-authority 422 surfaces as the generic error structure; message text is honest but could be friendlier in chat.
- No browser automation test in-repo; the flow is covered by E2E API tests + manual walkthrough.

## 16. Local run
```
cd backend
python app/main.py          # boot (email OFF by default)
python tests/test_grievance_workflow_e2e_email.py   # standalone E2E (38 checks)
python -m pytest tests/ -q                          # full suite
```
Frontend: open the served chat page (chip/SSE `grievance` trigger). To enable real email: set `EMAIL_ENABLED=true` + SMTP vars in `.env`.

## 17. Files changed (Phase 5)
- backend/app/authority/routes.py — `GET /api/authority/active`
- backend/app/utils/email.py — `_deliver` + `send_grievance_to_authority`
- backend/app/grievance/models.py — `authority_email_status`
- backend/app/database.py — `_upgrade_schema` grievances entry
- backend/app/grievance/intake.py — authority email + honest statuses + receipt/audit detail
- backend/app/grievance/routes.py — audit event detail (student/authority email status)
- frontend/js/chatbot.js — GOV state machine, modal removed, triggers rewired, keyboard
- frontend/css/chatbot.css — in-chat panel styles + mobile rules
- backend/tests/test_grievance_workflow_e2e_email.py — NEW E2E + loopback SMTP
- docs/PHASE_5_GRIEVANCE_UX_REPORT.md — this report

## 18. Acceptance summary
- In-chat workflow replaces the broken modal (no overlay, chat-native panel). ✔
- Authority-first, DB-driven, active-only. ✔
- AI compose + manual fallback + editable review + details + final review. ✔
- Honest delivery: authority + student emails, per-destination status surfaced. ✔
- Reused Phase 4 engine (gen/dedupe/token/verify/RBAC/audit) — no schema-breaking changes. ✔
- Pre-login capable with existing rate/token protections. ✔
- 105 pytest / 38 E2E / node checks green. ✔