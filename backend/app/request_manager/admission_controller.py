"""
backend/app/request_manager/admission_controller.py

Admission Controller — the main entry point for the request management layer.

Flow:
  1. Classify the request (priority + cost)
  2. Check response cache (fast path for cached structured responses)
  3. Try token bucket (fast path if tokens available)
  4. Check backpressure (decide queue vs immediate)
  5. If queue needed, enqueue and yield status events
  6. When dequeued, acquire service semaphore(s) and execute
  7. After execution, release semaphores, cache response, update metrics

Replaces the old `chat_rate_limit` dependency.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

from app.config import settings
from app.request_manager.backpressure import backpressure
from app.request_manager.metrics import request_metrics
from app.request_manager.models import Classification, Priority
from app.request_manager.priority_scheduler import classify_request
from app.request_manager.request_queue import QueueFullError, request_queue
from app.request_manager.response_cache import response_cache
from app.request_manager.service_semaphores import service_semaphores
from app.request_manager.token_bucket import token_bucket
from app.utils.logging import log


class AdmissionController:
    """Orchestrates request admission, queuing, and execution."""

    def __init__(self) -> None:
        self._executor: Callable | None = None

    def set_executor(self, executor: Callable) -> None:
        """Set the async generator function that processes requests.

        The executor should have the signature:
            async def executor(user_id, message, chat_id, classification) -> AsyncGenerator[dict, None]
        """
        self._executor = executor

    async def admit(
        self,
        user_id: str,
        message: str,
        chat_id: str,
        planner_action: str | None = None,
        executor: Callable | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Main admission control flow. Yields SSE-compatible event dicts.

        Args:
            user_id: The requesting user's ID.
            message: The chat message text.
            chat_id: Conversation ID.
            planner_action: Pre-computed planner action (optional).
            executor: Async generator function to execute the request.
                      Falls back to self._executor if not provided.
                      Signature: async def f(user_id, message, chat_id) -> AsyncGenerator[dict, None]
        """
        request_metrics.record_request()
        t0 = time.perf_counter()
        active_executor = executor or self._executor

        # ---- Step 1: Classify ----
        classification = classify_request(message, planner_action=planner_action)
        log.debug("Admission: user=%s action=%s priority=%d cost=%d",
                  user_id[:8], classification.action, classification.priority, classification.cost)

        # ---- Step 2: Check response cache (structured only) ----
        if classification.cacheable:
            # Key on the message itself (never on chat_id + action alone) so a
            # cached answer can never be served for a different question asked
            # in the same conversation.
            cache_hit, cached = response_cache.get_generic(
                q=message, action=classification.action
            )
            if cache_hit:
                request_metrics.record_cache_hit()
                request_metrics.record_response(0, "cache")
                log.debug("Cache HIT for %s: %s", classification.action, message[:48])
                yield {"type": "token", "text": cached}
                yield {"type": "done", "chat_id": chat_id, "cited_chunks": [], "cached": True}
                return

        request_metrics.record_cache_miss()

        # ---- Step 3: Check backpressure ----
        slowdown = backpressure.should_slow_down(classification.priority)
        if slowdown:
            await asyncio.sleep(slowdown)
        needs_queue = backpressure.should_queue(classification.priority)

        # ---- Step 4: Try token bucket (fast path) ----
        user_key = user_id
        has_tokens = token_bucket.consume(user_key, classification.cost)

        if has_tokens and not needs_queue:
            # Fast path — execute immediately
            log.debug("Fast path for user=%s action=%s", user_id[:8], classification.action)
            async for event in self._execute_with_protection(
                user_id, message, chat_id, classification, t0, active_executor
            ):
                yield event
            return
        elif not needs_queue and not has_tokens:
            # Tokens short on the immediate path — small wait for refill, then retry once.
            est_wait = token_bucket.wait_estimate(user_key, classification.cost)
            if 0 < est_wait <= settings.MAX_SEMAPHORE_WAIT:
                await asyncio.sleep(min(est_wait, 2.0))
                if token_bucket.consume(user_key, classification.cost):
                    async for event in self._execute_with_protection(
                        user_id, message, chat_id, classification, t0, active_executor
                    ):
                        yield event
                    return

        # Any request that does not take the fast path must not keep its token
        # debit — it is queued (or times out) and executed later.
        if has_tokens:
            token_bucket.refund(user_key, classification.cost)

        # ---- Step 5: Queue path ----
        request_metrics.record_queued()
        estimated_wait = token_bucket.wait_estimate(user_key, classification.cost)

        try:
            slot = await request_queue.enqueue(
                user_id=user_id,
                message=message,
                chat_id=chat_id,
                priority=classification.priority,
                cost=classification.cost,
                action=classification.action,
            )
        except QueueFullError:
            request_metrics.record_rejected()
            backpressure.record_capacity(100.0)
            # Last resort — return 429 with proper Retry-After info
            yield {
                "type": "error",
                "message": (
                    "The system is currently at maximum capacity. "
                    f"Please try again in a moment. (retry_after={int(estimated_wait)}s)"
                ),
            }
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
            request_metrics.record_response(0, "total")
            return

        # Yield queue status events
        yield {
            "type": "queued",
            "position": request_queue.size,
            "estimated_wait_sec": round(estimated_wait, 1),
            "priority": classification.priority,
            "action": classification.action,
        }

        # Wait for dequeue — bounded so the SSE stream can never hang forever.
        try:
            await asyncio.wait_for(
                slot.wait(), timeout=max(settings.MAX_QUEUE_WAIT, 10.0)
            )
        except asyncio.TimeoutError:
            await request_queue.cancel(slot.request.id)
            yield {
                "type": "error",
                "message": (
                    "Request timed out while waiting in the queue. "
                    "Please try again."
                ),
            }
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
            request_metrics.record_response(0, "total")
            return

        yield {"type": "processing", "action": classification.action}

        # Re-debit the tokens that were refunded when this request queued
        # (fast path debits at admission, line ~105). Without this a heavy
        # user could queue unlimited expensive requests and never pay.
        token_bucket.consume(user_key, classification.cost)

        # Execute with resource protection
        async for event in self._execute_with_protection(
            user_id, message, chat_id, classification, t0, active_executor
        ):
            yield event

    async def _execute_with_protection(
        self,
        user_id: str,
        message: str,
        chat_id: str,
        classification: Classification,
        t0: float,
        executor: Callable | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a request with per-service semaphore protection."""
        sem_name = self._semaphore_name(classification.action)
        sem_acquired = False
        active_executor = executor or self._executor

        try:
            # Acquire service semaphore (non-blocking for fast services)
            if classification.priority <= Priority.NAVIGATION:
                sem_acquired = True  # no limit for structured/navigation
            else:
                sem_acquired = await service_semaphores.wait_acquire(
                    sem_name, timeout=settings.MAX_SEMAPHORE_WAIT
                )

            if not sem_acquired and classification.priority > Priority.NAVIGATION:
                yield {
                    "type": "error",
                    "message": "The service is temporarily busy. Please try again.",
                }
                yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
                return

            # Execute
            if active_executor:
                stage_t0 = time.perf_counter()
                captured_text = ""
                async for event in active_executor(user_id, message, chat_id):
                    yield event
                    if event.get("type") == "token":
                        captured_text += event.get("text") or ""
                    elif event.get("type") in ("detail", "options") and event.get("message"):
                        captured_text = event["message"]
                    elif event.get("type") == "done" and classification.cacheable and captured_text:
                        response_cache.set_generic(
                            captured_text, ttl=classification.cache_ttl,
                            q=message, action=classification.action,
                        )
                elapsed_ms = int((time.perf_counter() - stage_t0) * 1000)
                request_metrics.record_response(elapsed_ms, classification.action)
            else:
                yield {"type": "error", "message": "No executor configured"}
                yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}

        except asyncio.CancelledError:
            yield {"type": "error", "message": "Request was cancelled"}
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        except Exception as exc:
            log.error("Execution failed: %s", exc)
            yield {"type": "error", "message": f"Processing failed: {exc}"}
            yield {"type": "done", "chat_id": chat_id, "cited_chunks": []}
        finally:
            if sem_name and sem_acquired and classification.priority > Priority.NAVIGATION:
                service_semaphores.release(sem_name)
            total_ms = int((time.perf_counter() - t0) * 1000)
            request_metrics.record_response(total_ms, "total")

    def _semaphore_name(self, action: str) -> str:
        mapping = {
            "rag": "chroma",
            "llm": "llm",
            "connector": "postgres",
        }
        return mapping.get(action, "postgres")


admission_controller = AdmissionController()
