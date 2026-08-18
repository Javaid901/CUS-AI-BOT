# Smart AI Service Orchestrator — Implementation Report

Date: 2026-08-08
Scope: Planner + query-understanding hardening on top of the existing deterministic
AI Orchestrator (no second chatbot, no LLM decision loop; planner stays rule-based).

## What was delivered

A conversational upgrade that routes every message through:
  raw message → query understanding (typo/abbrev/alias + confidence)
             → context memory (programme/scheme/semester/service/college)
             → news → authority → catalogue → services → RAG → LLM summary

All routing stays deterministic and data-gated; the LLM only summarizes retrieved
evidence and never invents authoritative facts.

## Files changed

1. `backend/app/orchestrator/query_understanding.py`
   - New typo corrections: `subjcts→subjects`, `notifcation→notification`,
     `circualr→circular`, `calnder→calendar`, `announcment→announcement`, etc.
   - Protected vocabulary (never fuzzy-corrected):
     - authority nouns (`registrar`, `vice chancellor`, `coe`, `controller`, `dean`,
       `officer(s)`, `incharge`, ...) — stops `registrar → register` hijacking the
       student-registration service;
     - news nouns (`notice`, `circular`, `notification`, `calendar`, `announcement`,
       `holiday`, `bulletin`, `news`, `update`, ...);
     - course-discovery verbs (`offer/offers/offered/offering`, ...) — prevents the
       edit-distance-2 false match `offers → officers` that would break
       "which college offers BCA".

2. `backend/app/orchestrator/planner.py`
   - New **Rule 3a — News / website knowledge**: a single news noun
     (notice/circular/notification/calendar/announcement/holiday/bulletin/update)
     routes to a new `news` action with a news-scoped retrieval query
     (`extra.is_news`). Bare navigation labels stay with the existing button flow.
   - New **Rule 3b — Authority / office intent** (moved from late Rule 16, so slot-fill
     can no longer swallow it):
     - strong keyword match (score ≥ 2.0) **plus** explicit authority evidence
       (question markers or office nouns) — keyword overlap alone is rejected
       ("documents required for admission" stays a slot-fill);
     - explicit office questions ("who is registrar", "who handles exams") resolve
       directly via the department alias/service maps;
     - escalation patterns (talk to a human / complaint / helpline) unchanged.
   - Rule docstring updated to document the new ordering (3a news, 3b authority).

3. `backend/app/catalogue/detect.py`
   - New **scheme-scoped overview branch (8d)**: `"BCA under NEP"` /
     `"per NEP"` / `"following the NEP programme"` resolves to
     `{op: overview, programme, scheme, scheme_name, scheme_code}` so the
     engine can carry the scheme forward instead of falling into a bare
     programme-detail route.

4. `backend/app/authority/matcher.py`
   - Department aliases aligned to the canonical row names:
     `exam(s)/examination(s)/coe/datesheet/results → Controller of Examinations`
     (± department now resolves even when keyword overlap is thin).

5. `backend/app/orchestrator/engine.py`
   - New `action == "news"` branch: mirrors RAG (hybrid retrieval + `run_chat`)
     with `last_intent="news"` and a synthetic `document_kind=news` hint in the
     rag context; metrics reported as `response_source=news`.

6. `backend/tests/test_smart_orchestrator.py` (new acceptance battery)
   - 8 spec categories, 65 checks; seeds authoritative rows (Registrar Office,
     Controller of Examinations) into the test DB / cache; expectations aligned
     to the corrector's lowercase output (`bcaa→bca`, `mcaa→mca`).

## Routing table (new/changed)

| User message                     | Before           | Now            |
|----------------------------------|------------------|-----------------|
| latest admission notice          | navigation (menu)| news            |
| latest circular                  | clarify          | news            |
| examination notification         | slot_fill        | news            |
| holiday notice                   | clarify          | news            |
| academic calendar                | slot_fill        | news            |
| latest exam notification         | slot_fill        | news            |
| who is registrar                 | connector (registration) | authority (Registrar Office) |
| who handles exams                | slot_fill        | authority (Controller of Examinations) |
| bca under nep                    | structured (bare) | catalogue/overview (scheme-scoped) |
| show ug courses under nep        | catalogue (scheme picker) | catalogue/list (NEP direct) |
| subjcts                          | unchanged        | subjects         |
| offer/offers in course queries   | -                | unchanged (protected from `officers`) |

## Verification

- `tests/test_smart_orchestrator.py`          65/65  (was 50/65 at handover)
- `tests/test_intelligence.py`                213/213 (baseline preserved)
- `pytest -q`                                 59 passed
- `tests/test_website_sync_engine.py`         66/66
- `tests/test_website_sync_hardening.py`      82/82

## Known limitations

- "who handles the USC wing" without a known department alias and small keyword
  pool falls through to evidence-gated removal → RAG (by design, avoids
  false positives).
- Programme-scheme scope uses phase words in the same sentence
  (`under/per/as per/in`); scheme-from-context is not downgrading from an
  overview to a targeted question when the overview data is empty.
- The `news` retrieval uses the knowledge base after syncing; if the site has
  no notices yet, the RAG fallback still forwards the query to the LLM
  summary pipeline (no dead-end menu).