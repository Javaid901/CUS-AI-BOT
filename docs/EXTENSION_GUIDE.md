# Extension Guide — Adding University Services

## Overview

The CUS AI Assistant is designed so that adding a new university service (e.g. "Exam Schedule", "Library Access", "Fee Payment") requires NO changes to chatbot logic, routing, or frontend code.

You only need to:

1. **Create a connector** — implement the `ServiceConnector` interface
2. **Register it** — add to `registry.py`

That is all. The Orchestrator discovers connectors automatically, handles auth flow, and formats responses for the frontend.

---

## Adding a New Service Connector

### Step 1: Create the connector file

```python
# backend/app/services/exam_schedule.py

from app.services.base import ServiceConnector, ServiceResult


class ExamScheduleConnector(ServiceConnector):
    name = "exam_schedule"
    display_name = "Exam Schedule"
    description = "View examination dates and schedule"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        """
        Authenticate against the university student portal.

        Real implementation:
          POST https://university-portal.example.com/api/login
          Body: {"registration_number": reg_no, "password": password}
          Headers: Content-Type application/json

        NEVER store reg_no or password.
        Return a temporary session token on success.
        """
        # TODO: Replace with actual university API call
        if not reg_no or not password:
            return ServiceResult(
                success=False,
                error="Registration number and password are required.",
            )
        return ServiceResult(
            success=True,
            data={
                "session_token": f"session_{reg_no}_{int(time.time())}",
                "expiry": None,  # or timestamp
            },
        )

    async def fetch(
        self,
        session_token: str | None,
        params: dict,
    ) -> ServiceResult:
        """
        Fetch exam schedule using active session.

        Real implementation:
          GET https://university-portal.example.com/api/exams/schedule
          Cookie: session=<session_token>
          → Parse response into fields list

        The data dict should match the frontend detail card format:
          {
            "title": "Exam Schedule",
            "message": "...",
            "fields": [{"label": "...", "value": "..."}, ...],
            "actions": [{"id": "...", "label": "..."}, ...],
          }
        """
        # TODO: Replace with actual university API call
        return ServiceResult(
            success=True,
            data={
                "title": "Examination Schedule",
                "message": "Your upcoming examinations.",
                "fields": [
                    {"label": "Programme", "value": "[Student Programme]"},
                    {"label": "Semester", "value": "[Current Semester]"},
                    {"label": "Next Exam", "value": "[Subject] on [Date]"},
                    {"label": "View Full Schedule", "value": "Available online"},
                ],
                "actions": [
                    {"id": "full_schedule", "label": "View Full Schedule"},
                    {"id": "download_schedule", "label": "Download PDF"},
                ],
            },
        )
```

### Step 2: Register the connector

```python
# backend/app/services/registry.py

# 1. Import at the top
from app.services.exam_schedule import ExamScheduleConnector

# 2. Add to SERVICE_NAMES
SERVICE_NAMES: dict[str, str] = {
    # ... existing services ...
    "exam_schedule": "Exam Schedule",
}

# 3. Register at the bottom
_register(ExamScheduleConnector())
```

### Step 3: Add intent detection keyword (optional)

```python
# backend/app/orchestrator/engine.py

_SERVICE_KEYWORDS: dict[str, str] = {
    # ... existing keywords ...
    "exam schedule": "exam_schedule",
    "exam schedule": "exam_schedule",
    "exam timetable": "exam_schedule",
    "exam date": "exam_schedule",
}
```

That is everything. The service now works end-to-end:

```
User: "exam schedule"
  → Orchestrator detects service intent "exam_schedule"
  → Auth form shown (if not authenticated)
  → User logs in → connector.authenticate()
  → Service data displayed via connector.fetch()
```

---

## Replacing a Placeholder with a Real Connector

### Current placeholder flow

The `ResultsConnector` in `placeholders.py` returns hardcoded placeholder data.
The `authenticate()` method returns a fake session token for any credentials.

### Step-by-step replacement

1. **Create a new file** (e.g. `results_portal.py`)

2. **Copy the placeholder code structure** from `placeholders.py`

3. **Implement `authenticate()`** with real portal integration:

```python
import httpx

class RealResultsConnector(ServiceConnector):
    name = "results"
    display_name = "Results"
    description = "Semester exam results from the university portal"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://portal.cusrinagar.edu.in/api/login",
                    json={"registration_number": reg_no, "password": password},
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return ServiceResult(
                        success=True,
                        data={"session_token": data["session_id"], "expiry": data.get("expires_at")},
                    )
                elif resp.status_code == 401:
                    return ServiceResult(
                        success=False,
                        error="Your registration number or password appears to be incorrect.",
                    )
                else:
                    return ServiceResult(
                        success=False,
                        error="The student portal is currently unavailable. Please try again later.",
                    )
        except httpx.RequestError:
            return ServiceResult(
                success=False,
                error="Could not connect to the student portal. Please check your connection.",
            )

    async def fetch(self, session_token, params):
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://portal.cusrinagar.edu.in/api/results",
                cookies={"session": session_token},
                params=params,
                timeout=15,
            )
            # Parse response into fields/actions format
            ...
```

4. **Update registry.py** to import and register the real connector instead of the placeholder.

5. **Test** — the chatbot should transparently use the real data source with no other code changes.

---

## Integration Requirements for University Portal

To integrate a real university student portal, the following is needed:

### Minimum API requirements

| Capability | Endpoint | Method |
|------------|----------|--------|
| Authentication | `/api/login` | POST |
| Results | `/api/results` | GET |
| Admit Card | `/api/admit-card` | GET |
| Exam Form | `/api/exam-form` | GET/POST |
| Attendance | `/api/attendance` | GET |
| Fee Status | `/api/fee` | GET |
| Registration | `/api/registration` | GET/POST |
| Profile | `/api/profile` | GET |

### Security requirements

- All endpoints MUST be served over HTTPS
- Authentication MUST use industry-standard practices (not plaintext password transmission)
- Session tokens SHOULD have a configurable TTL
- Rate limiting SHOULD be implemented on the portal side

### If no API exists

If the university portal does not expose APIs, the following approaches are possible (ordered by preference):

1. **Request official API access** from the university IT department
2. **Use a headless browser** (Playwright/Selenium) as a last resort — requires separate infrastructure and careful credential handling
3. **Implement manual data entry** through the admin panel — the chatbot can direct staff to update information manually

---

## Adding New Navigation Items

To add a new entry to the navigation tree:

```python
# backend/app/chat/intent_router.py

# 1. Add broad keyword
_BROAD_KEYWORDS["library"] = "library"

# 2. Add topic entry
_TOPICS["library"] = {
    "title": "Library Services",
    "message": "Select a library service.",
    "options": [
        {"id": "catalog", "label": "Online Catalog"},
        {"id": "timings", "label": "Library Timings"},
        {"id": "membership", "label": "Library Membership"},
        {"id": "digital", "label": "Digital Resources"},
    ],
}

# 3. Add detail entries for sub-options
# (known_ids is auto-computed from _TOPICS)

# 4. Add handler in get_broad_response
if cat == "library":
    return {
        "type": "options",
        "title": "Library Services",
        "message": "Select a service.",
        "options": _TOPICS["library"]["options"],
    }
```

The navigation item is now available from the chatbot. No frontend changes needed — the welcome chips automatically include it if added to `WELCOME_OPTIONS`.

---

## Configuration Reference

### `.env` file

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_MODEL` | `llama3.2:1b` | Ollama model for generation |
| `EMBED_MODEL` | `nomic-embed-text` | Ollama model for embeddings |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `SECRET_KEY` | `change-me...` | JWT signing key (change in production) |
| `DATABASE_URL` | `sqlite:///./cus_ai.db` | SQLAlchemy database URL |
| `CORS_ORIGINS` | `*` | Allowed CORS origins |
| `RATE_LIMIT_PER_MINUTE` | `20` | Max requests per minute per IP |
| `TOP_K` | `3` | Number of chunks retrieved from vector DB |
| `SCORE_THRESHOLD` | `0.0` | Minimum relevance score (0.0 = no filter) |
| `CHUNK_SIZE` | `900` | Character size of document chunks |
| `CHUNK_OVERLAP` | `150` | Overlap between consecutive chunks |

---

## Production Deployment Checklist

- [ ] Change `SECRET_KEY` to a long random value
- [ ] Set `ENVIRONMENT=production`
- [ ] Configure `CORS_ORIGINS` to the frontend domain
- [ ] Use PostgreSQL instead of SQLite (set `DATABASE_URL`)
- [ ] Set up HTTPS reverse proxy (nginx / Caddy)
- [ ] Configure rate limiting for production load
- [ ] Set up database backups
- [ ] Configure ChromaDB persistence path
- [ ] Set up monitoring (health check endpoint available at `/api/health`)
- [ ] Replace placeholder connectors with real university portal integrations
- [ ] Set up Ollama with a production-grade model (llama3 7B+ for better reasoning)
- [ ] Configure log rotation
