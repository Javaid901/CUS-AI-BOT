# CUS AI Assistant — Backend

RAG-based university AI assistant for **Cluster University Srinagar**. Built with FastAPI, Ollama, ChromaDB, PostgreSQL, and sentence embeddings.

## Architecture

```
User question → embedding → Chroma similarity search → Ollama (llama3) → grounded answer + citations
```

```
Admin upload → text extraction → chunking → embedding → Chroma + Postgres storage
```

No answers are hardcoded. Every response is generated from the university's own documents.

## Prerequisites

- Python 3.12+
- [Ollama](https://ollama.com) with models:
  ```bash
  ollama pull llama3
  ollama pull nomic-embed-text
  ollama serve
  ```
- PostgreSQL (optional — SQLite works locally)

## Quick Start

```bash
cd backend

# (Optional) create virtualenv
python -m venv venv
venv\Scripts\activate   # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Run
python run.py
```

Open `http://localhost:8001/docs` for the interactive API docs.

## Environment

Copy `.env.example` to `.env` and adjust:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./cus_ai.db` | Set to `postgresql+psycopg://user:pass@host/db` for prod |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server |
| `LLM_MODEL` | `llama3` | Model for answer generation |
| `EMBED_MODEL` | `nomic-embed-text` | Model for embeddings |
| `SECRET_KEY` | (change in prod) | JWT signing key |

## API Endpoints

| Endpoint | Auth | Description |
|----------|------|-------------|
| `POST /api/auth/register` | — | Register a new user (JSON: username, email, password, role) |
| `POST /api/auth/login` | — | Login (form: username, password) → `access_token` |
| `POST /api/chat/ask` | Bearer | Send question → SSE stream of answer + `event: done` with citations |
| `GET /api/public/suggested-questions` | — | Returns 8 default sample questions |
| `GET /api/health` | — | Service + Ollama status |
| `GET /api/documents` | Admin | List ingested documents |
| `POST /api/documents/upload` | Admin | Upload a file (PDF, DOCX, TXT, MD) |
| `DELETE /api/documents/{id}` | Admin | Delete document + vectors |
| `POST /api/documents/{id}/reindex` | Admin | Re-embed and re-index |
| `GET /api/admin/logs` | Admin | Audit log |

Also available at the original task-spec paths (`/api/admin/documents`, `/api/admin/upload`, `/api/admin/document/{id}`, `/api/admin/reindex/{id}`).

## Frontend

The frontend lives in `../frontend/`. Serve it alongside the backend:

```bash
cd ../frontend
python -m http.server 8200
# Open http://localhost:8200/pages/index.html
```

## Docker

```bash
cd backend
docker compose up --build
```

This starts PostgreSQL, Ollama, and the backend. Pull models after Ollama starts:

```bash
docker exec cus_ollama ollama pull llama3
docker exec cus_ollama ollama pull nomic-embed-text
```

## Default Admin

Username: `admin`  
Password: `admin123`
