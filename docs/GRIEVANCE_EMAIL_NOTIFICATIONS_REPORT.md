# Grievance Email Notifications — Implementation Report

Real-time, reliable grievance email delivery for the Cluster University of
Srinagar CUS AI Grievance System. This is an **additive** enhancement: the
existing email service, SMTP configuration, submission/authority/response
senders, audit trail, status-history chain and API contracts are untouched and
reused. No second SMTP service, provider, worker or queue was introduced.

---

## 1. Root cause of the missing student email

The grievance submission plumbing was correct — **the deployment had no email
configuration at all**. `backend/.env` contains no `EMAIL_*` / `SMTP_*` keys,
so at runtime:

| Setting              | Runtime state |
|----------------------|---------------|
| `EMAIL_ENABLED`      | missing/empty (defaults to `False`) |
| `SMTP_HOST`          | missing/empty |
| `SMTP_PORT`          | default `587` |
| `SMTP_STARTTLS`      | default `True` |
| `SMTP_USER`          | missing/empty |
| `SMTP_PASSWORD`      | missing/empty |
| `MAIL_FROM`          | missing/empty |
| `MAIL_FROM_NAME`     | default "CUS Grievance Cell" |
| `PUBLIC_BASE_URL`    | missing/empty |

`app/utils/email.py` is best-effort by design: when `enabled()` is false it
returns `False`, the grievance is saved with `email_status = "failed"` and no
message is ever sent. So the student received nothing, while the UI honestly
reported "⚠ We could not send a confirmation email". **The fix is to supply
real SMTP credentials in `backend/.env`** (block below) — no code change was
required for the email itself to start flowing.

## 2. Existing email infrastructure discovered

* `backend/app/utils/email.py` — single best-effort SMTP sender:
  * `enabled()` — `EMAIL_ENABLED && SMTP_HOST`
  * `_deliver(to, subject, body)` — `smtplib`, timeout 15 s, optional
    STARTTLS + login, **never raises**, returns `True` only on provider
    acceptance.
  * `send_grievance_acknowledgement` (student confirmation, submission)
  * `send_grievance_to_authority` (new-grievance alert to `authorities.email`)
  * `send_grievance_response` (official response to student)
* Senders invoked synchronously in `backend/app/grievance/intake.py`
  (`submit_grievance`, after commit) and `backend/app/authority_admin/portal.py`
  (`add_response`).
* Delivery outcomes stored on the grievance row: `email_status`,
  `authority_email_status`, `response_email_status`; failures audited with
  `grievance.email_failed`.
* No generic job queue exists for email; `app/ingest/worker.py` is document
  ingestion only (reused, not extended).
* No acknowledgement email, no resolution email, no notification log table.

## 3. Existing components reused (no duplication)

* `app/utils/email.py` — the only SMTP path; all new messages go through the
  same `_deliver`.
* `send_grievance_acknowledgement`, `send_grievance_to_authority`,
  `send_grievance_response` — signatures preserved, call sites preserved,
  existing behavior preserved.
* `app/grievance/intake.py` submission flow, `client_request_id` idempotency,
  `record_status_change` chain, `audit()` logging, `authority_service` cache,
  `grievance_status_history` as the source of truth.
* `app/authority_admin/portal.py` `change_status` / `add_response` — extended
  additively, response shapes unchanged (one new `notification` field).
* FastAPI security dependencies (`require_superadmin`), existing config model.

## 4. Files changed (new / modified)

| File | Change |
|------|--------|
| `backend/app/grievance/models.py` | **new** `GrievanceNotification` model + event/status constants |
| `backend/app/grievance/notifications.py` | **new** centralized notification service (ledger + triggers) |
| `backend/app/utils/email.py` | spec-formatted templates/subjects; **new** `send_grievance_acknowledged`, `send_grievance_resolved`, `send_test_email`; `_fmt_dt` helper |
| `backend/app/grievance/intake.py` | passes first name / submitted timestamp to senders; passive ledger recording after the existing sends |
| `backend/app/authority_admin/portal.py` | `change_status` → automatic ack/resolution notification (post-commit); `add_response` → ledger entry (existing send unchanged); response email gets response timestamp |
| `backend/app/admin/routes.py` | **new** Super-Admin `GET /api/admin/email/health`, `POST /api/admin/email/test` |
| `backend/app/database.py` | `_upgrade_schema`: `grievance_notifications.provider_message_id` for pre-existing DBs |
| `backend/app/models/__init__.py` | re-export `GrievanceNotification` (metadata registration) |
| `backend/tests/test_grievance_notifications.py` | **new** 63-check notification suite (pytest + standalone) |
| `frontend/js/chatbot.js` | honest success-card wording: masked student email on confirmed delivery; "logged for retry" on failure |
| `frontend/js/authority-admin.js` | honest status-change and response toasts (never claim "sent" without provider acceptance) |

## 5. Database changes

New table `grievance_notifications` (created automatically by the existing
`create_all()` startup path; `provider_message_id` patched into pre-existing
databases by the additive `_upgrade_schema`):

* `id` UUID PK, `grievance_id` FK (CASCADE), `event_type`,
  `recipient_role` (`student` / `authority`), `recipient_email`,
  `status` (`sent` / `failed` / `skipped`), `retry_count`, `attempted_at`,
  `sent_at`, `provider_message_id` (reserved), `error_message`, `created_at`.
* **Unique** `(grievance_id, event_type, recipient_role)` — the idempotency key.
* No secrets are ever stored (recipient address, status, timestamps, failure
  text only).

## 6. Email delivery architecture

```
SAVE FIRST → SEND IMMEDIATELY → RECORD RESULT → REPORT HONESTLY
```

* Submission: grievance + status history committed **first**; then, in the
  same request, the existing senders fire immediately (no queue, no worker,
  no artificial delay — only normal SMTP latency); ledger rows record the
  outcome; receipt returns honest flags.
* Status changes: committed via the existing `record_status_change` chain;
  **after** commit, acknowledged/resolved student emails are attempted
  immediately.
* Every attempt is synchronous best-effort with a 15 s SMTP timeout. Failure
  can never roll the grievance back.

## 7. Submission notification (student)

Subject `Grievance Submitted Successfully — <REFERENCE>`; body: first name,
reference, authority, category, submitted date/time, `Status: Submitted`,
forwarding note, tracking-token reminder. Recipient = the email the student
entered on the submitted grievance record (never the browser/session).

## 8. Submission notification (authority)

Subject `New Grievance — <REFERENCE>: <category>`; body: reference, category,
submitted date/time, full grievance text, student details (name, roll number,
college, semester, email), authority dashboard link when `PUBLIC_BASE_URL`
is configured. Recipient = `authorities.email` **from the database record**
(never hardcoded, never client-supplied).

## 9. Acknowledgement notification (student)

New. Triggered automatically when an authority sets status →
`acknowledged` (no extra button). Subject `Grievance Acknowledged —
<REFERENCE>`; body: reference, authority, acknowledgement date/time (from the
status-history entry), `Status: Acknowledged`. Delivered by the new
`send_grievance_acknowledged` through the same `_deliver` SMTP path.

## 10. Response notification (student)

Existing `send_grievance_response` reused unchanged. Subject now
`Response to Your Grievance — <REFERENCE>`; body: reference, authority,
status, response date/time, the official response text. Fired from
`add_response` immediately after the response is persisted; the existing
`response_email_status` contract is preserved and a ledger row is appended.

## 11. Resolution notification (student)

New. Triggered automatically when an authority sets status → `resolved`.
Subject `Grievance Resolved — <REFERENCE>`; body: reference, authority,
resolution date/time (from the status-history entry), `Status: Resolved`,
the recorded official response when present.

## 12. Failure handling

* DB commit failure → **no email is attempted** (emails only run after the
  state is committed).
* Email failure → grievance/acknowledgement/response/resolution **remains
  intact**; the ledger row is `failed` with `attempted_at` and
  `error_message`; the API response and UI report the operation as
  successful and the notification as failed/retryable. The UI never says
  "submitted successfully" falsely, and never claims "email sent" without
  provider acceptance.
* UI wording (existing style):
  * success: "Grievance submitted successfully … A confirmation email has
    been sent to: <student email>"
  * email failed: "We could not send the confirmation email right now and it
    has been logged for retry."
  * authority portal status toast: "Status updated to X. The student has
    been notified by email." / "…could not be delivered right now and has
    been logged for retry."

## 13. Retry strategy

No background worker was added (the existing infrastructure has none for
mail; the ingestion worker is document-specific). Retryability is provided by
the persistent ledger:

* A `failed` / `skipped` event may be re-attempted (e.g. by an operator or a
  future worker) by re-invoking the event trigger; the attempt bumps
  `retry_count` on the **same** row.
* A `sent` event is terminal: `notify_status_change` short-circuits and sends
  nothing.
* The API-level idempotency (`client_request_id`, unique reference,
  unique `tracking_token_hash`, 409 on repeated status changes) already
  prevents duplicate operations from being attempted at all.

## 14. Idempotency strategy

* Notifications: unique `(grievance_id, event_type, recipient_role)` — the
  ledger upsert plus the "already sent" short-circuit guarantee at most one
  delivered email per event, even if a retried acknowledgement/resolution
  operation re-enters the trigger.
* A notification is only ever considered delivered when the provider
  **accepted** it (`status = sent`); `failed` and `skipped` remain retryable
  (queued/processing are not modeled because nothing is queued).

## 15. Security considerations

* Never stored/logged: SMTP passwords, API keys, JWTs, tokens, credentials.
  Audit and ledger records hold statuses, recipient addresses and failure
  text only.
* Student email always from the persisted grievance record; authority email
  always from the database authority record.
* Authority Admin can only act on (and thus trigger notifications for)
  grievances scoped to their own `users.authority_id` (existing
  backend-enforced scope); cross-authority access stays 404.
* Health/test endpoints are `require_superadmin`-only; they report
  configured/connectivity states and never echo secrets. Test recipient is
  validated and supplied explicitly by the Super Admin.

## 16. Super Admin email health (Phase 15)

* `GET /api/admin/email/health` → `{configured, provider, smtp_host_configured,
  smtp_port, starttls, mail_from_configured, email_enabled, connection
  (not_configured|ok|unreachable), note}` — live SMTP connectivity probe,
  no secrets.
* `POST /api/admin/email/test` `{to_email}` → sends a controlled test message
  through the production sender and reports `accepted` honestly.
* Verified live: superadmin 200; authority_admin **403**.

## 17. Tests added

`backend/tests/test_grievance_notifications.py` — 63 checks, standalone +
pytest entry, real loopback SMTP:

* submission: grievance created, student + authority notification invoked,
  correct student and authority recipients (DB authority email)
* acknowledgement: status changed, history created, acknowledgement email
  invoked, one message only
* resolution: status changed, history created, resolution email invoked
* response: saved, response email invoked, exactly one new message
* non-notifying transitions (`in_progress`): no email, no ledger row
* failure: SMTP down → operation succeeds, status intact, ledger `failed`
  with timestamps + error; retry bumps `retry_count` on the same row; SMTP
  recovery → same row becomes `sent`; exactly one recovery email
* idempotency: re-invoked event → `deduplicated: true`, no duplicate email,
  single row; API second acknowledge → 409
* recipient discipline: student without email → `skipped`; authority email
  from DB; no cross-authority emails; admin A cannot open B's grievance (404)
* submission ledger rows for student + authority on the intake path
* log hygiene: no secrets anywhere in notification rows

## 18. Complete test results

* Full suite: **`python -m pytest tests/ -q` → 114 passed, 0 failed**
  (includes the new suite; all pre-existing grievance, portal, RBAC,
  intake, E2E, authority, catalogue and analytics tests stay green).
* Standalone E2E email flow (`test_grievance_workflow_e2e_email.py`):
  **38 passed, 0 failed**.
* `py_compile` on all changed Python: PASS.
* `node --check` on `chatbot.js`, `authority-admin.js`, `admin.js`: PASS.

## 19. Real email verification result (Phase 19)

Live server on `http://127.0.0.1:8001` (restarted with the new code):

* `GET /api/admin/email/health` → `configured: false`, `connection:
  not_configured` (honest — SMTP is not configured in this environment).
* `POST /api/admin/email/test` → `accepted: false` with the reason above.
* Real submission via the API: 201, reference issued, `email_confirmed:
  false`, `authority_email_status: "failed"` — honest, grievance intact.
* Authority acknowledgement: 200, status committed, `notification.status:
  failed` — honest, no rollback.
* Ledger verified in the DB: `grievance_submitted` (student + authority) and
  `grievance_acknowledged` rows all present with `failed` status.
* **A real inbox delivery could not be performed because no SMTP credentials
  exist in this environment.** The moment `backend/.env` is populated with
  the block below and the server is restarted, the first submission will
  send immediately through the exact same path that the loopback-SMTP tests
  exercise end to end (submission → inbox, acknowledged → inbox, response →
  inbox, resolved → inbox).

### Activate real email (no code change)

Add to `backend/.env`, then restart via `start_server.ps1`:

```
EMAIL_ENABLED=true
SMTP_HOST=<your smtp host>
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_USER=<smtp username>
SMTP_PASSWORD=<smtp password>
MAIL_FROM=<sender address, e.g. grievance@cus.ac.in>
MAIL_FROM_NAME=CUS Grievance Cell
PUBLIC_BASE_URL=http://127.0.0.1:8001
```

Verify with `GET /api/admin/email/health` (expect `connection: ok`) and
`POST /api/admin/email/test` (expect `accepted: true`), then submit a
grievance from the chatbot with a real inbox address. Check Spam/Junk too —
sender reputation is a recipient-provider concern, not an application bug.

## 20. Remaining limitations

* No SMTP credentials in this environment (see §19) — the only blocker to a
  real inbox delivery.
* `provider_message_id` is reserved but unpopulated: `smtplib` does not
  expose the server message-id; a provider-API integration could fill it.
* No automatic retry worker: retries are manual/re-invocable via the ledger
  (there is no existing background-email worker to reuse, by design).
* SQLite does not enforce the FK `ON DELETE CASCADE` for
  `grievance_notifications` (ORM-level only); production Postgres does.
* Authority emails require `PUBLIC_BASE_URL` to include the dashboard link.

---

## 21. Follow-up: email delivery activated and verified (2026-08-10)

### Root-cause resolution
The sole root cause (missing SMTP configuration in `backend/.env`) has been
acted on. The system now performs REAL SMTP delivery end-to-end on this
machine via a dev SMTP sink (`backend/dev/smtp_sink.py`, listens on
`127.0.0.1:1025`, no auth/TLS, captures every accepted message to
`backend/dev/smtp_capture/*.eml`). `backend/.env` now sets:

```
EMAIL_ENABLED=true
SMTP_HOST=127.0.0.1
SMTP_PORT=1025
SMTP_STARTTLS=false
SMTP_USER=
SMTP_PASSWORD=
MAIL_FROM=grievance@cus.ac.in
MAIL_FROM_NAME=CUS Grievance Cell
PUBLIC_BASE_URL=http://127.0.0.1:8001
```

When real credentials appear, only the host/port/TLS/user/password values
change (see section 19) and the sink is stopped.

### Sender hardening (`app/utils/email.py` — additive, same contract)
* `_deliver` now emits per-attempt diagnostics:
  `[EMAIL] event=... recipient=j***@gmail.com provider=smtp status=ACCEPTED`
  and `[EMAIL FAILURE] event=... error_type=... retryable=...` — recipients
  are always masked; secrets never logged. Missing configuration now produces
  an explicit `error_type=NOT_CONFIGURED` / `CONFIG` log line instead of
  silence.
* Bounded retry: exactly one immediate re-attempt for transient transport
  errors only (`OSError`/`TimeoutError`/`SMTPServerDisconnected`).
  Auth rejections, recipient refusals and config errors are never retried.
* Event labels on every send: `GRIEVANCE_SUBMITTED_STUDENT`,
  `GRIEVANCE_SUBMITTED_AUTHORITY`, `GRIEVANCE_RESPONSE`,
  `GRIEVANCE_ACKNOWLEDGED`, `GRIEVANCE_RESOLVED`, `EMAIL_TEST`.

### Live verification (real server on :8001, real SMTP acceptance, actual DB)
`GET /api/admin/email/health` -> `configured=True provider=smtp connection=ok`
`POST /api/admin/email/test` -> `accepted=True`

Full lifecycle against a live grievance (ref `CUS-GRV-2026-A9897DE4`) with
real student/authority addresses:

+--------------------------+-------------------------------+-----------+--------+
| Event                    | Recipient                     | Ledger    | SMTP   |
+==========================+===============================+===========+========+
| Grievance submitted      | student (confirmation)        | sent      | accepted|
+--------------------------+-------------------------------+-----------+--------+
| New grievance (authority)| admission block office       | sent      | accepted|
+--------------------------+-------------------------------+-----------+--------+
| Acknowledged             | student                       | sent      | accepted|
+--------------------------+-------------------------------+-----------+--------+
| Official response        | student                       | sent      | accepted|
+--------------------------+-------------------------------+-----------+--------+
| Resolved                 | student                       | sent      | accepted|
+--------------------------+-------------------------------+-----------+--------+

All five messages were accepted by the SMTP server, recorded in
`grievance_notifications` with `status=sent` and `sent_at`, and saved as
.eml in the sink spool. Server log confirms six ACCEPTED lines (test + five
lifecycle events) with masked recipients. Unchanged: save-first ordering,
immutable status-history chain, honest UI wording (now the success texts apply
because delivery truly succeeds). Live-test data was removed afterwards.

### Regression
`python -m pytest tests/ -q` -> 114 passed (includes the 63-check
notification suite and the SMTP-failure/recovery paths, which still pass).

### Real-inbox status
STILL OUTSTANDING — the only blocker, and it is credentials, not code: a
real internet inbox (Gmail/Outlook/Zoho/... ) cannot be verified without
provider SMTP credentials (Gmail app password, etc.). The moment they are
added to `backend/.env` and the server is restarted, the four real-lifecycle
emails (submission/ack/response/resolution) must be re-run against a real
inbox and recorded as RECEIVED/NOT RECEIVED word-for-word per this report's
test 2.

---

## 22. REAL INBOX verification — Gmail SMTP (2026-08-10, RESOLVED)

Gmail SMTP activated in `backend/.env` (smtp.gmail.com:587, STARTTLS on,
app password — credential never printed; `MAIL_FROM` corrected to the real
address, password stored space-stripped). Only configuration changed; **no
application code was modified** in this step.

Verification method (all on the live server :8001, real Gmail account inbox
checked via IMAP `imap.gmail.com:993` with the same app password — never
logged or printed):

1. `GET /api/admin/email/health` -> `configured=True connection=ok` (real
   TCP+TLS to smtp.gmail.com).
2. `POST /api/admin/email/test` -> `accepted=True`; message **RECEIVED** in
   the inbox (verified via IMAP).
3. Full lifecycle, pre-login student (no account created — guest
   `/api/grievances` submission, unchanged flow), grievance
   `CUS-GRV-2026-1DDD54F1` routed to a **temporary** authority created for
   the test (contact email = the real inbox so the authority notification
   also lands in a verifiable mailbox):

   | Step | Action | SMTP accepted | IMAP inbox received |
   |------|--------|---------------|---------------------|
   | Submission | student acknowledgement (email_confirmed=True) | yes | **YES** |
   | Authority  | "New Grievance — CUS-GRV-2026-1DDD54F1: Fee and Financial Assistance" to authority email | yes | **YES** |
   | Acknowledge| "Grievance Acknowledged" | yes | **YES** |
   | Response   | "Response to Your Grievance" (response_email_status=sent) | yes | **YES** |
   | Resolve    | "Grievance Resolved" | yes | **YES** |

   All five subjects observed verbatim in the inbox (IMAP SEARCH + header
   fetch), sequential timestamps, from `CUS Grievance Cell`.

4. Ledger audit (`grievance_notifications`): all 5 rows `status=sent` with
   `attempted_at`/`sent_at`, `retry_count=1`, `error_message=None`, no
   fake provider ids (smtplib exposes none). Grievance columns
   `email_status`/`authority_email_status`/`response_email_status` all
   `sent`; immutable status-history chain intact
   (draft->submitted->acknowledged->[response]->resolved).
5. The recommendation step (`/api/grievances/recommend`) returned no match
   for the test text (LLM/keyword matcher absent); identification is
   demonstrated by the existing dropdown flow and the authority notification
   targeting the selected authority's configured email.
6. Cleanup: removed ONLY the temporary authority, temporary authority-admin,
   the test grievance (+ notifications/history/audit). Super Admin (1),
   authority admins (2), students (2) untouched. Capture sink idled.
7. Regression: `python -m pytest tests/ -q` -> **114 passed**.
