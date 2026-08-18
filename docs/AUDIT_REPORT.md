# CUS AI Assistant — Conversation Engine Audit & Workflow-Leakage Fix

Final report for the end-to-end audit of the conversation engine and the elimination of
workflow leakage. Canonical bug fixed: **Admission → choose programme level →
"Undergraduate" → wrongly enters the Results workflow** (and related lookalike leaks).

---

## 1. Architecture issues

1. **Semantic model in front of deterministic routing.** The route planner ran a
   sentence-embedding classifier (`intent_classifier.py`) against every message — including
   terse navigation labels rendered by the bot itself (option ids, programme names, bare
   level keywords). Embeddings warp short tokens into a *lookalike* topic
   (`ug` → results), and the planner then injected that topic into `ExtractedEntities.topic`.
2. **Topic enrichment acted as a workflow hijack.** Stage 0c ("semantic topic enrichment")
   populated `e.topic` from the classifier when entity extraction returned nothing. For a
   bare label this permanently hijacked routing: the deterministic option/level logic was
   bypassed and services like Results were selected instead of the requested Admissions group.
3. **Two classifiers with overlapping, conflicting responsibilities.** `chat/intent_router.py`
   owns narrow nav labels (`classify`) while `orchestrator/intent_classifier.py` owns semantic
   intent. Nothing coordinated them, so the semantic pass re-labelled what the router had
   already resolved correctly.
4. **No isolation between navigation and generic lookup.** A single
   `get_selection_response()` had to serve bot-option clicks, back-navigation, and free-text
   lookups; the "back" signal was erased downstream and raw entity text leaked into the
   response.
5. **Query understanding treated navigation tokens as spelling candidates.** "back" was
   one-edit-distance from "ba" (a programme), so fuzzy-correction silently erased the back
   signal and turned it into a programme-selection.

## 2. Root causes

Empirically reproduced (see Tests): a terse navigation label warps through the embedding
classifier into an unrelated semantic class —

| Label | Semantic classifier said | Opened |
|---|---|---|
| `undergraduate` | colleges | Colleges list |
| `ug` | results | Results list |
| `pg` | results | Results list |
| `phd` | authorities | Authorities |
| `integrated` | results | Results list |
| `mba` | courses | Courses |
| `bca` | eligibility | "BA — Eligibility" |
| `ba` | authorities | Authorities |

The enrichment step then wrote these warped intents into `e.topic`, so the deterministic
option-selection code never ran. The same corruption turned `back` into `ba`.

## 3. Files modified

| File | Change |
|---|---|
| `backend/app/chat/intent_router.py` | `classify()` now resolves literal nav labels **first**, falls back to the semantic classifier only for natural-language phrasing |
| `backend/app/orchestrator/planner.py` | Semantic-skip + enrichment guards; back rule; bare-level routing; rule-1 fix |
| `backend/app/orchestrator/query_understanding.py` | `"back"` / `"cancel"` / `"skip"` added to `_KNOWN_WORDS` so back is not fuzzy-corrected to `ba` |
| `backend/tests/test_conversation_workflow_isolation.py` | **New regression suite** (72 checks) |

Left intentionally unchanged: `orchestrator/intent_classifier.py` (semantic misclassification
is now *bypassed*, not retrained), `orchestrator/state.py`, `orchestrator/extractor.py`,
connectors, catalogue, results, attendance, NEP services.

## 4. Bugs fixed

1. **Workflow leakage (canonical).** `Admission → Undergraduate` opened Colleges; `ug`/`pg`/
   integrated opened Results; `phd` → Authorities; `bca` → "BA — Eligibility". All now open
   the intended groups.
2. **`back` erased into `ba`.** Fuzzy spelling-correction re-wrote the back control into a
   programme; fixed in `query_understanding.py`.
3. **Planner Rule "back" returned garbage.** Rule 2 returned the raw entity/planned response
   that the engine's navigation branch cleared. Now calls
   `get_selection_response(chat_id, "back")` and forwards the options/detail payload
   (`planner.py:196`).
4. **Planner Rule-1 formula had wrong syntax** (from prior session) — patched.
5. **Bare programme levels routed nowhere.** `ug`/`pg`/`phd` entities with no message context
   now go straight to their own navigation response (`planner.py:825`).

## 5. State management improvements

- **Nav path is now normalized at write time.** `get_selection_response()` stores the
  *canonical* category (`admission` → `admissions`) via the `_BROAD_KEYWORDS` lookup
  (`intent_router.py:770`–`777`), so `back` pops the correct parent. Verified e2e:
  `Admission → ug → BA` stores `admissions → ug → ba`, first `back` → Admissions, second
  `back` → Welcome.
- **Conversation state preserved as-is.** `ConversationState` (`student_*` fields,
  `ServiceAuthState`, `Breadcrumb`, TTL) unchanged; no regressions.
- **Continuation-ID namespacing** is tracked but intentionally deferred (see Section 9).

## 6. Planner improvements

- **Literal-first classification** (`intent_router.py:118`–`125`): bare tokens in
  `_BROAD_KEYWORDS` and the new `_LEVEL_WORDS` map resolve deterministically; the embedding
  model no longer runs for them (`_is_semantic_skippable`, `planner.py:1051`).
- **Enrichment guard** (`_semantic_enrichment_allowed`, `planner.py:1057`): blocks
  topic-enrichment when the message is its own nav label (option id, bare programme, bare
  level like `ug`/`undergraduate`, or a domain keyword).
- **Explicit `phd` branch** added to `get_broad_response()`.
- **Back-navigation now deterministic** (Rule 2, `planner.py:194`–`204`).

## 7. Context improvements

- **Academic-scheme and semester awareness** (Stages 0d/0e) retained; NEP/CBCS routing and
  semester-folded RAG unchanged.
- **Guard caps**: `ctx.query_corrected` is only recorded when the query-understanding pass
  actually fired (non-trivial headers); no context is polluted by the labels that no longer
  pass through the classifier.
- No new global state; service context (`ServiceAuthState`) isolation untouched.

## 8. Tests performed

Run in `backend` (Windows PowerShell/Python 3.14):

| Suite | Result |
|---|---|
| `test_conversation_workflow_isolation.py` (new) | **72/72 passed** |
| `test_intelligence.py` | **213/213 passed** |
| `test_scheme_semester.py` | **41/41 passed** |
| `test_catalogue.py` | **60/60 passed** |
| `test_intent_classifier.py` | **7/7 passed** |

Coverage of the new suite: literal-label classification, level routing map, every
`_LEVEL_WORDS` entry, `get_broad_response` per category incl. `phd` and `back`, planner
back-rule payload, bare `ug`/`pg`/`phd` nav, enrichment-guard positives/negatives, alias
resolution, adversarial near-miss tokens, and full-engine e2e (`Admission → ug → BA →
back → Admissions → back → Welcome`).

## 9. Remaining recommendations

1. **Continuation-ID namespacing** in `state.py` — prefix continuation ids by workflow so one
   session cannot resume another workflow's continuation. Currently safe (context is already
   per-session), but worth hardening.
2. **Retrain/refit the semantic classifier** on a domain corpus containing the nav-label
   tokens so it no longer conflates them; the bypass is correct and cheap, but a robust
   model removes the whole failure mode rather than sidestepping it.
3. **Add `µ` level aliases** (`integrated`, `pg`, `diploma`, etc.) to `_LEVEL_WORDS` when new
   programme levels are added; keep `_BROAD_KEYWORDS` and the level map as the single source
   of truth for label normalization.
4. **E2E smoke script** — promote the temporary repro harness to a checked-in smoke runner
   wired into CI so workflow leakage is blocked permanently.