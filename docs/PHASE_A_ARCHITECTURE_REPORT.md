# PHASE A — Architecture Inspection Report (Authority Admin Portal)

Scope: read-only inspection of the existing Cluster University of Srinagar AI Assistant codebase (backend FastAPI + SQLite/SQLAlchemy, vanilla-JS frontend served by the API). Purpose: identify every reusable component before implementing the Authority Admin Portal.

## 1. Authentication & roles (backend/app/auth/)
- `routes.py` — `POST /api/auth/login` (form: username+password) is **role-agnostic** already: verifies bcrypt hash, rejects inactive accounts with 403 "Account is disabled", sets `last_login`, audits `login`/`login_failed`, returns `{access_token, refresh_token, user:{id,username,role,authority_id,is_active}}`. No changes needed; the new portal consumes it and routes on `user.role`.
- `security.py` — bcrypt (`hash_password`/`verify_password`, 72-byte cap), JWT access+refresh, `get_current_user` (DB re-derivation, `is_active` enforced), `require_role(...)`, `require_admin`, `require_superadmin`, `require_authority_admin`, and **`require_authority_scope(authority_id)`** — the existing IDOR guard: scope is always derived from `users.authority_id` (superadmin overrides; everyone else 403). Reused as-is.

## 2. Users / accounts (backend/app/models/db_models.py)
`users` table (role ∈ student|admin|authority_admin|superadmin; `authority_id` FK to authorities, `is_active`, `last_login`, `full_name`, `designation`, `phone`, `avatar_path`, `created_at/updated_at`). Multiple admins per authority are already supported (no uniqueness on authority_id) — spec §24 requirement satisfied.

## 3. Authority Admin account management — ALREADY EXISTS (backend/app/authority_admin/)
- `routes.py`: Super Admin prefix `/api/admin/authority-admins` — list/search, create (role forced to `authority_admin`, authority must exist AND be active, never returns password), get, patch (full_name/designation/email), toggle active, assign authority (audited: `authority_admin.create/toggle/assign/update`). Self prefix `/api/authority-admin` — single `GET /me` returning account + server-derived authority block (name, department, designation, email, phone, office_location, website, category, active).
- `service.py`: duplicate username/email (case-insensitive) rejection, `_user_view` never leaks hashes.
- `schemas.py`: create/update/assign request models.
- Covered by `tests/test_phase3_rbac.py` (roles, student-blocked, CRUD, IDOR scope guard, audit).

## 4. Grievance domain (backend/app/grievance/)
- `models.py` — `grievances` (reference unique, authority_id FK, self-reported student fields, `status`, `email_status`, `authority_email_status`, `tracking_token_hash` unique, `client_request_id` unique, timestamps incl. `resolved_at`/`closed_at`), `grievance_status_history` (immutable, append-only, `previous_status/new_status/changed_by/changed_by_role/comment/is_internal/created_at`), `grievance_attachments`. Vocabularies: `GRIEVANCE_STATUSES = [draft, submitted, acknowledged, in_progress, resolved, closed, rejected]`, `GRIEVANCE_CHANGED_BY_ROLES = [student, super_admin, authority_admin, system]`. **No read/unread columns exist. No response/comment fields exist.**
- `service.py` — **`record_status_change(db, grievance, new_status, changed_by, changed_by_role, comment, is_internal)`**: validates vocab, appends immutable history, advances status, commits. The ONLY status-mutation path — reuse, never mutate status from routes (spec §12).
- `routes.py` (Phase 4, public/rate-limited): `/api/grievances/draft/generate`, `/categories`, `/recommend`, `POST /api/grievances` (submit, idempotent via `client_request_id`, active-authority-only routing, receipt with one-time tracking token), `GET /{reference}/verify?token=` (PII-free, fail-closed).
- `intake.py` (Phase 5): reference `CUS-GRV-YYYY-XXXXXXXX`, token digest-only storage, `submit_grievance` (commit-first → best-effort student ack `email_status` + best-effort authority notify `authority_email_status` = sent/failed/unavailable → receipt; SMTP failure never rolls back), `verify_submission`.
- `llm.py` — `formalize()` (offline fallback) used by the chat composer.

## 5. Audit (backend/app/utils/logging.py)
Single `audit(db, action, actor_id, actor_role, target, detail, ip)` — opens its own session, never raises. Reused for all portal events (spec §29).

## 6. Email (backend/app/utils/email.py, Phase 5)
Env-driven, default OFF: `EMAIL_ENABLED, SMTP_HOST, SMTP_PORT, SMTP_STARTTLS, SMTP_USER, SMTP_PASSWORD, MAIL_FROM, MAIL_FROM_NAME` (config.py lines 194-202). `_deliver()` never raises; `send_grievance_acknowledgement` (student) and `send_grievance_to_authority` (new-grievance notify incl. student details) exist. **Missing: response-to-student email.** No dashboard link in authority mail yet.

## 7. Errors (backend/app/utils/errors.py + main.py)
All responses `{"error":{code,message}}` via global handlers; unhandled exceptions → 500 without stack traces to clients.

## 8. Frontend (frontend/)
- `pages/admin.html` + `js/admin.js` (2157 lines): Super Admin panel. Login via `/api/auth/login`; token in `localStorage["cus_admin_token"]`; `apiJson/authHeaders/toast/$/esc` helpers; login/dash split views; design tokens from `css/admin.css` (Inter/Sora, `.admin-card`, `.btn green|ghost|sm`, toasts, tabs). **No authority-admin UI exists anywhere.**
- `pages/index.html` + `js/chatbot.js` + `js/main.js` + `js/navigation.js`: public site + embedded chat. Chat grievance flow (Phase 5) is a GOV state machine INSIDE the chat log; trigger = chip "File a Grievance" or SSE `ev==="grievance"`; authority-first, DB-driven active authority list, AI formalize + manual fallback, details form, review, submit with reference+token, track via verify. Satisfies spec §17-21 except: verify UI has no dedicated student page (tracking is in-chat), acceptable per spec scope.
- `pages/*.html` served statically by FastAPI; `/admin` → redirect to `/pages/admin.html` (main.py).

## 9. Gaps to implement (this phase)
| # | Gap | Plan |
|---|---|---|
| 1 | Read/unread (spec §9/§11/§27) | Additive grievance columns `is_read`, `read_at`, `read_by` + portal endpoints (`read`/`unread`); GET detail auto-marks read (WhatsApp-style), audits `grievance.opened` once |
| 2 | Authority-response storage (spec §14) | Additive columns `authority_response`, `authority_response_at`, `response_email_status`; response appended to immutable history as `is_internal=False` entry |
| 3 | Portal API (spec §28) | Extend `/api/authority-admin` router: profile GET/PUT, password, dashboard stats, paginated/search/filtered grievance list, detail, read/unread, status (via `record_status_change`), response |
| 4 | Response email (spec §14/§15) | `send_grievance_response` in utils/email.py; status = sent/failed, never blocks; audits `grievance.response_created`, `email_sent`/`email_failed` |
| 5 | Authority mail dashboard link (spec §15) | Optional `PUBLIC_BASE_URL` setting appended to authority notification |
| 6 | Portal frontend (spec §3/§4/§30) | New `pages/authority-admin.html` + `js/authority-admin.js` (separate token slot `cus_authority_token`), redirect `/authority-admin`→page; dev-priv builds | login page reuses `/login`, role-routes to dashboard/login on authority admin vs super admin |
| 7 | Login routing | Reuse `/api/auth/login`; new page checks `user.role`; superadmin token → redirect /admin.html; student → reject with message (spec §3/§26) |
| 8 | Tests | New `tests/test_authority_admin_portal.py` covering the spec §34 checklist (auth, isolation/IDOR incl. forged authority_id, read/unread, status+history, response, emails via loopback SMTP, superadmin regression, student-blocked) |

## 10. Verification baseline (current)
`python -m pytest tests/ -q` → 105 passed; Phase 5 E2E standalone 38/38; `node --check frontend/js/chatbot.js` PASS; `node --check frontend/js/config.js` PASS.

## 11. Non-goals (explicitly preserved)
Super Admin panel (admin.html/admin.js) untouched except nothing required; no new auth framework; no new users/authorities/grievance tables; no schema changes beyond the additive columns above, idempotently patched via existing `_upgrade_schema()`.