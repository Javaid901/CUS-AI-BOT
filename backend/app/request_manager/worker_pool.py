"""
backend/app/request_manager/worker_pool.py

Auto-scaling worker pool that processes queued requests.

Scales between WORKER_MIN and WORKER_MAX based on queue depth.
Workers pick the highest-priority request from the queue and execute it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.config import settings
from app.request_manager.models import QueuedRequest, RequestState
from app.request_manager.request_queue import request_queue
from app.utils.logging import log


class WorkerPool:
    """Auto-scaling pool of async workers processing queued requests."""

    def __init__(
        self,
        min_workers: int | None = None,
        max_workers: int | None = None,
    ) -> None:
        self._min = min_workers or settings.WORKER_MIN
        self._max = max_workers or settings.WORKER_MAX
        self._workers: set[asyncio.Task] = set()
        self._running = False
        self._processor: Callable | None = None
        self._scan_interval = 1.0

    @property
    def active_count(self) -> int:
        return len(self._workers)

    @property
    def target_count(self) -> int:
        """Calculate desired worker count based on queue depth."""
        qsize = request_queue.size
        if qsize == 0:
            return self._min
        if qsize <= 5:
            return max(self._min, 2)
        if qsize <= 20:
            return max(self._min, 4)
        return self._max

    def set_processor(self, processor: Callable) -> None:
        """Set the async callable that processes each request."""
        self._processor = processor

    async def start(self) -> None:
        """Start the worker pool (auto-scaling loop)."""
        if self._running:
            return
        self._running = True
        log.info("Worker pool starting (min=%d, max=%d)", self._min, self._max)
        asyncio.ensure_future(self._scale_loop())

    async def stop(self) -> None:
        self._running = False
        for task in self._workers:
            task.cancel()
        self._workers.clear()

    async def _scale_loop(self) -> None:
        """Periodically adjust worker count based on queue depth."""
        while self._running:
            target = self.target_count
            current = self.active_count

            if current < target:
                needed = target - current
                for _ in range(needed):
                    task = asyncio.ensure_future(self._worker_loop())
                    self._workers.add(task)
                    task.add_done_callback(self._workers.discard)
            elif current > target:
                # Let excess workers finish naturally; don't force-kill
                pass

            await asyncio.sleep(self._scan_interval)

    async def _worker_loop(self) -> None:
        """Worker coroutine — continuously pulls requests from the queue."""
        while self._running:
            try:
                request = await request_queue.dequeue()
                if request is None:
                    await asyncio.sleep(0.1)
                    continue
                await self._process_request(request)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("Worker error: %s", exc)
                await asyncio.sleep(0.5)

    async def _process_request(self, request: QueuedRequest) -> None:
        """Process a single request using the configured processor."""
        if not self._processor:
            log.error("No processor configured for worker pool")
            return
        try:
            await self._processor(request)
            request.state = RequestState.COMPLETED
        except Exception as exc:
            log.error("Request %s failed: %s", request.id, exc)
            request.state = RequestState.FAILED
            request.error = str(exc)
            # Auto-retry for transient failures
            if request.retry_count < request.max_retries:
                log.info("Retrying request %s (attempt %d/%d)",
                         request.id, request.retry_count + 1, request.max_retries)
                await request_queue.retry(request.id)

    @property
    def stats(self) -> dict:
        return {
            "active_workers": self.active_count,
            "target_workers": self.target_count,
            "min_workers": self._min,
            "max_workers": self._max,
            "running": self._running,
        }


worker_pool = WorkerPool()
