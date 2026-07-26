from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator


class SSEManager:
    """Publish / subscribe for real-time job events via Server-Sent Events."""

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._global_subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def subscribe(self, upload_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.setdefault(upload_id, []).append(queue)
        return queue

    async def subscribe_global(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        async with self._lock:
            self._global_subscribers.append(queue)
        return queue

    async def unsubscribe(self, upload_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            queues = self._subscribers.get(upload_id)
            if queues:
                self._subscribers[upload_id] = [q for q in queues if q is not queue]
                if not self._subscribers[upload_id]:
                    del self._subscribers[upload_id]

    async def unsubscribe_global(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._global_subscribers = [q for q in self._global_subscribers if q is not queue]

    async def publish(self, upload_id: str, event: str, data: dict) -> None:
        async with self._lock:
            job_queues = list(self._subscribers.get(upload_id, []))
            global_queues = list(self._global_subscribers)
        msg = {"event": event, "data": data}
        for q in job_queues:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass
        for q in global_queues:
            try:
                q.put_nowait({**msg, "upload_id": upload_id})
            except asyncio.QueueFull:
                pass

    async def event_generator(self, upload_id: str) -> AsyncGenerator[str, None]:
        queue = await self.subscribe(upload_id)
        try:
            yield f"event: connected\ndata: {json.dumps({'upload_id': upload_id})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    payload = json.dumps(msg["data"])
                    yield f"event: {msg['event']}\ndata: {payload}\n\n"
                    if msg["event"] in ("completed", "failed", "cancelled"):
                        break
                except asyncio.TimeoutError:
                    yield "event: keepalive\ndata: {}\n\n"
        finally:
            await self.unsubscribe(upload_id, queue)

    async def global_event_generator(self) -> AsyncGenerator[str, None]:
        queue = await self.subscribe_global()
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    payload = json.dumps(msg["data"])
                    yield f"event: {msg['event']}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield "event: keepalive\ndata: {}\n\n"
        finally:
            await self.unsubscribe_global(queue)


sse_manager = SSEManager()
