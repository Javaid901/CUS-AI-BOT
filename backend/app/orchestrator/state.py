"""
backend/app/orchestrator/state.py

Enhanced conversation state management.

Extends the simple nav-path dict from intent_router with:
  - Service context tracking
  - Authentication state per service
  - Breadcrumb trail for multi-step flows
  - TTL-based eviction to prevent memory leaks

NOTE: Navigation path state is managed by intent_router._nav_state.
      This module handles service-level state only.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app.orchestrator.context import ConversationContext


@dataclass
class ServiceAuthState:
    """Authentication state for a specific student service.

    CREDENTIALS MUST NEVER BE STORED HERE.
    Only session tokens (opaque, ephemeral) are stored.

    Security rules:
      - registration_number: NOT stored (PII)
      - password: NOT stored (secret)
      - session_token: stored in memory only, NEVER persisted to DB/disk
      - session_token is destroyed on logout, timeout, or state eviction
    """

    status: str = "none"  # none | pending | authenticated | failed
    session_token: str | None = None
    session_expiry: float | None = None
    last_error: str | None = None
    attempt_count: int = 0


@dataclass
class Breadcrumb:
    """A single breadcrumb entry for navigation history."""

    label: str
    type: str = "nav"  # nav | service | detail
    context: dict[str, Any] = field(default_factory=dict)


_MAX_BREADCRUMBS = 20


@dataclass
class ConversationState:
    """Full state for a single conversation session.

    Navigation path is NOT stored here — it lives in intent_router._nav_state.
    This class tracks service-level concerns only.
    """

    chat_id: str
    service_context: str | None = None
    service_auth: dict[str, ServiceAuthState] = field(default_factory=dict)
    breadcrumbs: list[Breadcrumb] = field(default_factory=list)
    context: ConversationContext = field(default_factory=ConversationContext)
    last_intent: str | None = None
    service_data: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)
    # Student identity — populated after successful auth; persists across
    # service calls within the same conversation session.
    student_reg_no: str | None = None
    student_name: str | None = None
    student_programme: str | None = None
    student_semester: int | None = None
    student_session_id: str | None = None

    # Pending request — saved BEFORE auth form is shown so it can be resumed
    # automatically after successful authentication.
    pending_service: str | None = None
    pending_action: str | None = None  # "fetch" | "search" | "execute"
    pending_query: str | None = None   # original user message
    pending_params: dict[str, str] = field(default_factory=dict)

    def touch(self) -> None:
        self.touched_at = time.time()


# ---------------------------------------------------------------------------
# Singleton state store with periodic TTL cleanup
# ---------------------------------------------------------------------------

_STATE: dict[str, ConversationState] = {}
_LOCK = asyncio.Lock()
_TTL_SECONDS = 1800  # 30 minutes of inactivity
_evict_counter: int = 0


async def get_state(chat_id: str) -> ConversationState:
    """Get or create a ConversationState for the given chat_id."""
    async with _LOCK:
        if chat_id not in _STATE:
            _STATE[chat_id] = ConversationState(chat_id=chat_id)
        state = _STATE[chat_id]
        state.touch()
    # Periodic eviction check (every 50 accesses)
    global _evict_counter
    _evict_counter += 1
    if _evict_counter % 50 == 0:
        await evict_stale()
    return state


async def set_state(chat_id: str, state: ConversationState) -> None:
    async with _LOCK:
        _STATE[chat_id] = state


async def clear_state(chat_id: str) -> None:
    """Clear all state for a chat session."""
    async with _LOCK:
        _STATE.pop(chat_id, None)


async def pop_breadcrumb(chat_id: str) -> Breadcrumb | None:
    """Pop the last breadcrumb and return it."""
    state = await get_state(chat_id)
    if state.breadcrumbs:
        return state.breadcrumbs.pop()
    return None


async def push_breadcrumb(chat_id: str, crumb: Breadcrumb) -> None:
    """Push a breadcrumb with duplicate prevention and size limit."""
    state = await get_state(chat_id)
    if state.breadcrumbs and state.breadcrumbs[-1].label == crumb.label:
        return  # Skip duplicate
    if len(state.breadcrumbs) >= _MAX_BREADCRUMBS:
        state.breadcrumbs.pop(0)  # Evict oldest
    state.breadcrumbs.append(crumb)


async def evict_stale() -> int:
    """Remove states that have exceeded the TTL. Returns count evicted."""
    now = time.time()
    async with _LOCK:
        stale = [cid for cid, s in _STATE.items() if now - s.touched_at > _TTL_SECONDS]
        for cid in stale:
            _STATE.pop(cid, None)
    return len(stale)


async def get_auth_state(chat_id: str, service: str) -> ServiceAuthState:
    """Get or create auth state for a specific service."""
    state = await get_state(chat_id)
    if service not in state.service_auth:
        state.service_auth[service] = ServiceAuthState()
    return state.service_auth[service]


async def service_needs_auth(chat_id: str, service: str) -> bool:
    """Check if the user needs to authenticate for a service.

    If the student has an active global session (logged in once), all
    services skip the auth form.
    """
    state = await get_state(chat_id)
    if state.student_reg_no is not None:
        return False
    auth = state.service_auth.get(service)
    return auth is None or auth.status != "authenticated"


async def is_service_authenticated(chat_id: str) -> bool:
    """Check if the CURRENT service context is authenticated.

    Also returns True if a global student session exists.
    """
    state = await get_state(chat_id)
    if state.student_reg_no is not None:
        return True
    if not state.service_context:
        return False
    auth = state.service_auth.get(state.service_context)
    return auth is not None and auth.status == "authenticated"
