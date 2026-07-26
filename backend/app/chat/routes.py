"""
backend/app/chat/routes.py

Chat endpoint — thin SSE layer over the Orchestration Engine,
now fronted by the Admission Controller for intelligent request management.

POST /api/chat/ask
  Body: {"message": str, "chat_id": str|null, "stream": bool}
  Auth: Bearer JWT

Response: Server-Sent Events stream:
  event: queued\ndata: {...}               (request queued)
  event: processing\ndata: {...}           (processing started)
  data: <token>\n\n                        (LLM streaming token)
  event: options\ndata: {...}\n\n           (structured navigation options)
  event: detail\ndata: {...}\n\n            (structured detail card)
  event: auth_form\ndata: {...}\n\n         (student login form)
  event: done\ndata: {"chat_id":"...",...}\n\n (end of response)
  event: error\ndata: {"message":"..."}\n\n  (error)
"""

from __future__ import annotations

import json
import uuid

from app.auth.security import get_current_user
from app.chat.intent_router import get_nav_path, set_nav_path
from app.config import settings
from app.database import get_db
from app.models import User
from app.orchestrator.engine import process
from app.request_manager import admission_controller
from app.utils.logging import audit
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix=f"{settings.API_PREFIX}/chat", tags=["chat"])


class AskRequest(BaseModel):
    message: str
    chat_id: str | None = None
    stream: bool = True


def _sse(event: str | None, data: str) -> str:
    if event:
        return f"event: {event}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


def _structured_event(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


@router.post("/ask")
async def ask(
    body: AskRequest,
    request: Request,
    db=Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not body.message or not body.message.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    chat_id = body.chat_id or ""
    if not chat_id:
        chat_id = "anon_" + uuid.uuid4().hex[:12]
    message = body.message.strip()
    client_ip = request.client.host if request.client else None
    user_id = str(current_user.id)
    user_role = current_user.role

    async def _run_orchestrator(uid: str, msg: str, cid: str):
        """Wrapper that binds the DB session into the orchestrator."""
        async for event in process(db, uid, msg, cid):
            yield event

    async def event_stream():
        try:
            # Wrap the orchestrator with admission control
            async for event in admission_controller.admit(
                user_id=user_id,
                message=message,
                chat_id=chat_id,
                executor=_run_orchestrator,
            ):
                etype = event["type"]

                # Admission controller events
                if etype == "queued":
                    yield _sse("queued", json.dumps({
                        "position": event.get("position"),
                        "estimated_wait_sec": event.get("estimated_wait_sec"),
                        "action": event.get("action"),
                    }))
                    continue

                if etype == "processing":
                    yield _sse("processing", json.dumps({
                        "action": event.get("action"),
                    }))
                    continue

                # Orchestrator events (delegated to process())
                if etype == "token":
                    yield _sse(None, event["text"])

                elif etype in ("options", "detail", "auth_form"):
                    yield _structured_event(etype, event)
                    audit(
                        db, "chat",
                        actor_id=user_id,
                        actor_role=user_role,
                        detail=f"[{etype}] {message}",
                        ip=client_ip,
                    )

                elif etype == "done":
                    # Migrate nav state from anonymous session to real conversation.
                    real_id = event.get("chat_id", chat_id)
                    if chat_id.startswith("anon_") and real_id != chat_id:
                        nav_path = get_nav_path(chat_id)
                        if nav_path:
                            set_nav_path(real_id, nav_path)
                    yield _sse("done", json.dumps(event))

                elif etype == "error":
                    yield _sse("error", json.dumps({"message": event["message"]}))

        except Exception as exc:
            yield _sse("error", json.dumps({"message": str(exc)}))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
