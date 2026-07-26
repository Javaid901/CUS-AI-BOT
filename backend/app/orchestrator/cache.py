"""
backend/app/orchestrator/cache.py

Multi-namespace TTL cache for the orchestration pipeline.

Namespaces:
  - intent:  intent classification results
  - entity:  entity extraction results
  - lookup:  structured data lookups
  - rag:     retrieval results
  - llm:     generated LLM responses (short TTL)

Thread-safe via asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

_MAX_ENTRIES_PER_NS = 256
_DEFAULT_TTL: dict[str, float] = {
    "intent": 30.0,
    "entity": 30.0,
    "lookup": 300.0,
    "rag": 60.0,
    "llm": 10.0,
}


class _CacheEntry:
    __slots__ = ("expires_at", "value")

    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.expires_at = time.time() + ttl


class TtlCache:
    def __init__(self) -> None:
        self._stores: dict[str, dict[str, _CacheEntry]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, namespace: str) -> asyncio.Lock:
        if namespace not in self._locks:
            self._locks[namespace] = asyncio.Lock()
        return self._locks[namespace]

    async def get(self, namespace: str, key: str) -> Any | None:
        async with self._lock(namespace):
            store = self._stores.get(namespace)
            if store is None:
                return None
            entry = store.get(key)
            if entry is None:
                return None
            if time.time() > entry.expires_at:
                del store[key]
                return None
            return entry.value

    async def set(
        self, namespace: str, key: str, value: Any, ttl: float | None = None
    ) -> None:
        if ttl is None:
            ttl = _DEFAULT_TTL.get(namespace, 60.0)
        async with self._lock(namespace):
            store = self._stores.setdefault(namespace, {})
            store[key] = _CacheEntry(value, ttl)
            # Evict oldest if over capacity
            if len(store) > _MAX_ENTRIES_PER_NS:
                oldest_key, _oldest_ts = min(
                    store.items(), key=lambda kv: kv[1].expires_at
                )
                if oldest_key != key:
                    del store[oldest_key]

    async def delete(self, namespace: str, key: str) -> None:
        async with self._lock(namespace):
            store = self._stores.get(namespace)
            if store:
                store.pop(key, None)

    async def clear_namespace(self, namespace: str) -> None:
        async with self._lock(namespace):
            self._stores.pop(namespace, None)

    async def clear_all(self) -> None:
        self._stores.clear()

    async def hit_rate(self, namespace: str) -> float | None:
        """Return the hit rate for a namespace, or None if no data."""
        # Tracked via hit/miss counters
        hits = await self._get_counter(namespace, "_hits")
        misses = await self._get_counter(namespace, "_misses")
        total = hits + misses
        if total == 0:
            return None
        return hits / total

    async def _get_counter(self, namespace: str, key: str) -> int:
        # Simple counter stored as cache entry
        val = await self.get(namespace, key)
        return val if isinstance(val, int) else 0

    async def _incr_counter(self, namespace: str, key: str) -> None:
        val = (await self._get_counter(namespace, key)) + 1
        await self.set(namespace, key, val, ttl=3600.0)


# Singleton
_cache: TtlCache | None = None


def get_cache() -> TtlCache:
    global _cache
    if _cache is None:
        _cache = TtlCache()
    return _cache


# ---------------------------------------------------------------------------
# Convenience async wrapper: cache-only-if-cached, or compute-and-store
# ---------------------------------------------------------------------------


async def cached_or_compute(
    namespace: str,
    key: str,
    compute: Callable,
    ttl: float | None = None,
) -> Any:
    """Return cached value if available, otherwise compute, store, and return."""
    cache = get_cache()
    cached = await cache.get(namespace, key)
    if cached is not None:
        return cached
    value = await compute() if asyncio.iscoroutinefunction(compute) else compute()
    await cache.set(namespace, key, value, ttl=ttl)
    return value
