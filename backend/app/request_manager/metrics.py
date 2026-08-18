"""
backend/app/request_manager/metrics.py

Real-time operational metrics for the request management layer.

Collects and exposes:
  - Queue length and utilization
  - Concurrent requests per service
  - Average wait and response times
  - Token bucket stats
  - Cache hit rates
  - 429 and rejection counts
  - Requests per second
  - Peak concurrent users

These are updated in real-time and exposed via the AI Insights dashboard.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any


class RequestMetrics:
    """Thread-safe metrics collector for the request management layer."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reset()

    def _reset(self) -> None:
        self._request_times: deque[float] = deque(maxlen=1000)
        self._latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))
        self._counters: dict[str, int] = defaultdict(int)
        self._peak_concurrent = 0
        self._current_concurrent = 0
        self._429_count = 0
        self._rejected_count = 0
        self._queued_count = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._start_time = time.time()

    def record_request(self) -> None:
        """Record an incoming request (for RPS calculation)."""
        with self._lock:
            self._request_times.append(time.time())
            self._current_concurrent += 1
            self._peak_concurrent = max(self._peak_concurrent, self._current_concurrent)

    def record_response(self, latency_ms: float, stage: str = "total") -> None:
        """Record a completed response with its latency.

        Only the terminal records ("total" for executed work, "cache" for a
        served cache hit) release the concurrent-slot counter; per-stage
        records (action, etc.) are pure latency samples.
        """
        with self._lock:
            self._latencies[stage].append(latency_ms)
            if stage in ("total", "cache"):
                self._current_concurrent = max(0, self._current_concurrent - 1)

    def record_429(self) -> None:
        with self._lock:
            self._429_count += 1

    def record_rejected(self) -> None:
        with self._lock:
            self._rejected_count += 1

    def record_queued(self) -> None:
        with self._lock:
            self._queued_count += 1

    def record_cache_hit(self) -> None:
        with self._lock:
            self._cache_hits += 1

    def record_cache_miss(self) -> None:
        with self._lock:
            self._cache_misses += 1

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self._start_time

    @property
    def requests_per_second(self) -> float:
        with self._lock:
            if not self._request_times:
                return 0.0
            window = 60.0
            cutoff = time.time() - window
            recent = [t for t in self._request_times if t >= cutoff]
            return len(recent) / window

    def _p50(self, values: list[float]) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        return s[len(s) // 2]

    def _p90(self, values: list[float]) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        return s[int(len(s) * 0.9)]

    def _p99(self, values: list[float]) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        return s[int(len(s) * 0.99)]

    def latency_stats(self, stage: str = "total") -> dict:
        with self._lock:
            vals = list(self._latencies.get(stage, []))
        return {
            "avg_ms": round(sum(vals) / len(vals), 1) if vals else 0,
            "p50_ms": round(self._p50(vals), 1) if vals else 0,
            "p90_ms": round(self._p90(vals), 1) if vals else 0,
            "p99_ms": round(self._p99(vals), 1) if vals else 0,
            "samples": len(vals),
        }

    @property
    def cache_hit_rate(self) -> float:
        total = self._cache_hits + self._cache_misses
        return round(self._cache_hits / total * 100, 1) if total > 0 else 0.0

    @property
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "uptime_sec": round(self.uptime_seconds),
                "requests_per_sec": round(self.requests_per_second, 2),
                "current_concurrent": self._current_concurrent,
                "peak_concurrent": self._peak_concurrent,
                "total_429": self._429_count,
                "total_rejected": self._rejected_count,
                "total_queued": self._queued_count,
                "cache_hit_rate_pct": self.cache_hit_rate,
                "latency": {
                    stage: self.latency_stats(stage)
                    for stage in list(self._latencies.keys())
                },
                "counters": dict(self._counters),
            }


request_metrics = RequestMetrics()
