# API Documentation

## Authentication

All endpoints except `/api/auth/*` and `/api/public/*` require a Bearer JWT token.

### POST /api/auth/register
Create a new user account.

**Request:** `application/json`
```json
{"username": "web_abc123", "email": "user@example.com", "password": "g_xyz789"}
```

**Response:** `200 OK`
```json
{"access_token": "...", "refresh_token": "...", "token_type": "bearer", "user": {"id": "...", "username": "..."}}
```

### POST /api/auth/login
Authenticate with existing credentials.

**Request:** `application/x-www-form-urlencoded`
```
username=web_abc123&password=g_xyz789
```

**Response:** `200 OK`
```json
{"access_token": "...", "token_type": "bearer", "user": {"id": "...", "username": "...", "role": "student"}}
```

---

## Chat

### POST /api/chat/ask
Send a message and receive a Server-Sent Events stream.

**Auth:** Bearer JWT (student or admin)
**Rate Limit:** 20 requests/minute per IP

**Request:** `application/json`
```json
{
  "message": "What is the fee for BCA?",
  "chat_id": "550e8400-e29b-41d4-a716-446655440000",
  "stream": true
}
```

- `chat_id`: Omit or `null` for new conversations. The server returns the real ID in the `done` event.
- `stream`: Always `true` (SSE is the only response mode).

**Response:** `text/event-stream`

#### Event: Text Token
```
data: The fee for BCA is approximately Rs 10,500 per year.
```

#### Event: Navigation Options
```
event: options
data: {"type":"options","title":"Admissions","message":"Select a programme level.","options":[{"id":"ug","label":"Undergraduate"},{"id":"pg","label":"Postgraduate"},{"id":"phd","label":"PhD"}]}
```

#### Event: Detail Card
```
event: detail
data: {"type":"detail","title":"BCA","fields":[{"label":"Duration","value":"3 Years"},{"label":"Fee","value":"Rs 10,500/year"}],"actions":[{"id":"fee","label":"View Fee Structure"}]}
```

#### Event: Auth Form (Student Login)
```
event: auth_form
data: {"type":"auth_form","service":"results","title":"Student Login Required","message":"To access Results, please sign in.","fields":[{"id":"registration_number","label":"Registration Number","type":"text"},{"id":"password","label":"Password","type":"password"}],"submit_label":"Sign In"}
```

The user fills in the fields and clicks Submit. The frontend sends credentials as `registration_number||password` in a new chat message.

#### Event: Done
```
event: done
data: {"chat_id":"550e8400-e29b-41d4-a716-446655440000","cited_chunks":[{"document_id":"...","document_title":"BCA Brochure 2025","page_number":3,"score":0.92}]}
```

#### Event: Error
```
event: error
data: {"message":"The student portal is currently unavailable."}
```

---

## Admin

### GET /api/health
System health check. No auth required.

### GET /api/documents
List all ingested documents.

**Auth:** admin / superadmin

### POST /api/documents/upload
Upload and ingest a document.

**Auth:** admin / superadmin
**Request:** `multipart/form-data`
| Field | Type |
|-------|------|
| `file` | PDF/DOCX/TXT/MD (max 25 MB) |

### DELETE /api/documents/{id}
Delete a document and its vectors.

### POST /api/documents/{id}/reindex
Re-process a stored document.

### GET /api/admin/kb-health
Full system health check (DB, Ollama, ChromaDB, models).

### GET /api/admin/kb-stats
Knowledge base statistics.

### POST /api/admin/sync-website
Sync documents from the university website.

### GET /api/admin/logs
Recent audit log entries.

### GET /api/public/suggested-questions
Returns 8 suggested questions for the chatbot widget. No auth required.
