"""
backend/app/request_manager/service_semaphores.py

Per-service concurrency semaphores.

Protects expensive subsystems (PostgreSQL, ChromaDB, Embedding, Ollama/LLM)
from overload by limiting concurrent access.  Uses asyncio.Semaphore so that
awaiting code does not block the event loop.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from app.config import settings


class ServiceSemaphores:
    """Manages semaphores for each protected service.

    Planner and Structured Lookup are unlimited.
    PostgreSQL, Chroma, Embedding, and LLM have configurable limits.
    """

    def __init__(self) -> None:
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._maxes: dict[str, int] = {}
        self._stats: dict[str, dict] = defaultdict(
            lambda: {"acquired": 0, "rejected": 0, "peak": 0, "current": 0}
        )
        self._lock = asyncio.Lock()

    def _ensure(self, name: str, max_concurrent: int) -> asyncio.Semaphore:
        if name not in self._semaphores:
            self._semaphores[name] = asyncio.Semaphore(max_concurrent)
            self._maxes[name] = max_concurrent
        return self._semaphores[name]

    async def acquire(self, name: str, max_concurrent: int | None = None) -> bool:
        """Try to acquire a slot for *name*. Returns True if acquired."""
        if max_concurrent is None:
            max_concurrent = getattr(settings, f"MAX_CONCURRENT_{name.upper()}", 10)
        sem = self._ensure(name, max_concurrent)
        acquired = sem.locked() is False or sem._value > 0  # fast check
        if not acquired:
            async with self._lock:
                self._stats[name]["rejected"] += 1
            return False
        await sem.acquire()
        async with self._lock:
            st = self._stats[name]
            st["acquired"] += 1
            st["current"] += 1
            st["peak"] = max(st["peak"], st["current"])
        return True

    async def wait_acquire(self, name: str, timeout: float = 10.0) -> bool:
        """Block until a slot is available or timeout expires."""
        max_concurrent = getattr(settings, f"MAX_CONCURRENT_{name.upper()}", 10)
        sem = self._ensure(name, max_concurrent)
        try:
            await asyncio.wait_for(sem.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            async with self._lock:
                self._stats[name]["rejected"] += 1
            return False
        async with self._lock:
            st = self._stats[name]
            st["acquired"] += 1
            st["current"] += 1
            st["peak"] = max(st["peak"], st["current"])
        return True

    def release(self, name: str) -> None:
        """Release a slot."""
        if name in self._semaphores:
            self._semaphores[name].release()
        async def _update():
            async with self._lock:
                st = self._stats.get(name)
                if st:
                    st["current"] = max(0, st["current"] - 1)
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(_update())
        except RuntimeError:
            pass

    @property
    def stats(self) -> dict:
        """Return per-service usage stats."""
        result = {}
        for name in self._semaphores:
            max_ = self._maxes.get(name, 1)
            st = dict(self._stats.get(name, {}))
            st["max"] = max_
            st["available"] = max_ - st.get("current", 0)
            st["utilization_pct"] = round((st.get("current", 0) / max_) * 100, 1) if max_ else 0
            result[name] = st
        return result


service_semaphores = ServiceSemaphores()
