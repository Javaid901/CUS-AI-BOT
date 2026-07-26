# Sequence Diagrams

## 1. Knowledge Query (RAG)

```
User                  chatbot.js              routes.py           Orchestrator         intent_router    service.py    Ollama/Chroma
 │                        │                      │                    │                    │                │              │
 │  "What is BCA fee?"    │                      │                    │                    │                │              │
 │───────────────────────>│                      │                    │                    │                │              │
 │                        │  POST /api/chat/ask  │                    │                    │                │              │
 │                        │─────────────────────>│                    │                    │                │              │
 │                        │                      │  process()         │                    │                │              │
 │                        │                      │───────────────────>│                    │                │              │
 │                        │                      │                    │  classify()         │                │              │
 │                        │                      │                    │────────────────────>│                │              │
 │                        │                      │                    │  ("specific",None)  │                │              │
 │                        │                      │                    │<────────────────────│                │              │
 │                        │                      │                    │                    │                │              │
 │                        │                      │                    │  not service intent │                │              │
 │                        │                      │                    │  not nav intent     │                │              │
 │                        │                      │                    │                    │                │              │
 │                        │                      │                    │  run_chat()         │                │              │
 │                        │                      │                    │ ──────────────────────────────────────────────────>│
 │                        │                      │                    │                    │                │              │
 │                        │                      │                    │                    │     embed query │              │
 │                        │                      │                    │                    │────────────────>│              │
 │                        │                      │                    │                    │<───────────────│              │
 │                        │                      │                    │                    │                │              │
 │                        │                      │                    │                    │     Chroma search              │
 │                        │                      │                    │                    │───────────────────────────────>│
 │                        │                      │                    │                    │<───────────────────────────────│
 │                        │                      │                    │                    │                │              │
 │                        │                      │  event: token      │  yield token       │  stream tokens │              │
 │                        │<─────────────────────│<───────────────────│<─────────────────────────────────────│              │
 │  "BCA fee is..."       │                      │                    │                    │                │              │
 │<───────────────────────│                      │                    │                    │                │              │
 │                        │                      │                    │                    │                │              │
 │                        │                      │  event: done       │  yield done        │                │              │
 │                        │<─────────────────────│<───────────────────│<─────────────────────────────────────│              │
 │  Citations shown       │                      │                    │                    │                │              │
```

## 2. Navigation Flow

```
User                  chatbot.js              routes.py           Orchestrator        intent_router
 │                        │                      │                    │                    │
 │  "Admissions"          │                      │                    │                    │
 │───────────────────────>│                      │                    │                    │
 │                        │  POST /api/chat/ask  │                    │                    │
 │                        │─────────────────────>│                    │                    │
 │                        │                      │  process()         │                    │
 │                        │                      │───────────────────>│                    │
 │                        │                      │                    │  classify()         │
 │                        │                      │                    │────────────────────>│
 │                        │                      │                    │  ("broad","admissions")│
 │                        │                      │                    │<────────────────────│
 │                        │                      │                    │                    │
 │                        │                      │                    │  get_broad_response()│
 │                        │                      │                    │────────────────────>│
 │                        │                      │                    │<────────────────────│
 │                        │                      │                    │  {type:"options",   │
 │                        │                      │                    │   options:[UG,PG..]}│
 │                        │                      │                    │                    │
 │                        │   event: options     │  yield event       │                    │
 │                        │<─────────────────────│<───────────────────│                    │
 │  [UG] [PG] [PhD] ...   │                      │                    │                    │
 │<───────────────────────│                      │                    │                    │
 │                        │                      │                    │                    │
 │  User clicks "UG"      │                      │                    │                    │
 │───────────────────────>│                      │                    │                    │
 │                        │  POST chat_id + "ug" │                    │                    │
 │                        │─────────────────────>│                    │                    │
 │                        │                      │  process()         │                    │
 │                        │                      │───────────────────>│                    │
 │                        │                      │                    │  is_option → True   │
 │                        │                      │                    │  get_selection_response("ug")
 │                        │                      │                    │────────────────────>│
 │                        │                      │                    │  advance_path        │
 │                        │                      │                    │  get_broad_response("ug")
 │                        │                      │                    │<────────────────────│
 │                        │   event: options     │                    │                    │
 │                        │<─────────────────────│                    │                    │
 │  [BA] [B.Sc] [BCA]...  │                      │                    │                    │
 │<───────────────────────│                      │                    │                    │
 │                        │                      │                    │                    │
 │  User clicks "BCA"     │                      │                    │                    │
 │───────────────────────>│                      │                    │                    │
 │                        │                      │  get_selection_response("bca")
 │                        │                      │───────────────────>│────────────────────>│
 │                        │                      │                    │  advance_path        │
 │                        │                      │                    │  return detail card  │
 │                        │   event: detail      │                    │                    │
 │                        │<─────────────────────│<───────────────────│                    │
 │  Duration: 3 Years     │                      │                    │                    │
 │  Fee: Rs 10,500/yr     │                      │                    │                    │
 │  [Back]                │                      │                    │                    │
```

## 3. Student Service Auth Flow

```
User                  chatbot.js              routes.py           Orchestrator         Connector
 │                        │                      │                    │                    │
 │  "Results"             │                      │                    │                    │
 │───────────────────────>│                      │                    │                    │
 │                        │  POST /api/chat/ask  │                    │                    │
 │                        │─────────────────────>│                    │                    │
 │                        │                      │  process()         │                    │
 │                        │                      │───────────────────>│                    │
 │                        │                      │                    │  detect_service → "results"
 │                        │                      │                    │  service_needs_auth → True
 │                        │                      │                    │                    │
 │                        │  event: auth_form    │                    │                    │
 │                        │<─────────────────────│<───────────────────│                    │
 │  ┌──────────────────┐  │                      │                    │                    │
 │  │ Registration:___ │  │                      │                    │                    │
 │  │ Password:    *** │  │                      │                    │                    │
 │  │ [Sign In]        │  │                      │                    │                    │
 │  └──────────────────┘  │                      │                    │                    │
 │                        │                      │                    │                    │
 │  User fills + submits  │                      │                    │                    │
 │───────────────────────>│                      │                    │                    │
 │                        │  POST "CUS-2023-001  │                    │                    │
 │                        │        ||mypassword" │                    │                    │
 │                        │─────────────────────>│                    │                    │
 │                        │                      │  process()         │                    │
 │                        │                      │───────────────────>│                    │
 │                        │                      │                    │  last_intent=="awaiting_credentials"
 │                        │                      │                    │  parse "||" format  │
 │                        │                      │                    │────────────────────>│
 │                        │                      │                    │  authenticate(reg,  │
 │                        │                      │                    │    password)         │
 │                        │                      │                    │<────────────────────│
 │                        │                      │                    │  success=true        │
 │                        │                      │                    │  session_token saved │
 │                        │                      │                    │  (memory only)       │
 │                        │                      │                    │                    │
 │                        │  event: detail       │                    │                    │
 │                        │<─────────────────────│<───────────────────│                    │
 │  Authentication        │                      │                    │                    │
 │  Successful!           │                      │                    │                    │
 │  Status: Connected     │                      │                    │                    │
 │                        │                      │                    │                    │
 │  "Show my results"     │                      │                    │                    │
 │───────────────────────>│                      │                    │                    │
 │                        │                      │  process()         │                    │
 │                        │                      │───────────────────>│                    │
 │                        │                      │                    │  service_authenticated
 │                        │                      │                    │  _handle_service_query│
 │                        │                      │                    │────────────────────>│
 │                        │                      │                    │  fetch(session_token)│
 │                        │                      │                    │<────────────────────│
 │                        │                      │                    │  {fields, actions}   │
 │                        │  event: detail       │                    │                    │
 │                        │<─────────────────────│<───────────────────│                    │
 │  Sem 1: 82%            │                      │                    │                    │
 │  Sem 2: 78%            │                      │                    │                    │
 │  [Download]            │                      │                    │                    │
```

## 4. Back Navigation with Breadcrumbs

```
User: "Admissions"
  → breadcrumbs: []
  → Show UG/PG/PhD options
  → breadcrumbs: ["Admissions"]

User clicks "UG"
  → breadcrumbs: ["Admissions"]
  → Show BA/B.Sc/BCA options
  → breadcrumbs: ["Admissions", "UG Programmes"]

User clicks "BCA"
  → breadcrumbs: ["Admissions", "UG Programmes"]
  → Show BCA detail card
  → breadcrumbs: ["Admissions", "UG Programmes", "BCA"]

User clicks "← Back"
  → intent_router pops nav path
  → Returns to UG programme list (breadcrumbs: ["Admissions", "UG Programmes"])

User clicks "← Back" again
  → Returns to Admissions options (breadcrumbs: ["Admissions"])

User clicks "← Back" again
  → Returns to Welcome screen (breadcrumbs: [])
```

## 5. Service Auth Cancel Flow

```
User: "Results"
  → auth_form shown with [Sign In] [Cancel]

User clicks "Cancel"
  → Frontend sends "back" as message
  → Orchestrator sees last_intent=="awaiting_credentials" and text=="back"
  → Clears service context
  → Shows WELCOME_OPTIONS
  → User is back at start
```
