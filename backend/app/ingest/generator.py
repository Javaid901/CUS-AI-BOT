"""
backend/app/ingest/generator.py

LLM generation via Ollama, with streaming token output.

The generator is strictly grounded: it receives retrieved context and the system
prompt that forbids hallucination. If no context is retrieved, the caller should
short-circuit with the fallback message (see chat service) so the LLM is never
asked to invent an answer.
"""

from __future__ import annotations

import json
import threading

import httpx
from app.config import settings
from app.ingest.prompts import CONTEXT_TEMPLATE, SYSTEM_PROMPT
from app.utils.logging import log

_GEN_TIMEOUT = 180.0
_HTTP_CLIENT: httpx.Client | None = None
_HTTP_LOCK = threading.Lock()


class GenerationError(Exception):
    pass


def _get_client() -> httpx.Client:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        with _HTTP_LOCK:
            if _HTTP_CLIENT is None:
                _HTTP_CLIENT = httpx.Client(timeout=_GEN_TIMEOUT)
    return _HTTP_CLIENT


def _build_payload(question: str, context: str) -> dict:
    prompt = CONTEXT_TEMPLATE.format(context=context, question=question)
    return {
        "model": settings.LLM_MODEL,
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "stream": True,
        "keep_alive": f"{settings.OLLAMA_KEEP_ALIVE}s",
        "options": {
            "temperature": settings.LLM_TEMPERATURE,
            "top_p": settings.LLM_TOP_P,
            "num_predict": settings.LLM_MAX_TOKENS,
        },
    }


def stream_answer(question: str, context: str):
    """
    Yield tokens (strings) from the Ollama streaming endpoint.
    Raises GenerationError on connection/HTTP failure.

    Sync variant — for CLI/standalone use. The async SSE path must use
    stream_answer_async so per-token network reads never block the event loop.
    """
    payload = _build_payload(question, context)
    client = _get_client()
    try:
        with client.stream(
            "POST", f"{settings.OLLAMA_BASE_URL}/api/generate", json=payload
        ) as resp:
            if resp.status_code != 200:
                raise GenerationError(f"Ollama returned HTTP {resp.status_code}")
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("done"):
                    return
                token = obj.get("response")
                if token:
                    yield token
    except httpx.HTTPError as exc:
        raise GenerationError(f"Ollama request failed: {exc}") from exc


async def stream_answer_async(question: str, context: str):
    """
    Async twin of stream_answer — yields tokens without blocking the loop.

    Uses its own AsyncClient per call so the pooled sync client and its lock
    stay untouched; an async context manager guarantees connection cleanup.
    Raises GenerationError on connection/HTTP failure.
    """
    payload = _build_payload(question, context)
    try:
        async with httpx.AsyncClient(timeout=_GEN_TIMEOUT) as client:
            async with client.stream(
                "POST", f"{settings.OLLAMA_BASE_URL}/api/generate", json=payload
            ) as resp:
                if resp.status_code != 200:
                    raise GenerationError(f"Ollama returned HTTP {resp.status_code}")
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("done"):
                        return
                    token = obj.get("response")
                    if token:
                        yield token
    except httpx.HTTPError as exc:
        raise GenerationError(f"Ollama request failed: {exc}") from exc


def is_ollama_available() -> bool:
    try:
        client = _get_client()
        r = client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
        return r.status_code == 200
    except Exception as exc:
        log.warning("Ollama availability check failed: %s", exc)
        return False


def list_models() -> list[str]:
    try:
        client = _get_client()
        r = client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return []
