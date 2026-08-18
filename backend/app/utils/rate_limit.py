"""
backend/app/utils/rate_limit.py

In-memory sliding-window rate limiter for the chat endpoint.
Sufficient for a single-process deployment; swap for Redis in multi-instance.

Used via the `chat_rate_limit` dependency which raises 429 when exceeded.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.config import settings
from fastapi import HTTPException, Request, status

_HITS: dict[str, deque[float]] = defaultdict(deque)
_RATE_LOCK = threading.Lock()


def _client_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def chat_rate_limit(request: Request) -> None:
    limit = settings.RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return
    key = _client_key(request)
    now = time.time()
    window_start = now - 60
    with _RATE_LOCK:
        dq = _HITS[key]
        while dq and dq[0] < window_start:
            dq.popleft()
        if len(dq) >= limit:
            retry = int(dq[0] - window_start) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {retry}s.",
            )
        dq.append(now)


def endpoint_rate_limit(max_per_minute: int, bucket: str = "endpoint"):
    """Factory for per-IP sliding-window limits on public endpoints.

    Usage:  Depends(endpoint_rate_limit(settings.GRIEVANCE_CREATE_LIMIT, "grievance-create"))
    Raises HTTPException(429) when the client exceeds the limit.
    """

    def _dep(request: Request) -> None:
        limit = max_per_minute
        if limit <= 0:
            return
        key = f"{bucket}:{_client_key(request)}"
        now = time.time()
        window_start = now - 60
        with _RATE_LOCK:
            dq = _HITS[key]
            while dq and dq[0] < window_start:
                dq.popleft()
            if len(dq) >= limit:
                retry = int(dq[0] - window_start) + 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded. Try again in {retry}s.",
                )
            dq.append(now)

    return _dep
