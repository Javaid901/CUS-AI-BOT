"""
backend/app/request_manager/backpressure.py

Backpressure controller — monitors system capacity and decides when to
slow down, queue, or reject requests.

Thresholds:
  - 80% capacity: slow down expensive requests (add artificial delay)
  - 90% capacity: queue everything except Priority 1 (structured lookups)
  - 100%: reject only if queue is full AND max wait would be exceeded
"""

from __future__ import annotations

import time

from app.config import settings
from app.request_manager.models import Priority


class BackpressureController:
    """Monitors resource usage and provides backpressure guidance."""

    def __init__(self) -> None:
        self._history: list[dict] = []
        self._max_history = 100
        # How long a recorded capacity signal stays meaningful (seconds).
        self._decay_window = 30.0

    def record_capacity(self, usage_pct: float) -> None:
        """Record the current capacity usage percentage."""
        self._history.append({
            "timestamp": time.time(),
            "usage_pct": usage_pct,
        })
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    @property
    def current_usage_pct(self) -> float:
        """Estimate current capacity usage based on recent history.

        Older samples decay linearly to zero over the decay window so a
        single transient spike does not latch the system into backpressure
        forever.
        """
        if not self._history:
            return 0.0
        now = time.time()
        weighted = 0.0
        weight_sum = 0.0
        for sample in self._history[-self._max_history:]:
            age = now - sample["timestamp"]
            if age > self._decay_window or age < 0:
                continue
            weight = 1.0 - (age / self._decay_window)
            weighted += sample["usage_pct"] * weight
            weight_sum += weight
        if weight_sum <= 0:
            return 0.0
        return round(weighted / weight_sum, 2)

    def should_queue(self, priority: Priority) -> bool:
        """Decide whether a request should be queued based on load."""
        usage = self.current_usage_pct
        if usage >= 90:
            # At 90%+, queue everything except priority 1
            return priority != Priority.STRUCTURED
        if usage >= 80:
            # At 80%+, queue LLM and RAG
            return priority >= Priority.RAG
        return False

    def should_slow_down(self, priority: Priority) -> float | None:
        """Return an artificial delay in seconds, or None if no slowdown needed."""
        usage = self.current_usage_pct
        if usage >= 95:
            return 0.5
        if usage >= 80 and priority >= Priority.LLM:
            return 0.2
        return None

    def should_reject(self, queue_full: bool, queue_wait: float) -> bool:
        """Decide whether to return a 429."""
        if not queue_full:
            return False
        if queue_wait >= settings.MAX_QUEUE_WAIT:
            return True
        return self.current_usage_pct >= 99

    @property
    def stats(self) -> dict:
        usage = self.current_usage_pct
        return {
            "current_usage_pct": usage,
            "status": "critical" if usage >= 95 else "heavy" if usage >= 80 else "normal" if usage >= 50 else "idle",
            "history_samples": len(self._history),
        }


backpressure = BackpressureController()
