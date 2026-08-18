# Project Audit Report — CUS AI Assistant

Date: 2026-08-10
Scope: full backend (`backend/app` — 128 Python files, ~32.4k lines) + frontend JS (`frontend/js` — 8 files) + test suite (19 files).

## 1. Scope covered

| Area | Coverage |
|---|---|
| Syntax (all py) | `py_compile` — 0 failures |
| Syntax (all frontend JS) | `node --check` — 0 failures |
| Test suite | `pytest tests/` — 114 passed (baseline and after fixes) |
| Live smoke test | health, login, chat (nav + structured + RAG), admin metrics, student portal flow, college list — all OK |
| Manual code review | config, database, main, models, auth, rate_limit, logging, errors, public, admin, authority, grievance, chat, ingest (retrieve/retriever/store/embed/worker/generator), request_manager, orchestrator (engine, student_session), services (demo_connectors, base), college, knowledge_sync, catalogue, analytics |

## 2. Bugs found and fixed

### HIGH severity — event-loop blocking (concurrency)
1. **`agent review conf.: app/chat/service.py`** — `retrieve()` (sync) ran directly in the async SSE path. It chains an Ollama embedding HTTP call (120s timeout), a Chroma query and a BM25 refresh loading up to 20k chunks.
   **Fix:** wrapped in `asyncio.to_thread()` inside `run_chat` (chat/service.py:129).
   *Verification: a live retrieval took 5.8s — that used to freeze the whole server per query.*

2. **`app/chat/service.py:149`** — the sync generator `stream_answer()` (sync `httpx.Client`, 180s timeout) was iterated inside the async generator; every token read blocked the event loop for all users.
   **Fix:** added `stream_answer_async()` to `app/ingest/generator.py` (own `httpx.AsyncClient` per call, `aiter_lines()`), and `run_chat` now streams `async for token in stream_answer_async(...)`.

3. **`app/orchestrator/engine.py` (~1685-1710)** — synchronous SQLAlchemy queries (`Student` lookup + bcrypt verify, `StudentSession` revoke loop) inside an async handler. Fixed with `asyncio.to_thread`.

4. **`app/orchestrator/engine.py:1531, 1825`** — all connector `fetch()` calls. Demo connectors declare `async def fetch` but execute synchronous SQLAlchemy work. Fixed with a `_run_coroutine_in_thread` helper (`asyncio.run` in a worker thread), so the shared `ServiceConnector` interface is unchanged.
   *Verified live: full student portal flow (portal → results → credentials → real results for CUS-2023-0001) returned correct data.*

### HIGH severity — ingest worker
5. **`app/ingest/worker.py:213-218`** — `asyncio.wait_for(to_thread(add_chunks_with_embeddings), timeout=30.0)` abandoned the thread on timeout: the job was marked failed while the Chroma write finished later — silent late write + double report. Fixed by removing `wait_for` (the semaphore already bounds parallelism).
6. **`app/ingest/worker.py` `_loop()`** — on cancellation while processing, the job/Document stayed stuck in `extracting`/`processing` forever. Fixed: track `self._current_id`, `_fail_abandoned()` marks the job + Document failed and publishes a `failed` event before re-raising.

### MEDIUM severity — data integrity / fairness
7. **`app/ingest/store.py:130-132`** — chunk/embedding count mismatch only logged and silently skipped storing; now raises `ValueError` so the worker marks the job failed instead of silently dropping data.
8. **`app/ingest/store.py:175`** — dedupe hash snapshot capped at 10,000 chunks; a doc with >10k chunks could re-store duplicates on re-upload. Raised to 100,000.
9. **`app/request_manager/admission_controller.py`** — queue path refunded the token debit at enqueue but never re-charged at execution, so a heavy user could queue unlimited expensive requests. Fixed: re-debit `token_bucket.consume()` when the queued request starts executing.
10. **`app/orchestrator/student_session.py:_fuzzy_phrase`** — window loop started at index 1, never 0: a portal-entry phrase with a typo in its first word was never matched. Fixed to start at 0.
11. **`app/orchestrator/student_session.py`** — `set_session`/`resolve_semester_list` never passed the DB, so the semester list was just `1..current_semester` even when the results tables had more. `set_session(..., db=db)` + `student_id` param; engine.py passes its session.

### Clean checks (reviewed, no defect)
- `CollegeService.get_college` is an in-memory dict lookup — NOT blocking (agent claim disproven).
- Retrieval cache, response cache, `_relevant` thresholding, dedupe citations — correct.
- `/_fuzzy_phrase` aside, credential handling: passwords never stored in state/context/audit; auth redaction present.
- Worker pool self-scaling, queue backpressure, service semaphores — correct.
- Rate limiting is single-process in-memory behind `X-Forwarded-For` (spoofable) — acceptable for dev, note for production.
- Frontend JS: syntax-clean; chatbot.js/admin auth flows consistent with backend endpoints.

## 3. Files changed

| File | Change |
|---|---|
| `backend/app/chat/service.py` | to_thread retrieve; async streaming |
| `backend/app/ingest/generator.py` | added `stream_answer_async()` |
| `backend/app/ingest/worker.py` | no wait_for abandonment; cancel-safe loop + `_fail_abandoned` |
| `backend/app/ingest/store.py` | raise on count mismatch; dedupe limit 100k; `-> int` |
| `backend/app/request_manager/admission_controller.py` | re-debit tokens on queued execution |
| `backend/app/orchestrator/student_session.py` | fuzzy matcher window fix; db-aware semester list |
| `backend/app/orchestrator/engine.py` | to_thread for auth DB + connector fetches; `_run_coroutine_in_thread`; pass db to set_session |

## 4. Verification

- `python -m py_compile` on all changed files — OK
- Full suite: `114 passed, 5 warnings` (warnings are external chromadb/fastapi deprecations)
- Live smoke against restarted server (`:8001`):
  - `/api/health` OK, Ollama reachable (llama3.2:1b / nomic-embed-text)
  - superadmin login OK; `/api/admin/metrics/operations` OK
  - Chat: nav path streams tokens; structured catalogue returns detail card; RAG retrieval+LLM streaming verified directly (async tokens received)
  - Student portal: entry menu → results → credentials → real result card (exercises fixes 3/4)
  - All smoke-test conversations removed afterwards

## 5. Remaining notes (non-blocking)
- `X-Forwarded-For` rate limiting is dev-only — use a real IP trust layer / Redis rate limiter in production.
- Demo connectors authenticate() is dead code (real auth uses the Student table) — harmless, could be removed.
- `asyncio.wait_for` removal means a hung Chroma write occupies a worker slot until it returns (bounded by max_concurrent=2) — the honest trade-off vs. phantom failures.