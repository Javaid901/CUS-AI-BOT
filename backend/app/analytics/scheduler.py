"""
backend/app/analytics/scheduler.py

Background scheduler for periodic analytics tasks.

Tasks:
  - Aggregation: computes daily/weekly/monthly/yearly rollups
  - Insight generation: builds automated observations
  - Cleanup: removes expired data per retention policy

Uses asyncio for non-blocking periodic execution.
"""

from __future__ import annotations

import asyncio
import time

from app.utils.logging import log

_AGGREGATION_INTERVAL = 3600  # 1 hour
_CLEANUP_INTERVAL = 86400     # 24 hours
_INSIGHT_INTERVAL = 7200       # 2 hours

_task: asyncio.Task | None = None
_running = False


async def _run_aggregation() -> None:
    """Run aggregation in a thread to avoid blocking."""
    from app.analytics.aggregator import run_aggregation
    loop = asyncio.get_event_loop()
    t0 = time.monotonic()
    try:
        result = await loop.run_in_executor(None, run_aggregation)
        elapsed = time.monotonic() - t0
        log.info("Analytics aggregation completed in %.2fs: %s", elapsed, result)
    except Exception as exc:
        log.warning("Analytics aggregation failed: %s", exc)


async def _run_cleanup(retention_days: int = 365) -> None:
    """Run data cleanup."""
    from app.analytics.aggregator import run_cleanup
    loop = asyncio.get_event_loop()
    t0 = time.monotonic()
    try:
        result = await loop.run_in_executor(None, run_cleanup, retention_days)
        elapsed = time.monotonic() - t0
        if result.get("events") or result.get("samples"):
            log.info("Analytics cleanup removed %s events, %s samples (%.2fs)", result.get("events", 0), result.get("samples", 0), elapsed)
    except Exception as exc:
        log.warning("Analytics cleanup failed: %s", exc)


async def _run_insights() -> None:
    """Generate and log automated insights."""
    from app.analytics.insights import generate_insights
    loop = asyncio.get_event_loop()
    try:
        insights = await loop.run_in_executor(None, generate_insights)
        if insights:
            log.info("Generated %d analytics insights", len(insights))
            for ins in insights[:3]:
                log.debug("Insight [%s]: %s", ins.get("severity"), ins.get("message"))
    except Exception as exc:
        log.warning("Analytics insight generation failed: %s", exc)


async def _scheduler_loop(aggregation_interval: int, cleanup_interval: int, insight_interval: int) -> None:
    """Main scheduler loop."""
    global _running
    _running = True
    last_aggregation = 0.0
    last_cleanup = 0.0
    last_insight = 0.0

    # Run initial aggregation on startup
    await _run_aggregation()
    last_aggregation = time.monotonic()

    try:
        while _running:
            await asyncio.sleep(60)  # check every minute
            now = time.monotonic()

            if now - last_aggregation >= aggregation_interval:
                await _run_aggregation()
                last_aggregation = now

            if now - last_cleanup >= cleanup_interval:
                await _run_cleanup()
                last_cleanup = now

            if now - last_insight >= insight_interval:
                await _run_insights()
                last_insight = now
    except asyncio.CancelledError:
        pass
    finally:
        _running = False


def start(aggregation_interval: int = _AGGREGATION_INTERVAL,
          cleanup_interval: int = _CLEANUP_INTERVAL,
          insight_interval: int = _INSIGHT_INTERVAL) -> None:
    """Start the analytics background scheduler.

    Args:
        aggregation_interval: Seconds between aggregation runs (default 3600)
        cleanup_interval: Seconds between cleanup runs (default 86400)
        insight_interval: Seconds between insight generation runs (default 7200)
    """
    global _task
    if _task is not None and not _task.done():
        log.warning("Analytics scheduler already running")
        return
    _task = asyncio.create_task(
        _scheduler_loop(aggregation_interval, cleanup_interval, insight_interval)
    )
    log.info("Analytics scheduler started (agg=%ds, cleanup=%ds, insight=%ds)",
             aggregation_interval, cleanup_interval, insight_interval)


def stop() -> None:
    """Stop the analytics background scheduler."""
    global _task, _running
    _running = False
    if _task:
        _task.cancel()
        _task = None
        log.info("Analytics scheduler stopped")
