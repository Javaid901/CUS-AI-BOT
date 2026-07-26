"""
backend/app/config.py

Central application configuration via environment variables.
Uses pydantic-settings so values can be supplied by a .env file or the OS env.

Database:
  - Default metadata store is SQLite so the service runs with zero external setup.
  - Set DATABASE_URL to a PostgreSQL connection string to use Postgres (e.g. via docker-compose).

LLM / Embeddings:
  - Both run through Ollama (on-premise). The model names are configurable.
"""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ----- Service -----
    APP_NAME: str = "CUS AI Assistant"
    ENVIRONMENT: str = "development"
    API_PREFIX: str = "/api"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False

    # ----- Database -----
    # SQLite by default so the app runs immediately; override with Postgres in prod.
    DATABASE_URL: str = "sqlite:///./cus_ai.db"
    DB_ECHO: bool = False

    # ----- Security / JWT -----
    SECRET_KEY: str = "change-me-in-production-please-use-a-long-random-string"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    BCRYPT_ROUNDS: int = 12

    # ----- CORS -----
    # Comma-separated allowed origins. "*" allows all (dev convenience only).
    CORS_ORIGINS: str = "*"

    # ----- Ollama / LLM -----
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    # llama3.2:1b is ~8× faster than llama3 7B on CPU with good quality for Q&A.
    # Switch back to llama3 if you need deeper reasoning: add LLM_MODEL=llama3 to .env
    LLM_MODEL: str = "llama3.2:1b"
    EMBED_MODEL: str = "nomic-embed-text"
    LLM_TEMPERATURE: float = 0.1
    LLM_TOP_P: float = 0.9
    LLM_MAX_TOKENS: int = 256
    # How long to keep models loaded in Ollama memory after last use (seconds).
    OLLAMA_KEEP_ALIVE: int = 600  # 10 minutes

    # ----- Vector store (ChromaDB) -----
    CHROMA_PERSIST_DIR: str = "./chroma_store"

    # ----- Retrieval -----
    CHUNK_SIZE: int = 900
    CHUNK_OVERLAP: int = 150
    TOP_K: int = 3  # Final top-K chunks sent to LLM
    TOP_K_EXPAND: int = 20  # Candidate pool before reranking
    SCORE_THRESHOLD: float = 0.0  # Chroma distance threshold (lower = more similar). 0 = keep all.
    SCORE_THRESHOLD_STRICT: float = 0.65  # Minimum similarity score (0-1) to consider evidence strong
    ENABLE_HYBRID_RETRIEVAL: bool = True  # Combine embedding + keyword search
    ENABLE_QUERY_REWRITE: bool = True  # Rewrite short/vague queries for better retrieval
    ENABLE_RERANKING: bool = True  # Rerank candidates before LLM
    RERANK_K: int = 6  # Keep top K after reranking before context compression
    CONTEXT_COMPRESSION: bool = True  # Group chunks by source document before LLM
    ENABLE_ANSWER_VERIFICATION: bool = True  # Refuse if evidence is weak
    MAX_QUERY_LENGTH: int = 300  # Truncate overly long queries
    BM25_INDEX_REFRESH_INTERVAL: int = 300  # Seconds between BM25 index rebuilds

    # ----- File upload -----
    MAX_UPLOAD_MB: int = 25
    ALLOWED_EXTENSIONS: str = "pdf,docx,txt,md"

    # ----- Rate limiting (chat) — replaced by Token Bucket -----
    RATE_LIMIT_PER_MINUTE: int = 20  # kept for backward compat; actual limiter is token bucket below

    # ----- Token Bucket (replaces sliding-window limiter) -----
    TOKEN_BUCKET_SIZE: int = 100       # max tokens per user
    TOKEN_REFILL_RATE: float = 2.0     # tokens per second

    # ----- Request Queue -----
    MAX_QUEUE_SIZE: int = 200          # maximum queued requests
    MAX_QUEUE_WAIT: float = 30.0       # seconds before a queued request times out
    MAX_SEMAPHORE_WAIT: float = 10.0   # seconds to wait for a service semaphore

    # ----- Per-Service Concurrency -----
    MAX_CONCURRENT_PLANNER: int = 1000   # effectively unlimited
    MAX_CONCURRENT_STRUCTURED: int = 1000
    MAX_CONCURRENT_POSTGRES: int = 30
    MAX_CONCURRENT_CHROMA: int = 20
    MAX_CONCURRENT_EMBEDDING: int = 6
    MAX_CONCURRENT_LLM: int = 2

    # ----- Response Cache -----
    CACHE_DEFAULT_TTL: int = 300     # seconds (5 min)
    CACHE_MAX_SIZE: int = 1000       # entries

    # ----- Worker Pool -----
    WORKER_MIN: int = 1
    WORKER_MAX: int = 6

    # ----- Backpressure -----
    BACKPRESSURE_SLOWDOWN_PCT: float = 80.0
    BACKPRESSURE_QUEUE_PCT: float = 90.0

    # ----- Request Timeouts (seconds) -----
    TIMEOUT_PLANNER: float = 2.0
    TIMEOUT_STRUCTURED: float = 3.0
    TIMEOUT_RAG: float = 15.0
    TIMEOUT_LLM: float = 60.0

    # ----- Knowledge Sync (admin-only document acquisition) -----
    # Comma-separated approved domains for Knowledge Sync downloads.
    KNOWLEDGE_SYNC_DOMAINS: str = "cusrinagar.edu.in"
    # Directory to store downloaded files before ingestion.
    KNOWLEDGE_SYNC_DIR: str = "./sync_downloads"
    # Path to the sync manifest JSON file.
    SYNC_MANIFEST_PATH: str = "./sync_downloads/.sync_manifest.json"
    # When True, downloaded files require admin approval before ingestion.
    KNOWLEDGE_SYNC_REVIEW_MODE: bool = False
    # Auto-sync interval in hours (0 = disabled).
    KNOWLEDGE_SYNC_SCHEDULE_HOURS: int = 0
    # Max concurrent downloads.
    KNOWLEDGE_SYNC_MAX_CONCURRENT: int = 5

    # ----- Demo Mode -----
    # When enabled, seeds demo student data and synthetic service records on startup.
    DEMO_MODE: bool = True
    # Number of demo students to seed (when DEMO_MODE is enabled and tables are empty).
    DEMO_STUDENT_COUNT: int = 25

    # ----- Seeded admin (created on startup if not present) -----
    SEED_ADMIN_USERNAME: str = "admin"
    SEED_ADMIN_PASSWORD: str = "admin123"
    SEED_ADMIN_EMAIL: str = "admin@cus.ac.in"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def allowed_extensions_list(self) -> list[str]:
        return [e.strip().lower() for e in self.ALLOWED_EXTENSIONS.split(",") if e.strip()]


settings = Settings()
