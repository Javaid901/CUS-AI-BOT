# Authority Admin Management & Portal — Cluster University of Srinagar AI Assistant

Operational guide for administering Authority Admin accounts and using the
Authority Administration Portal. Covers the complete flow:

**Super Admin creates accounts → Authority Admins log in → scoped grievance
management → response emails → audit trail.**

No real passwords or secrets are documented here.

---

## 1. How Super Admin creates an Authority Admin

1. Open the Super Admin panel: `http://<host>:8001/admin` and sign in
   (Super Admin only).
2. Open the **Authority Admins** tab.
3. Click **+ Add Authority Admin**.
4. Fill the form:
   - **Full Name**, **Username**, **Email**, **Initial Password** (min 6 chars)
   - **Assigned Authority** — selected from the live database; a preview box
     shows the authority's name, designation, official email and category so
     the correct authority is verified before saving
   - **Status** — Active (default) or Inactive
5. Save. The account is created with `role = authority_admin` (assigned
   server-side, never from the client) and the authority link stored in
   `users.authority_id`.

Rules enforced by the backend (`backend/app/authority_admin/service.py`):

- Username and email are unique (email case-insensitive)
- The authority must exist and be **active**
- The password is hashed immediately and **never returned or displayed again**
- The user's **role is always set server-side** — a client-supplied role is ignored

After creation the Super Admin can only see the username/email — never the password.

## 2. How an Authority Admin logs in

1. Open: `http://<host>:8001/authority/login` (also `/authority-admin` and
   `/authority/dashboard` resolve to the same page).
2. Enter **Username or Email** and the **password** assigned by the Super Admin.
3. Click **Sign In**.

Authentication uses the **existing** `POST /api/auth/login` endpoint — no
separate authentication backend exists. The username field accepts either the
account **username** or its **email**.

Role-based redirection after login (in `frontend/js/authority-admin.js`):

| Role            | Destination                                          |
|-----------------|------------------------------------------------------|
| `superadmin`    | Existing Super Admin dashboard (`/admin`)            |
| `authority_admin` | Authority Administration Portal dashboard          |
| `student`       | Rejected with a clear message (no dashboard access)  |

The backend independently enforces roles — the frontend routing is never the
only guard.

## 3. Authority Admin URLs

| URL                              | Purpose                                |
|----------------------------------|----------------------------------------|
| `/authority/login`               | Authority Admin sign-in page            |
| `/authority/dashboard`           | Same portal (login first if signed out) |
| `/authority-admin`               | Same portal (legacy alias)              |
| `/admin`                         | Super Admin panel (unchanged)           |

## 4. How credentials are assigned

- The Super Admin sets the initial password inside the **Add Authority Admin**
  form. It is shown in the form once, then hashed with bcrypt and stored in
  `users.hashed_password`.
- Passwords are **never** returned by any API (`_user_view` / identity
  payloads contain no credential material).
- Super Admin cannot recover a password; if it is lost, the account must be
  reactivated with a new password (contact the system administrator — the
  password-change path for admins is the portal's own *My Profile* → password
  rotation, which verifies the current password first).

## 5. Authority scoping

**Rule:** an Authority Admin can only ever see data belonging to the authority
stored in `users.authority_id` (the server-derived scope).

- Every `/api/authority-admin/*` request derives scope from
  `authenticated_user.authority_id` — **never** from query parameters, URL
  segments or request bodies (`backend/app/authority_admin/routes.py`,
  `portal.py`).
- A grievance that does not belong to the admin's authority is
  indistinguishable from a missing one (404), so cross-authority access cannot
  even be probed.
- Forged `authority_id` values in request bodies are ignored; this is covered
  by IDOR regression tests.
- Authority Admins have **no** route to change `role` or `authority_id`.

## 6. Authority Admin permissions

| Capability                          | Super Admin | Authority Admin |
|-------------------------------------|:-----------:|:---------------:|
| Manage authorities / categories     | ✅          | ❌               |
| Create / edit / deactivate / reassign admins | ✅ | ❌      |
| View all admin accounts             | ✅          | ❌               |
| View all grievances                 | ✅ (global) | only own authority |
| Mark grievances read / unread       | —           | ✅ (own authority) |
| Update workflow status              | —           | ✅ (own, permitted statuses) |
| Reply to students (email)           | —           | ✅ (own authority) |
| View / edit own profile             | —           | ✅ (no role/authority change) |
| Change own password                 | —           | ✅ (current password verified) |

## 7. How to deactivate an Authority Admin

1. Super Admin → **Authority Admins** tab → click the administrator's card.
2. In the detail drawer click **Deactivate** (or **Activate** for a disabled
   account).
3. Confirm in the dialog. The account's `is_active` flag flips; the change is
   audited.
4. Deactivated admins can **no longer log in**, and their existing JWTs are
   rejected immediately by the auth guards.

## 8. How to reassign an Authority Admin

1. Super Admin → **Authority Admins** tab → click the administrator's card →
   **Reassign Authority** (or use **Edit** and change the authority).
2. The confirmation explicitly warns:
   *"Changing the assigned authority will change which grievances this
   administrator can access."*
3. Saving calls the guarded assign endpoint; the server re-validates that the
   new authority exists, is active, and the target account is an
   `authority_admin`.

The admin's old grievances remain stored; new access resolves against the new
`authority_id`.

## 9. Grievance read / status / reply workflow

- **Unread/read:** new grievances start **unread**. The portal shows unread
  counts (dashboard cards + inbox badge). Opening a grievance's detail and/or
  clicking **Mark as Read** persists `is_read`/`read_at`/`read_by` **in the
  database** (survives refresh, logout, other sessions) and is audited
  (`grievance.read` / `grievance.opened` with actor, role, timestamp).
- **Status:** every transition goes through `record_status_change()` — the
  single service that writes the immutable `grievance_status_history` table.
  No code path mutates status directly. Available workflow statuses are the
  existing vocabulary: `submitted`, `acknowledged`, `in_progress`, `resolved`,
  `closed`, `rejected` (draft is creation-only). No duplicate status system.
- **Reply:** the portal's grievance detail offers a **Reply to Student**
  textarea → **Send Response**. The response is stored on the grievance
  (`authority_response`, `authority_response_at`), recorded in history
  (is_internal = False, same-status row), and emailed to the student's
  submitted email via `app/utils/email.py` (`send_grievance_response`).
- **Email:** notifications are **best-effort**:
  - new grievance → authority notification email (env-driven, off unless
    `EMAIL_ENABLED=true`)
  - response → student email
  - a failed send never rolls back the stored grievance; the outcome is
    recorded (`response_email_status` / audit `email_sent`/`email_failed`) —
    a failure is never silently reported as success.
- **Audit:** all of the above are recorded with the existing audit framework
  (`app/utils/logging.audit`) — actor id, actor role, target, IP, timestamp.

---

## Files in this feature

**Backend**
- `backend/app/authority_admin/routes.py` — Super Admin management router
  (`/api/admin/authority-admins`) + Authority Admin portal router
  (`/api/authority-admin`) with scope derivation
- `backend/app/authority_admin/service.py` — account CRUD, `user/authority`
  views (never credentials), password change
- `backend/app/authority_admin/portal.py` — scoped dashboard/grievance/read/
  status/response business logic
- `backend/app/auth/routes.py` — login accepts username **or** email; safe
  identity payload
- `backend/app/main.py` — `/authority/login`, `/authority/dashboard` aliases
- `backend/app/utils/email.py` — notification + response emails

**Frontend**
- `frontend/pages/admin.html` / `frontend/js/admin.js` / `frontend/css/admin.css`
  — redesigned Authority Admins section (stat cards, live authority filter,
  clickable admin cards, detail drawer, authority preview in the form,
  confirmation dialogs, `extractApiError` — no `[object Object]`)
- `frontend/pages/authority-admin.html` / `frontend/js/authority-admin.js` /
  `frontend/css/authority-admin.css` — Authority Administration Portal
  (login → dashboard → grievances → detail → profile)
- `frontend/js/navigation.js` — footer link to the Authority Portal

**Tests**
- `backend/tests/test_phase3_rbac.py` — RBAC, list empty-param regression,
  email login, authority filter, IDOR, audit, history
- `backend/tests/test_authority_admin_portal.py` — 88-check portal E2E suite
  (auth, lifecycle, isolation, emails, hygiene, superadmin regression)