"""
backend/app/orchestrator/metrics.py

Lightweight timing instrumentation for the orchestration pipeline.

Records per-request latencies for each pipeline stage and exposes
a summary via a health/metrics endpoint.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any

from app.utils.logging import log

# ---------------------------------------------------------------------------
# Stage-level timer
# ---------------------------------------------------------------------------

_timings: dict[str, list[float]] = {}
_MAX_SAMPLES = 1000
_METRICS_LOCK = threading.Lock()


@contextmanager
def stage_timer(stage: str):
    """Context manager that records elapsed time for a named stage.

    Usage:
        with stage_timer("intent_detection"):
            ...
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed = (time.perf_counter() - t0) * 1000  # ms
        with _METRICS_LOCK:
            samples = _timings.setdefault(stage, [])
            samples.append(elapsed)
            if len(samples) > _MAX_SAMPLES:
                samples.pop(0)


def stage_elapsed(stage: str) -> float | None:
    """Return the last recorded elapsed time for a stage (ms)."""
    samples = _timings.get(stage)
    if samples:
        return samples[-1]
    return None


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def metrics_summary() -> dict[str, Any]:
    """Return a summary of all recorded timing metrics."""
    with _METRICS_LOCK:
        summary: dict[str, Any] = {}
        for stage, samples in _timings.items():
            if not samples:
                continue
            avg = sum(samples) / len(samples)
            summary[stage] = {
                "count": len(samples),
                "last_ms": round(samples[-1], 1),
                "avg_ms": round(avg, 1),
                "min_ms": round(min(samples), 1),
                "max_ms": round(max(samples), 1),
            }
        return summary


def clear_metrics() -> None:
    with _METRICS_LOCK:
        _timings.clear()


# ---------------------------------------------------------------------------
# Convenience log helper
# ---------------------------------------------------------------------------


def log_stage(stage: str, detail: str = "", **extra: Any) -> None:
    """Log a pipeline stage with its elapsed time."""
    elapsed = stage_elapsed(stage)
    elapsed_str = f"{elapsed:.1f}ms" if elapsed is not None else "?"
    parts = [f"[{stage}] {elapsed_str}"]
    if detail:
        parts.append(detail)
    for k, v in extra.items():
        parts.append(f"{k}={v}")
    log.info(" ".join(parts))
