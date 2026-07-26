"""
backend/app/request_manager/__init__.py

Production-grade Intelligent Request Management Layer.

Replaces the simplistic sliding-window rate limiter with an Admission Controller
that classifies requests, assigns priority, estimates cost, manages a token
bucket, queues work when necessary, protects backend resources with semaphores,
and provides real-time monitoring metrics.

NOTE: request_queue and worker_pool are not re-exported here to avoid module
shadowing. Import them directly:
  from app.request_manager.request_queue import request_queue
  from app.request_manager.worker_pool import worker_pool
"""

from __future__ import annotations

from app.request_manager.admission_controller import (
    AdmissionController,
    admission_controller,
)
from app.request_manager.metrics import RequestMetrics, request_metrics
from app.request_manager.models import (
    Classification,
    Priority,
    RequestCost,
    RequestState,
)
from app.request_manager.priority_scheduler import classify_request
from app.request_manager.response_cache import ResponseCache, response_cache
from app.request_manager.service_semaphores import ServiceSemaphores, service_semaphores
from app.request_manager.token_bucket import TokenBucket, token_bucket

__all__ = [
    "AdmissionController",
    "Classification",
    "Priority",
    "RequestCost",
    "RequestMetrics",
    "RequestState",
    "ResponseCache",
    "ServiceSemaphores",
    "TokenBucket",
    "admission_controller",
    "classify_request",
    "request_metrics",
    "response_cache",
    "service_semaphores",
    "token_bucket",
]
