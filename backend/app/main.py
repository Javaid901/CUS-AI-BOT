"""
backend/app/main.py

FastAPI application entrypoint.

Wires routers, CORS, structured error handlers, and startup tasks
(admin seeding + table creation).

Endpoint contract note:
  The existing frontend calls /api/documents, /api/chat/ask, etc.
  The original task spec lists /api/admin/documents, /api/admin/upload, ...
  Both are mounted (see alias wiring) so the frontend works unchanged and the
  spec-style paths also resolve.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.admin.routes import router as admin_router
from app.analytics.routes import router as analytics_router
from app.auth.routes import router as auth_router
from app.authority.routes import public_router as authority_lookup_router
from app.authority.routes import router as authority_admin_router
from app.chat.routes import router as chat_router
from app.college.routes import router as college_router
from app.config import settings
from app.database import create_all
from app.public.routes import router as public_router
from app.utils.errors import register_exception_handlers
from app.utils.logging import log

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="RAG-based university AI assistant for Cluster University Srinagar.",
)

# ----- CORS -----
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=settings.cors_origin_list != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- Structured errors -----
register_exception_handlers(app)

# ----- Routers (frontend contract paths) -----
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(college_router)
app.include_router(analytics_router)
app.include_router(public_router)
app.include_router(authority_admin_router)
app.include_router(authority_lookup_router)


# ----- Spec-style aliases (for compatibility with the task spec) -----
# The frontend uses /api/documents etc.; these add_api_route calls expose
# the same handlers under the original spec paths.
from app.admin.routes import (
    delete_document,
    list_documents,
    reindex_document,
    upload_document,
)

app.add_api_route(
    f"{settings.API_PREFIX}/admin/documents",
    list_documents,
    methods=["GET"],
    include_in_schema=False,
)
app.add_api_route(
    f"{settings.API_PREFIX}/admin/upload",
    upload_document,
    methods=["POST"],
    include_in_schema=False,
)
app.add_api_route(
    f"{settings.API_PREFIX}/admin/document/{{doc_id}}",
    delete_document,
    methods=["DELETE"],
    include_in_schema=False,
)
app.add_api_route(
    f"{settings.API_PREFIX}/admin/reindex/{{doc_id}}",
    reindex_document,
    methods=["POST"],
    include_in_schema=False,
)


# ----- Startup -----
@app.on_event("startup")
def on_startup() -> None:
    log.info("Starting %s (env=%s)", settings.APP_NAME, settings.ENVIRONMENT)
    create_all()
    _seed_admin()
    if settings.DEMO_MODE:
        # Demo mode: seed full synthetic dataset (students + all service data)
        _seed_demo_service_data()
    else:
        # Non-demo: seed only the minimal 5 test students
        _seed_students()
    _warmup_models()
    _start_analytics_scheduler()
    _backfill_analytics()
    _start_background_worker()
    _warmup_authority_cache()
    log.info("Analytics module initialized")


def _start_analytics_scheduler() -> None:
    """Start the analytics background scheduler."""
    from app.analytics.scheduler import start
    try:
        asyncio.get_running_loop()
        start()
        log.info("Analytics background scheduler started")
    except RuntimeError:
        try:
            asyncio.run(start())
            log.info("Analytics background scheduler started")
        except RuntimeError:
            log.debug("Analytics scheduler deferred (no event loop)")


def _backfill_analytics() -> None:
    """Backfill analytics from existing conversation data if empty."""
    try:
        from app.analytics.service import ensure_analytics_data
        count = ensure_analytics_data()
        if count:
            log.info("Analytics backfilled: %d events created", count)
    except Exception as exc:
        log.warning("Analytics backfill skipped: %s", exc)


def _start_background_worker() -> None:
    """Start the background ingestion worker."""
    from app.ingest.worker import worker
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            loop.create_task(worker.start())
        else:
            asyncio.run(worker.start())
        log.info("Background ingestion worker started")
    except Exception as exc:
        log.warning("Background worker start deferred: %s", exc)


def _warmup_models() -> None:
    """Pre-load Ollama models so first user request is fast."""
    import threading

    from app.ingest.embed import _ollama_embed
    from app.ingest.generator import _build_payload as _build_llm_payload

    def _warmup_llm():
        try:
            payload = _build_llm_payload("warmup", "CUS is a university")
            import httpx
            with httpx.Client(timeout=300.0) as c:
                resp = c.post(
                    f"{settings.OLLAMA_BASE_URL}/api/generate",
                    json={**payload, "stream": False, "keep_alive": f"{settings.OLLAMA_KEEP_ALIVE}s", "options": {**payload.get("options", {}), "num_predict": 1}},
                )
                if resp.status_code == 200:
                    log.info("LLM model '%s' warmed up", settings.LLM_MODEL)
                else:
                    log.warning("LLM warmup returned HTTP %s", resp.status_code)
        except Exception as exc:
            log.warning("LLM warmup failed (non-fatal): %s", exc)

    def _warmup_embed():
        try:
            _ollama_embed(["warmup"])
            log.info("Embed model '%s' warmed up", settings.EMBED_MODEL)
        except Exception as exc:
            log.warning("Embed warmup failed (non-fatal): %s", exc)

    # Warm up in parallel threads — this can take time
    t1 = threading.Thread(target=_warmup_embed, daemon=True)
    t2 = threading.Thread(target=_warmup_llm, daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=120)
    t2.join(timeout=120)


def _seed_admin() -> None:
    import uuid

    from sqlalchemy.orm import Session

    from app.auth.security import hash_password
    from app.database import SessionLocal
    from app.models import User

    db: Session = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == settings.SEED_ADMIN_USERNAME).first()
        if existing:
            return
        admin = User(
            id=uuid.uuid4(),
            username=settings.SEED_ADMIN_USERNAME,
            email=settings.SEED_ADMIN_EMAIL,
            hashed_password=hash_password(settings.SEED_ADMIN_PASSWORD),
            role="superadmin",
            is_active=True,
        )
        db.add(admin)
        db.commit()
        log.info("Seeded superadmin user '%s'", settings.SEED_ADMIN_USERNAME)
    finally:
        db.close()


def _seed_students() -> None:
    """Seed test student accounts for development/demo."""
    import uuid

    from sqlalchemy.orm import Session

    from app.auth.security import hash_password
    from app.database import SessionLocal
    from app.models import Student

    test_students = [
        {"reg_no": "CUS-2023-0001", "roll_no": "23001", "name": "Aarav Sharma", "father_name": "Rajesh Sharma", "mother_name": "Sunita Sharma", "dob": "15-Apr-2005", "gender": "Male", "category": "General", "college": "Sri Pratap College, Srinagar", "programme": "bca", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "status": "active"},
        {"reg_no": "CUS-2023-0002", "roll_no": "23002", "name": "Priya Singh", "father_name": "Vikram Singh", "mother_name": "Anita Singh", "dob": "22-Aug-2004", "gender": "Female", "category": "OBC", "college": "Amar Singh College, Srinagar", "programme": "bba", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "status": "active"},
        {"reg_no": "CUS-2022-0003", "roll_no": "22003", "name": "Rohit Kumar", "father_name": "Suresh Kumar", "mother_name": "Geeta Devi", "dob": "10-Jan-2003", "gender": "Male", "category": "SC", "college": "Government Degree College, Bemina", "programme": "bsc", "semester": 6, "admission_year": 2022, "batch": "2022-2025", "status": "active"},
        {"reg_no": "CUS-2024-0004", "roll_no": "24004", "name": "Anjali Verma", "father_name": "Ravi Verma", "mother_name": "Sita Verma", "dob": "05-Jun-2006", "gender": "Female", "category": "General", "college": "Women's College, Sopore", "programme": "ba", "semester": 2, "admission_year": 2024, "batch": "2024-2027", "status": "active"},
        {"reg_no": "CUS-2023-0005", "roll_no": "23005", "name": "Vikram Patel", "father_name": "Mohan Patel", "mother_name": "Kavita Patel", "dob": "18-Nov-2004", "gender": "Male", "category": "General", "college": "Sri Pratap College, Srinagar", "programme": "bcom", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "status": "active"},
    ]

    db: Session = SessionLocal()
    try:
        existing_count = db.query(Student).count()
        if existing_count > 0:
            log.info("Students table already has %d records — skipping seed", existing_count)
            return
        for s in test_students:
            student = Student(
                id=uuid.uuid4(),
                reg_no=s["reg_no"],
                roll_no=s.get("roll_no"),
                name=s["name"],
                father_name=s.get("father_name"),
                mother_name=s.get("mother_name"),
                dob=s.get("dob"),
                gender=s.get("gender"),
                category=s.get("category"),
                college=s.get("college"),
                programme=s["programme"],
                current_semester=s["semester"],
                admission_year=s["admission_year"],
                batch=s.get("batch"),
                status=s.get("status", "active"),
                hashed_password=hash_password("student123"),
                is_active=True,
            )
            db.add(student)
        db.commit()
        log.info("Seeded %d test students", len(test_students))
    finally:
        db.close()


def _seed_demo_service_data() -> None:
    """Seed demo service data (results, attendance, fees, etc.) if tables are empty."""
    from sqlalchemy.orm import Session

    from app.database import SessionLocal
    from app.seeders.demo_data import seed_demo_data
    db: Session = SessionLocal()
    try:
        count = seed_demo_data(db, count=settings.DEMO_STUDENT_COUNT)
        if count:
            log.info("Demo data seeded for %d students", count)
    except Exception as exc:
        log.warning("Demo data seeding skipped: %s", exc)
    finally:
        db.close()


def _warmup_authority_cache() -> None:
    """Load authority cache on startup so lookups are instant."""
    from app.authority.service import authority_service
    from app.database import SessionLocal
    try:
        db = SessionLocal()
        try:
            authority_service.load_cache(db)
            log.info("Authority cache loaded (%d offices)", len(authority_service.list_active()))
        finally:
            db.close()
    except Exception as exc:
        log.warning("Authority cache warmup skipped (table may not exist yet): %s", exc)


# ----- Frontend static files (serves the site on the configured PORT) -----
_frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
def admin_redirect():
    return RedirectResponse(url="/pages/admin.html")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/pages/index.html")


if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


# ----- API health checked from the public router -----
# GET /api/health is defined in app.public.routes
