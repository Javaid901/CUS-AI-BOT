"""
backend/app/utils/logging.py

Structured-ish logging to stdout plus DB audit logging.

  - get_logger: standard library logger
  - audit: writes an AuditLog row (best-effort, never raises to the caller)
"""

from __future__ import annotations

import logging
import sys

from app.models import AuditLog
from sqlalchemy.orm import Session

_CONFIGURED = False


def get_logger(name: str = "cus_ai") -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger(name)
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _CONFIGURED = True
    return logger


log = get_logger()


def audit(
    db: Session,  # kept for backward-compat signature; no longer used internally
    action: str,
    actor_id: str | None = None,
    actor_role: str | None = None,
    target: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    """Persist an audit entry with its own session to avoid flushing caller's pending changes.
    Swallows all errors so callers are never blocked."""
    from app.database import SessionLocal

    _db: Session | None = None
    try:
        _db = SessionLocal()
        _db.add(
            AuditLog(
                actor_id=actor_id,
                actor_role=actor_role,
                action=action,
                target=target,
                detail=detail,
                ip=ip,
            )
        )
        _db.commit()
    except Exception as exc:  # pragma: no cover - defensive
        if _db is not None:
            try:
                _db.rollback()
            except Exception:
                pass
        log.warning("audit log write failed: %s", exc)
    finally:
        if _db is not None:
            _db.close()
