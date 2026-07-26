"""
backend/app/request_manager/response_cache.py

Intelligent response cache for structured lookups.

Caches fee structures, eligibility, course duration, admission info,
college info, academic calendar, and other frequently accessed responses.

TTL-based expiry, LRU eviction, auto-invalidation on knowledge sync / reindex.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any

from app.config import settings


class ResponseCache:
    """Thread-safe LRU cache with TTL support for structured responses.

    Key is derived from the request parameters (programme, topic, college, etc.)
    Value is the serializable response dict or string.
    """

    def __init__(
        self,
        max_size: int | None = None,
        default_ttl: int | None = None,
    ) -> None:
        self._max_size = max_size or settings.CACHE_MAX_SIZE
        self._default_ttl = default_ttl or settings.CACHE_DEFAULT_TTL
        self._store: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def _make_key(self, **parts: str | None) -> str:
        """Build a deterministic cache key from named parts."""
        raw = json.dumps({k: v for k, v in sorted(parts.items()) if v is not None}, sort_keys=True)
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, key: str) -> tuple[bool, Any]:
        """Look up a key. Returns (hit: bool, value: Any)."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False, None
            if entry.is_expired:
                del self._store[key]
                return False, None
            # Move to end (most recently used)
            self._store.move_to_end(key)
            return True, entry.value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value with optional TTL (seconds)."""
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = _Entry(value, ttl or self._default_ttl)
            self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)  # LRU

    def invalidate(self, pattern: str | None = None) -> int:
        """Invalidate entries matching a key prefix pattern. Returns count."""
        with self._lock:
            if pattern is None:
                count = len(self._store)
                self._store.clear()
                return count
            keys = [k for k in self._store if k.startswith(pattern)]
            for k in keys:
                del self._store[k]
            return len(keys)

    def invalidate_all(self) -> int:
        return self.invalidate()

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def stats(self) -> dict:
        with self._lock:
            total = len(self._store)
            expired = sum(1 for e in self._store.values() if e.is_expired)
        return {
            "size": total,
            "max_size": self._max_size,
            "expired_entries": expired,
            "default_ttl_sec": self._default_ttl,
        }

    # Convenience methods for structured lookups

    def get_structured(self, programme: str, topic: str) -> tuple[bool, Any]:
        key = self._make_key(programme=programme, topic=topic)
        return self.get(key)

    def set_structured(self, programme: str, topic: str, value: Any, ttl: int | None = None) -> None:
        key = self._make_key(programme=programme, topic=topic)
        self.set(key, value, ttl)

    def get_college(self, college_id: str, topic: str | None = None) -> tuple[bool, Any]:
        key = self._make_key(college_id=college_id, topic=topic)
        return self.get(key)

    def set_college(self, college_id: str, topic: str | None, value: Any, ttl: int | None = None) -> None:
        key = self._make_key(college_id=college_id, topic=topic)
        self.set(key, value, ttl)

    def get_generic(self, **parts: str | None) -> tuple[bool, Any]:
        key = self._make_key(**parts)
        return self.get(key)

    def set_generic(self, value: Any, ttl: int | None = None, **parts: str | None) -> None:
        key = self._make_key(**parts)
        self.set(key, value, ttl)


class _Entry:
    __slots__ = ("expires_at", "value")

    def __init__(self, value: Any, ttl: int) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at


response_cache = ResponseCache()
