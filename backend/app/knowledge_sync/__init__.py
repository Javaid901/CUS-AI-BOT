"""Knowledge Sync — admin-only document acquisition from approved sources.

Also hosts the Enterprise Website Knowledge Synchronization Engine:
  - web_crawler  — bounded same-domain crawler (robots.txt aware)
  - web_extractor— semantic HTML extraction
  - web_classifier — page categorization
  - web_engine   — incremental sync, versioning, archiving, dedup, RAG indexing
  - web_scheduler — dashboard-controlled scheduled runs
"""

from app.knowledge_sync.dedup import SyncManifest
from app.knowledge_sync.engine import SyncEngine
from app.knowledge_sync.fetcher import Fetcher
from app.knowledge_sync.web_classifier import CATEGORIES, classify_page
from app.knowledge_sync.web_crawler import WebsiteCrawler, normalize_url
from app.knowledge_sync.web_engine import WebsiteSyncEngine, load_state, save_state
from app.knowledge_sync.web_extractor import SemanticExtractor, extract_html

__all__ = [
    "CATEGORIES",
    "Fetcher",
    "SemanticExtractor",
    "SyncEngine",
    "SyncManifest",
    "WebsiteCrawler",
    "WebsiteSyncEngine",
    "classify_page",
    "extract_html",
    "load_state",
    "normalize_url",
    "save_state",
]
