"""
backend/app/request_manager/models.py

Shared data models for the request management layer.
"""

from __future__ import annotations

import asyncio
import enum
import uuid
from dataclasses import dataclass, field
from typing import Any


class Priority(enum.IntEnum):
    """Request priority — lower number = higher priority."""

    STRUCTURED = 1       # Fee, Eligibility, Duration, College info
    NAVIGATION = 2       # Menu navigation, option selection
    STUDENT_SERVICE = 3  # Authenticated student portal lookups
    RAG = 4             # Semantic search / KB retrieval
    LLM = 5             # Free-form LLM generation


class RequestCost(enum.IntEnum):
    """Weighted execution cost used by the token bucket."""

    STRUCTURED = 1
    NAVIGATION = 1
    STUDENT_SERVICE = 2
    RAG = 3
    LLM = 6


class RequestState(enum.StrEnum):
    """State machine for a queued request."""

    QUEUED = "queued"
    WAITING = "waiting"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class Classification:
    """Result of request classification."""

    priority: Priority
    cost: int
    action: str
    cacheable: bool = False
    cache_ttl: int = 300


@dataclass
class QueuedRequest:
    """A request waiting in the queue."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_id: str = ""
    message: str = ""
    chat_id: str = ""
    priority: Priority = Priority.LLM
    cost: int = 6
    action: str = ""
    state: RequestState = RequestState.QUEUED
    enqueued_at: float = 0.0
    dequeued_at: float | None = None
    timeout: float = 60.0
    retry_count: int = 0
    max_retries: int = 2
    result: Any = None
    error: str | None = None

    @property
    def wait_seconds(self) -> float:
        if self.dequeued_at and self.enqueued_at:
            return self.dequeued_at - self.enqueued_at
        return 0.0


@dataclass
class QueueSlot:
    """A future-like slot that the request generator can await."""

    request: QueuedRequest
    _event: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait(self) -> QueuedRequest:
        await self._event.wait()
        return self.request

    def set(self) -> None:
        self._event.set()



