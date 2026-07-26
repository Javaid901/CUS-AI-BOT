# CUS AI Assistant — Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────┐
│                    FRONTEND (Vanilla JS)                 │
│  HTML pages  ←──  chatbot.js  ←──  SSE stream           │
│                         │                               │
│                    navigates user                        │
│                    through chips, cards, forms            │
└────────────────────────┬────────────────────────────────┘
                         │ POST /api/chat/ask
                         │ Authorization: Bearer <JWT>
                         ▼
┌─────────────────────────────────────────────────────────┐
│              FASTAPI BACKEND (chat/routes.py)             │
│                                                          │
│  1. Validates input, rate-limits, authenticates          │
│  2. Delegates to Orchestrator Engine                     │
│  3. Converts engine events → SSE frames                  │
│  4. Migrates nav state on anon→real chat_id transition   │
└────────────────────────┬────────────────────────────────┘
                         │ orchestrator.engine.process()
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              AI ORCHESTRATION ENGINE                         │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐   │
│  │ Intent      │  │ Conversation │  │ Service Router     │   │
│  │ Classifier  │  │ State        │  │                    │   │
│  │             │  │              │  │ Knowledge Engine  │   │
│  │ • nav       │  │ • service ctx│  │ Navigation Tree   │   │
│  │ • service   │  │ • auth state │  │ Student Services  │   │
│  │ • specific  │  │ • breadcrumb │  │ ┌──────────────┐  │   │
│  └──────┬──────┘  └──────────────┘  │ │ Connector    │  │   │
│         │                           │ │ Registry     │  │   │
│         ▼ routes to                 │ └──────┬───────┘  │   │
│  ┌──────────────┐                          │           │   │
│  │  NAV intent  │ ──→ intent_router        │           │   │
│  │  SERVICE     │ ──→ connector.authenticate/fetch      │   │
│  │  KNOWLEDGE   │ ──→ run_chat (RAG)                    │   │
│  └──────────────┘                                       │   │
└───────────────────────────────────────────────────────────┘
```

## Layer Architecture

### 1. Transport Layer (`chat/routes.py`)
- Thin SSE wrapper (120 lines)
- Handles input validation, JWT auth, rate limiting
- Converts engine event dicts → SSE wire format
- Manages anonymous→real chat_id migration

### 2. Orchestration Layer (`orchestrator/engine.py`)
- Single `process()` entry point for all messages
- Extended intent detection (navigation + service + knowledge)
- Routes to the correct handler based on intent + state
- Manages auth flow (login prompts, credential verification)
- Returns SSE-compatible dicts

### 3. State Layer (`orchestrator/state.py`)
- `ConversationState` per chat_id (in-memory dict)
- `ServiceAuthState` per service (session tokens only, NEVER credentials)
- `Breadcrumb` trail for multi-step navigation
- TTL-based eviction (30 min inactivity)
- Async-safe with `asyncio.Lock`

### 4. Knowledge Layer (`chat/service.py` + `ingest/`)
- Unchanged from original architecture
- RAG pipeline: retrieve → format → generate → stream
- ChromaDB vector search, Ollama LLM
- Structured citation display

### 5. Navigation Layer (`chat/intent_router.py`)
- Unchanged core logic
- Keyword-based intent classification
- Complete navigation tree (programmes, fee, results, etc.)
- Nav path tracking per chat_id

### 6. Service Connector Layer (`services/`)
- `ServiceConnector` abstract base class
- 11 placeholder connectors registered
- Clean `authenticate()` + `fetch()` interface
- Registry pattern for discovery

## SSE Event Protocol

| Event | Payload | Trigger |
|-------|---------|---------|
| `data: <token>` | Raw text token | LLM streaming |
| `event: options` | `{type, title, message, options[]}` | Navigation options |
| `event: detail` | `{type, title, fields[], actions[]}` | Information card |
| `event: auth_form` | `{type, service, title, message, fields[], submit_label}` | Student login prompt |
| `event: done` | `{chat_id, cited_chunks[]}` | End of response |
| `event: error` | `{message}` | Error |

## Student Auth Flow

```
User: "results"
  ↓
Orchestrator detects service intent (→ "results")
  ↓
Checks auth state — not authenticated
  ↓
Yields event: auth_form with registration_number + password fields
  ↓
User fills form, clicks "Sign In"
  ↓
Frontend packs credentials as "reg_no||password", sends as chat message
  ↓
Orchestrator detects state.last_intent == "awaiting_credentials"
  ↓
Parses credentials from "||" format
  ↓
Calls connector.authenticate(reg_no, password)
  ↓
On success: stores session_token in memory, yields detail card
On failure: re-yields auth_form with error message
```

## Security Rules

1. **Credentials NEVER stored**: Registration numbers and passwords exist only in the local scope of `authenticate()` — never written to DB, logs, localStorage, or persisted state
2. **Session tokens in memory only**: Stored in `ServiceAuthState.session_token`, destroyed on logout, timeout, or state eviction (30 min TTL)
3. **JWT for API auth**: Existing Bearer token pattern unchanged
4. **HTTPS required**: All communication encrypted in production

## File Index

```
backend/app/
├── orchestrator/
│   ├── __init__.py          # Module docstring
│   ├── engine.py            # Central routing (338 lines)
│   └── state.py             # Conversation state (132 lines)
├── services/
│   ├── __init__.py          # Module docstring
│   ├── base.py              # ServiceConnector ABC (100 lines)
│   ├── registry.py          # Connector registry (80 lines)
│   └── placeholders.py      # 11 placeholder connectors (409 lines)
├── chat/
│   ├── routes.py            # SSE transport (was 149, now 130 lines)
│   ├── service.py           # RAG pipeline (unchanged, 134 lines)
│   └── intent_router.py     # Navigation tree (unchanged, 685 lines)
└── ... (auth, ingest, admin, models, utils unchanged)

frontend/js/
├── chatbot.js               # Extended with auth_form handler (593 lines)
frontend/css/
├── chatbot.css              # Added auth form styles (307 lines)

docs/
├── ARCHITECTURE.md           # This file
├── API.md                    # API documentation
├── SEQUENCE_DIAGRAMS.md      # Interaction diagrams
└── EXTENSION_GUIDE.md        # How to add services and connectors
```
