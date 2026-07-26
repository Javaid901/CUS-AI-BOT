"""Optional scheduled sync — disabled by default. Enabled via KNOWLEDGE_SYNC_SCHEDULE."""

from __future__ import annotations

import threading

from app.config import settings
from app.utils.logging import log

_SCHEDULER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def _run_schedule(interval_hours: int, auto_discover: bool) -> None:
    """Run sync every `interval_hours` until stop event is set."""
    from app.database import SessionLocal
    from app.knowledge_sync.engine import SyncEngine

    log.info("Knowledge Sync scheduler started (every %dh, auto_discover=%s)", interval_hours, auto_discover)
    while not _STOP_EVENT.wait(interval_hours * 3600):
        if _STOP_EVENT.is_set():
            break
        try:
            db = SessionLocal()
            try:
                engine = SyncEngine(db)
                result = engine.run(auto_discover=auto_discover)
                log.info("Scheduled sync completed: %s", result)
            finally:
                db.close()
        except Exception as exc:
            log.error("Scheduled sync failed: %s", exc)


def start(interval_hours: int | None = None, auto_discover: bool = False) -> None:
    """Start the scheduler thread. No-op if already running or interval is 0."""
    global _SCHEDULER_THREAD
    if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
        log.info("Scheduler already running")
        return
    interval = interval_hours if interval_hours is not None else settings.KNOWLEDGE_SYNC_SCHEDULE_HOURS
    if interval <= 0:
        log.info("Knowledge Sync scheduler is disabled (interval=%d)", interval)
        return
    _STOP_EVENT.clear()
    _SCHEDULER_THREAD = threading.Thread(
        target=_run_schedule,
        args=(interval, auto_discover),
        daemon=True,
    )
    _SCHEDULER_THREAD.start()


def stop() -> None:
    """Signal the scheduler to stop."""
    _STOP_EVENT.set()
    log.info("Knowledge Sync scheduler stopping")
