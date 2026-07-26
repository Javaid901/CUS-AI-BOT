"""
backend/app/request_manager/request_queue.py

Async request queue with priority scheduling, timeout, cancellation, retry,
and duplicate coalescing.

Supports:
  - Priority-based ordering (lower number = higher priority)
  - FIFO within same priority
  - Per-request timeout
  - Cancellation and retry
  - Duplicate request coalescing (same user + same message + same context)
  - Queue status notifications via callback
  - Statistics for monitoring
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from heapq import heappop, heappush

from app.request_manager.models import Priority, QueuedRequest, QueueSlot, RequestState


class RequestQueue:
    """Async priority queue for chat requests."""

    def __init__(self, max_size: int | None = None, max_wait: float | None = None) -> None:
        self._max_size = max_size or 200
        self._max_wait = max_wait or 30.0
        self._heap: list[tuple[int, float, str, QueuedRequest]] = []
        self._slots: dict[str, QueueSlot] = {}
        self._coalesce: dict[str, QueueSlot] = {}
        self._counter = 0
        self._lock = asyncio.Lock()
        self._on_enqueue: list[Callable] = []
        self._on_dequeue: list[Callable] = []
        self._on_complete: list[Callable] = []
        self._stats: dict = {
            "enqueued": 0,
            "dequeued": 0,
            "cancelled": 0,
            "timed_out": 0,
            "coalesced": 0,
            "rejected": 0,
        }

    @property
    def size(self) -> int:
        return len(self._heap)

    @property
    def stats(self) -> dict:
        s = dict(self._stats)
        s["current_size"] = self.size
        s["max_size"] = self._max_size
        s["max_wait_sec"] = self._max_wait
        return s

    def _coalesce_key(self, req: QueuedRequest) -> str:
        """Generate a coalescing key from user + message + chat_id."""
        return f"{req.user_id}:{req.message.strip().lower()}:{req.chat_id}"

    async def enqueue(
        self,
        user_id: str,
        message: str,
        chat_id: str,
        priority: Priority = Priority.LLM,
        cost: int = 6,
        action: str = "",
        timeout: float | None = None,
    ) -> QueueSlot:
        """Enqueue a request. Returns a QueueSlot that can be awaited.

        If an identical request from the same user is already in the queue,
        returns the existing slot (duplicate coalescing).
        """
        req = QueuedRequest(
            user_id=user_id,
            message=message,
            chat_id=chat_id,
            priority=priority,
            cost=cost,
            action=action,
            enqueued_at=time.monotonic(),
            timeout=timeout or self._max_wait,
        )

        async with self._lock:
            # Duplicate coalescing
            ck = self._coalesce_key(req)
            if ck in self._coalesce:
                self._stats["coalesced"] += 1
                return self._coalesce[ck]

            if self.size >= self._max_size:
                self._stats["rejected"] += 1
                raise QueueFullError("Queue is full")

            slot = QueueSlot(request=req)
            self._counter += 1
            entry = (priority.value, self._counter, req.id, req)
            heappush(self._heap, entry)
            self._slots[req.id] = slot
            self._coalesce[ck] = slot
            self._stats["enqueued"] += 1

        self._notify(self._on_enqueue, req)
        return slot

    async def dequeue(self) -> QueuedRequest | None:
        """Pop the highest-priority request from the queue.

        Returns None if the queue is empty.  Removes timed-out entries.
        """
        async with self._lock:
            while self._heap:
                _prio, _counter, rid, req = heappop(self._heap)
                slot = self._slots.get(rid)

                # Check timeout
                elapsed = time.monotonic() - req.enqueued_at
                if elapsed >= req.timeout:
                    req.state = RequestState.TIMEOUT
                    self._stats["timed_out"] += 1
                    self._slots.pop(rid, None)
                    self._cleanup_coalesce(req)
                    continue

                req.state = RequestState.PROCESSING
                req.dequeued_at = time.monotonic()
                self._stats["dequeued"] += 1
                if slot:
                    slot.set()
                self._notify(self._on_dequeue, req)
                return req

            return None

    async def cancel(self, request_id: str) -> bool:
        """Cancel a queued request by ID. Returns True if cancelled."""
        async with self._lock:
            slot = self._slots.get(request_id)
            if slot is None:
                return False
            req = slot.request
            if req.state not in (RequestState.QUEUED, RequestState.WAITING):
                return False
            req.state = RequestState.CANCELLED
            self._slots.pop(request_id, None)
            self._stats["cancelled"] += 1
            self._cleanup_coalesce(req)
            return True

    async def retry(self, request_id: str) -> bool:
        """Re-queue a failed request for retry. Returns True if retried."""
        async with self._lock:
            slot = self._slots.get(request_id)
            if slot is None:
                return False
            req = slot.request
            if req.retry_count >= req.max_retries:
                return False
            req.retry_count += 1
            req.state = RequestState.QUEUED
            req.enqueued_at = time.monotonic()
            # Re-push with exponential backoff priority boost
            boosted_priority = max(1, req.priority.value - 1)
            self._counter += 1
            entry = (boosted_priority, self._counter, req.id, req)
            heappush(self._heap, entry)
            return True

    def _cleanup_coalesce(self, req: QueuedRequest) -> None:
        ck = self._coalesce_key(req)
        self._coalesce.pop(ck, None)

    def _notify(self, handlers: list[Callable], req: QueuedRequest) -> None:
        for handler in handlers:
            try:
                handler(req)
            except Exception:
                pass

    def on_enqueue(self, handler: Callable) -> None:
        self._on_enqueue.append(handler)

    def on_dequeue(self, handler: Callable) -> None:
        self._on_dequeue.append(handler)

    def on_complete(self, handler: Callable) -> None:
        self._on_complete.append(handler)

    async def snapshot(self) -> list[dict]:
        """Return a snapshot of queued requests for monitoring."""
        async with self._lock:
            entries = []
            for prio, counter, rid, req in sorted(self._heap, key=lambda x: (x[0], x[1])):
                elapsed = time.monotonic() - req.enqueued_at
                entries.append({
                    "id": req.id,
                    "user_id": req.user_id[:8] + "...",
                    "action": req.action,
                    "priority": req.priority,
                    "cost": req.cost,
                    "state": req.state.value if hasattr(req.state, "value") else str(req.state),
                    "wait_sec": round(elapsed, 1),
                    "retry_count": req.retry_count,
                    "timeout": req.timeout,
                })
            return entries


class QueueFullError(Exception):
    pass


request_queue = RequestQueue()
