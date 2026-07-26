"""
backend/app/request_manager/token_bucket.py

Token Bucket rate limiter.

Replaces the old sliding-window rate limiter (utils/rate_limit.py).

Each user gets a token bucket. Tokens are refilled at a configurable rate.
Each request costs a number of tokens based on its weight.
Heavy users naturally slow down; normal users never see 429.
"""

from __future__ import annotations

import threading
import time

from app.config import settings


class _UserBucket:
    """Per-user token bucket."""

    __slots__ = ("last_refill", "max_tokens", "refill_rate", "tokens")

    def __init__(self, max_tokens: int, refill_rate: float) -> None:
        self.tokens = float(max_tokens)
        self.last_refill = time.monotonic()
        self.max_tokens = float(max_tokens)
        self.refill_rate = refill_rate  # tokens per second

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def consume(self, cost: int = 1) -> bool:
        """Try to consume *cost* tokens. Returns True if allowed."""
        self._refill()
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

    @property
    def available(self) -> float:
        self._refill()
        return self.tokens

    @property
    def wait_seconds(self) -> float:
        """Estimated seconds until enough tokens are available for *cost*."""
        self._refill()
        if self.refill_rate <= 0:
            return float("inf")
        return self.tokens / self.refill_rate


class TokenBucket:
    """Global token bucket manager — one bucket per user key."""

    def __init__(
        self,
        max_tokens: int | None = None,
        refill_rate: float | None = None,
    ) -> None:
        self._max_tokens = float(max_tokens or settings.TOKEN_BUCKET_SIZE)
        self._refill_rate = float(refill_rate or settings.TOKEN_REFILL_RATE)
        self._buckets: dict[str, _UserBucket] = {}
        self._lock = threading.Lock()

    def _get_or_create(self, key: str) -> _UserBucket:
        if key not in self._buckets:
            self._buckets[key] = _UserBucket(self._max_tokens, self._refill_rate)
        return self._buckets[key]

    def consume(self, key: str, cost: int = 1) -> bool:
        """Deduct *cost* tokens from the user's bucket. Returns True if allowed."""
        with self._lock:
            bucket = self._get_or_create(key)
            return bucket.consume(cost)

    def available(self, key: str) -> float:
        """Return current token count for a user."""
        with self._lock:
            bucket = self._get_or_create(key)
            return bucket.available

    def wait_estimate(self, key: str, cost: int = 1) -> float:
        """Estimate seconds until *cost* tokens are available."""
        with self._lock:
            bucket = self._get_or_create(key)
            return bucket.wait_seconds

    def reset(self, key: str) -> None:
        """Reset a user's bucket to full."""
        with self._lock:
            self._buckets.pop(key, None)

    @property
    def active_users(self) -> int:
        return len(self._buckets)

    def stats(self) -> dict:
        with self._lock:
            active = len(self._buckets)
            total_tokens = sum(b.available for b in self._buckets.values())
        return {
            "active_users": active,
            "total_tokens_remaining": round(total_tokens, 1),
            "max_tokens_per_user": self._max_tokens,
            "refill_rate_per_sec": self._refill_rate,
        }


token_bucket = TokenBucket()
