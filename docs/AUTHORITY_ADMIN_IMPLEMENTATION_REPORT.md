# Authority Admin Portal — Implementation Report (Phase 6)

## 1. Executive summary
A complete, isolated Authority Admin Portal was built on the existing CUS architecture: every authority in the DB can have its own admin account (already supported by Phase 3) and its own dashboard showing ONLY that authority's grievances. The Super Admin panel is untouched. Authentication reuses the existing JWT/bcrypt login; scope is always derived server-side from `users.authority_id`; status mutations go through the existing immutable `record_status_change()` history service; emails go through the existing best-effort sender with honest per-destination delivery status. Verified: full pytest suite **111 passed**, new portal E2E **88/88**, Phase 3 RBAC **53/53**, Phase 4 intake **90/90**, Phase 5 E2E **38/38**, `node --check` PASS on all changed JS.

## 2. Architecture inspected (see also docs/PHASE_A_ARCHITECTURE_REPORT.md)
- Auth: `app/auth/` (bcrypt + JWT, `get_current_user`, `require_role`, `require_authority_admin`, `require_superadmin`, `require_authority_scope`; `POST /api/auth/login` is role-agnostic and sets `last_login`, audits `login`/`login_failed`).
- Users: `role ∈ student|admin|authority_admin|superadmin`, `authority_id` FK, `is_active`, `last_login`, profile fields. Multiple admins per authority supported (no uniqueness constraint).
- Phase 3 account management: `/api/admin/authority-admins` CRUD/toggle/assign + `/api/authority-admin/me`.
- Grievance: `app/grievance/` — model (status vocabulary `draft..closed|rejected`), `service.record_status_change` (immutable history), Phase 4 intake + token-gated PII-free verify, Phase 5 authority+student emails.
- Audit: `app/utils/logging.py` `audit()` (own session, never raises). Errors: `{"error":{code,message}}`. Email: env-driven, OFF by default.
- Frontend: `pages/admin.html` + `admin.js` (Super Admin), `index.html` + `chatbot.js` (student, embedded GOV grievance flow from Phase 5), design tokens in `css/admin.css`/`main.css`.

## 3. Files changed / added
Backend:
- `app/grievance/models.py` — additive columns on `grievances`: `is_read`, `read_at`, `read_by`, `authority_response`, `authority_response_at`, `response_email_status`.
- `app/database.py` — `_upgrade_schema()` additions for the six columns + `ix_grievances_authority_read` index (idempotent ALTERs on existing DBs).
- `app/authority_admin/portal.py` — **NEW** portal business logic (scope, views, read/unread, status, response, list/dashboard queries).
- `app/authority_admin/service.py` — `update_own_profile`, `change_own_password`; `self_scope` extended with description/office/services.
- `app/authority_admin/routes.py` — portal endpoints on the existing self-router (below).
- `app/authority_admin/schemas.py` — `PortalProfileUpdate`, `PortalPasswordChange`, `PortalStatusChange`, `PortalResponseCreate`.
- `app/utils/email.py` — `send_grievance_response()`; optional dashboard link in authority notification via `PUBLIC_BASE_URL`.
- `app/grievance/intake.py` — audit events `grievance.email_sent`/`grievance.email_failed` for both destinations on submission.
- `app/config.py` — `PUBLIC_BASE_URL` (env-driven, default empty).
- `app/main.py` — `/authority-admin` redirect route (Super Admin `/admin` untouched).

Frontend:
- `pages/authority-admin.html` — **NEW** (login + portal views).
- `js/authority-admin.js` — **NEW** controller (role-routed login, dashboard, grievances, detail, profile).
- `css/authority-admin.css` — **NEW** styles consistent with the CUS admin system (badges, KPIs, detail layout, toast); reuses admin.css tokens/classes.

Tests/docs:
- `tests/test_authority_admin_portal.py` — **NEW** (88 checks, pytest + standalone).
- `docs/PHASE_A_ARCHITECTURE_REPORT.md`, `docs/AUTHORITY_ADMIN_IMPLEMENTATION_REPORT.md`.

## 4. Database changes (all additive + idempotent + backward compatible)
| Table | Column | Type |
|---|---|---|
| grievances | is_read | BOOLEAN (default false) |
| grievances | read_at | DATETIME |
| grievances | read_by | VARCHAR(200) |
| grievances | authority_response | TEXT |
| grievances | authority_response_at | DATETIME |
| grievances | response_email_status | VARCHAR(20) |
Plus index `ix_grievances_authority_read (authority_id, is_read)`. Read state is per-grievance at the authority level (multiple admins of one authority share it — consistent with the authority-scoped portal and the spec's "simplest architecture" guidance). No existing column/table/user/authority was altered or dropped.

## 5. APIs created / modified
`/api/authority-admin` (existing self-router extended; all require `authority_admin`):
- `GET /me` (existing), `GET /profile` — account + server-derived authority block.
- `PUT /profile` — full_name / designation / phone ONLY (authority identity, category, status, official email, routing are NOT editable — Super Admin only).
- `PUT /password` — verifies current password first; never logs/returns passwords; does not revoke other sessions.
- `GET /dashboard` — authority header + counters (total, unread, in_progress, resolved, closed) + 8 recent.
- `GET /grievances` — paginated (page/page_size ≤ 50), backend-enforced `q` (reference/student/roll/email/category), `status`, `read` (read|unread), `date_from`/`date_to`; returns `unread_total`; invalid status/date → 422.
- `GET /grievances/{id}` — detail + immutable history; **opening an unread grievance marks it READ once** (audited `grievance.opened`); cross-authority → 404.
- `POST /grievances/{id}/read` / `unread` — idempotent, audited (`grievance.mark_read` / `mark_unread`).
- `POST /grievances/{id}/status` — `{new_status, note?}`; validates against existing `GRIEVANCE_STATUSES` vocabulary minus `draft`; no-op → 409 (no duplicate history); mutation ONLY via `record_status_change()` with `changed_by_role="authority_admin"`; stamps `resolved_at`/`closed_at`; audited `grievance.status_changed`.
- `POST /grievances/{id}/response` — stores official response (single per grievance, 409 on repeat), appends a student-visible history entry (`is_internal=False`), emails the student best-effort, records `response_email_status` (`sent`/`failed`/`unavailable`), audits `grievance.response_created` + `grievance.email_sent`/`email_failed`.
- Modified: `app/grievance/intake.py` submission now emits the email audit events; `app/utils/email.py` gained the response sender.

No second login endpoint: the existing `POST /api/auth/login` authenticates authority admins; role detection decides the destination UI.

## 6. Authentication flow
`/authority-admin` → login page → `POST /api/auth/login` →
- role `authority_admin` + `authority_id` set → token in `cus_authority_token` (separate from Super Admin's `cus_admin_token`), portal loads.
- role `superadmin` → token stored as `cus_admin_token`, redirect to existing `/pages/admin.html` (Super Admin behavior unchanged).
- role `student` → rejected with a clear message on the login card ("Student accounts cannot access this portal").
- inactive account → 403 from the server, surfaced as "Account is disabled".
- bad credentials → 401, clear message; wrong-password path is server-enforced (no client-side bypass).

## 7. Authorization model / authority isolation
- Every portal endpoint derives scope from `current_user.authority_id` (`require_authority_admin` + portal scope helpers). Query params, path ids and body fields never influence scope; a forged `?authority_id=` or body `authority_id` is ignored (verified 9/9d).
- A grievance of another authority is indistinguishable from a missing one: **404** on detail and on every mutation (read/unread/status/response). Existence of other authorities' grievances is never leaked.
- Super Admin: blocked from `/api/authority-admin/*` (403) — it keeps its own global panel.
- Student: 403 on all portal endpoints.
- Inactive-authority policy (existing): account login follows account `is_active`; routing of NEW grievances refuses inactive authorities (422) — portal still serves the (empty) scope.

## 8. Grievance workflow (authority admin)
Dashboard counters → list (search/filter/page) → detail (auto-READ) → status updates (immutable history) → official response (emailed to student). Read state (`is_read/read_at/read_by`) is fully separate from workflow `status`; the UI shows "● New"/"Read" badges next to status badges.

## 9. Read/unread implementation
Columns on `grievances`; GET detail auto-marks (first open only — re-opens are stable and never re-audited); explicit idempotent read/unread endpoints; unread counters on nav + dashboard updated after every action; audit events `grievance.opened/mark_read/mark_unread`. No history rows are used for read state, and repeated clicks never duplicate anything.

## 10. Email implementation
All through `app/utils/email.py` (`EMAIL_ENABLED`/SMTP env vars, default OFF; never hardcoded or client-side; failures never block storage and are recorded honestly):
- student submission confirmation (`send_grievance_acknowledgement`, existing).
- authority new-grievance notification (`send_grievance_to_authority`, existing; now includes a dashboard link when `PUBLIC_BASE_URL` is set).
- student response email (`send_grievance_response`, new) — body per spec: Reference, Status, Authority, Official response.
Flow order everywhere: validate → store (commit) → attempt mail → record `sent|failed` → return success. SMTP failure keeps the grievance (verified 22a-d) and the UI shows "Response recorded. Email notification could not be delivered at this time."

## 11. Student UI changes
None required beyond Phase 5: the embedded chatbot GOV flow already implements authority-first selection (DB-driven, active-only), AI formalization (editable), manual text, student details validation, submission with reference + one-time token, and in-chat tracking (verify). Verified wired: chip "File a Grievance" and SSE `ev==="grievance"` → `govStart()`. Student-side API contract regression-tested (draft/generate, categories, submit receipt, verify token gate) — §28-33 of the checklist.

## 12. Authority Admin UI
`/authority-admin` page with the CUS look (Inter/Sora, admin cards, `.btn green/ghost`): login card with role-routed handling; top bar with authority name + logout; nav Dashboard / Grievances / Profile; KPI cards; recent table; grievances table with backend search/filters/pagination and one-click Open; detail with student info, grievance text, status dropdown + Update, read/unread toggle, immutable history timeline, response composer with delivery-status feedback; profile page (authority card + own-name/designation/phone + password change). Loading states ("Loading dashboard…", "Loading grievances…", "Sending…"), buttons disabled during requests (no duplicate submits), human-readable errors (detail/error.message extraction; never `[object Object]`).

## 13. Security tests (PHASE H — tests/test_authority_admin_portal.py, 88 checks)
Authentication 1-5, isolation 6-10 (incl. forged `authority_id` in query AND body), grievance lifecycle 11-18 (unread→auto-read→unread→status→no-op 409→invalid 422→draft 422→response→dup 409→immutable history), email 19-22 (real loopback SMTP: student + authority + response emails captured and content-checked; failure path keeps data), hygiene 23 (no hashes/credential keys in any portal response), audit coverage of all Phase-6 events, student API contract, superadmin regression 34-40.

## 14. Regression tests (PHASE I)
- `python -m pytest tests/ -q` → **111 passed** (105 baseline + 6 portal tests), 5 warnings.
- `python tests/test_authority_admin_portal.py` → **88 passed, 0 failed**.
- `python tests/test_phase3_rbac.py` → **53 passed, 0 failed**.
- `python tests/test_grievance_intake.py` → **90 passed, 0 failed**.
- `python tests/test_grievance_workflow_e2e_email.py` → **38 passed, 0 failed**.
- `python -m py_compile` on every changed Python file → PASS.
- `node --check js/authority-admin.js js/chatbot.js js/admin.js` → PASS.
- Route smoke: `/authority-admin` → 307 → page/css/js all 200.
- No existing test was modified or weakened; no test was deleted.

## 15. Known limitations
- One response per grievance (columns-based storage, per spec's minimal design); multiple threaded replies would need a comments table (deliberately not added).
- Read state is per-grievance (shared by all admins of the authority), per the spec's "simplest architecture" option.
- Password change does not revoke existing JWTs (token validity is not password-bound; acceptable for this system, documented).
- No SMS/WhatsApp; email only.
- Historical audit lookups may be slow at very large scale (no index on action+created_at — unchanged to avoid touching existing schema).

## 16. Configuration requirements
- Existing email block in `.env`: `EMAIL_ENABLED`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `MAIL_FROM_NAME` (default OFF — portal works but delivery shows `failed`).
- `PUBLIC_BASE_URL` (optional): e.g. `https://cus.ac.in` — adds the Authority Dashboard link to authority notification emails.
- Role/authority map requirement: each Authority Admin account must have `authority_id` set (Super Admin assignment UI already enforces this).

## 17. Deployment instructions
1. Pull changes; run `python -m py_compile app/main.py` and `node --check` on the new JS.
2. Start: `cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8001` (startup runs `create_all()` which applies the idempotent additive columns to existing databases automatically — no manual migration).
3. Super Admin: log in at `/admin` → Authorities → Manage → create Authority Admin for the desired authority (existing UI, unchanged).
4. Authority Admin: open `/authority-admin`, log in with those credentials; dashboard shows only their authority's data.
5. Email: set the SMTP env vars above (never commit credentials); verify with a submission — student receives confirmation, authority receives the notification, response sends the update email.
6. Tests: `python -m pytest tests/ -q` (full suite) or the standalone runners listed in §14.

## 18. Final status
All Phase-6 acceptance flows (Super Admin creates/assigns → Authority Admin logs in → sees only own grievances → unread counter → opens (READ) → status IN_PROGRESS → official response → student email; student chatbot path unchanged and fully covered) are implemented and verified by executed tests. The Super Admin panel and its functionality were not modified.