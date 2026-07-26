"""Knowledge Sync — admin-only document acquisition from approved sources."""

from app.knowledge_sync.dedup import SyncManifest
from app.knowledge_sync.engine import SyncEngine
from app.knowledge_sync.fetcher import Fetcher

__all__ = ["Fetcher", "SyncEngine", "SyncManifest"]
