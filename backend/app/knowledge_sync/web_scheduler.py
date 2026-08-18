"""
backend/app/knowledge_sync/web_scheduler.py

Scheduled running of the Website Knowledge Sync engine.

Reads the persisted sync state (dashboard-controlled):
    enabled : bool
    schedule: manual | hourly | daily | weekly | monthly | disabled
    hours   : explicit cadence override (0 disables)

The scheduler polls the state every 60s and triggers a background full crawl
when the next run is due. It never blocks the chat/API event loop.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from app.knowledge_sync.web_engine import load_state, save_state
from app.utils.logging import log

_SCHEDULER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()
_POLL_SECONDS = 60

# Schedule presets: hours between sync runs (0 = disabled / manual)
SCHEDULE_PRESETS = {
    "disabled": 0,       # master toggle must be ON for any sync
    "manual": 0,         # ON + manual = immediate sync, no repeats
    "hourly": 1,
    "6hourly": 6,
    "daily": 24,
    "weekly": 168,
    "monthly": 720,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_master_enabled() -> bool:
    """Check the master sync toggle. Returns True when the admin has turned
    the Website Sync master switch ON (via the dashboard or API)."""
    state = load_state()
    return bool(state.get("enabled", False))


def _get_schedule_hours() -> int:
    """Return the scheduled interval hours based on the state preset."""
    schedule = load_state().get("schedule", "disabled")
    return SCHEDULE_PRESETS.get(schedule, 0)


def _run_sync() -> None:
    """Fire-and-forget a full crawl in a dedicated thread."""
    def _worker() -> None:
        try:
            from app.database import SessionLocal
            from app.knowledge_sync.web_engine import WebsiteSyncEngine

            db = SessionLocal()
            try:
                # Check master toggle before running
                if not _is_master_enabled():
                    log.info("Sync skipped: master toggle is OFF")
                    return
                engine = WebsiteSyncEngine(db)
                stats = engine.run(trigger="scheduled")
                log.info("Scheduled website sync completed: %s", stats)
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            log.error("Scheduled sync crashed: %s", exc)

    threading.Thread(target=_worker, daemon=True).start()


def _run_loop() -> None:
    """Poll the persisted state every POLL_SECONDS and trigger sync when due."""
    log.info("Website Sync scheduler started (poll %ss)", _POLL_SECONDS)
    while not _STOP_EVENT.wait(_POLL_SECONDS):
        try:
            state = load_state()
            # Master toggle check — if OFF, never auto-crawl regardless of schedule
            if not _is_master_enabled():
                # Still track last poll time for UI, but skip crawl
                log.info("Scheduler: master toggle OFF — skipping auto-crawl")
                continue
            hours = _get_schedule_hours()
            if hours <= 0:
                # "manual" schedule: only run when explicitly triggered,
                # never auto-repeat. The engine run with trigger="manual"
                # performs one shot; the scheduler then waits longer.
                log.info("Scheduler: manual schedule — performing immediate sync")
                _run_sync_manual()
                # After manual sync, wait longer before checking again
                # (the engine run will have set next_run_at or the state
                # will reflect that manual mode doesn't auto-repeat).
                # Sleep a bit longer to avoid tight loop.
                threading.Event().wait(3600)  # 1 hour cooldown
                continue
            if hours <= 0:
                continue
            last_raw = state.get("last_run_at")
            due = True
            if last_raw:
                try:
                    last = datetime.fromisoformat(last_raw)
                    due = (_utcnow() - last).total_seconds() >= hours * 3600
                except ValueError:
                    due = True
            if due:
                state["last_run_at"] = _utcnow().isoformat()
                save_state(state)
                _run_sync()
        except Exception as exc:  # noqa: BLE001
            log.error("Website sync scheduler iteration failed: %s", exc)


def _run_sync_manual() -> None:
    """Run a single manual sync respecting the master toggle."""
    if not _is_master_enabled():
        log.info("Manual sync skipped: master toggle is OFF")
        return
    _run_sync()


def start() -> None:
    """Start the scheduler thread (no-op if already running)."""
    global _SCHEDULER_THREAD
    global _STOP_EVENT
    if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
        log.info("Scheduler already running")
        return
    _STOP_EVENT = threading.Event()
    thread = threading.Thread(target=_run_loop, daemon=True)
    _SCHEDULER_THREAD = thread
    thread.start()
    log.info("Website Sync scheduler started")


def stop() -> None:
    """Signal the scheduler thread to stop."""
    _STOP_EVENT.set()