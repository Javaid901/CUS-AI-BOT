from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

from app.ingest.embed_cache import EmbeddingCache
from app.ingest.embed_cache import cache as _embed_cache
from app.ingest.job_manager import JobManager
from app.ingest.job_manager import job_manager as _job_manager
from app.ingest.sse import SSEManager
from app.ingest.sse import sse_manager as _sse_manager
from app.utils.files import extract_text
from app.utils.logging import log


class BackgroundWorker:
    """Async background worker that processes upload jobs from a queue."""

    def __init__(
        self,
        job_manager: JobManager | None = None,
        sse: SSEManager | None = None,
        embed_cache: EmbeddingCache | None = None,
        max_concurrent: int = 2,
    ):
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None
        self._job_manager = job_manager or _job_manager
        self._sse = sse or _sse_manager
        self._embed_cache = embed_cache or _embed_cache
        self._max_concurrent = max_concurrent
        self._sem: asyncio.Semaphore | None = None

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._sem = asyncio.Semaphore(self._max_concurrent)
        self._task = asyncio.create_task(self._loop())
        log.info(
            "Background worker started (max_concurrent=%d)", self._max_concurrent
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Background worker stopped")

    async def enqueue(self, upload_id: str) -> None:
        await self._job_manager.update(upload_id, enqueued_at=datetime.now(timezone.utc), enqueued_at_mono=time.monotonic())
        await self._queue.put(upload_id)

    async def _loop(self) -> None:
        while self._running:
            try:
                upload_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                async with self._sem:
                    await self._process_job(upload_id)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.exception("Worker loop unhandled error: %s", exc)

    async def _process_job(self, upload_id: str) -> None:
        job = await self._job_manager.get(upload_id)
        if not job:
            log.warning("Worker: job %s not found", upload_id)
            return

        if job.cancelled:
            return

        t_start = time.monotonic()
        queue_wait = (t_start - job.enqueued_at_mono) * 1000 if job.enqueued_at_mono else 0.0
        await self._job_manager.update(
            upload_id,
            status="extracting",
            started_at=datetime.now(timezone.utc),
            current_stage="extracting",
            progress=5,
        )
        await self._publish(upload_id, "processing", {"progress": 5})

        db = None
        doc = None
        try:
            from app.database import SessionLocal

            db = SessionLocal()
            from app.models import Document

            doc = db.query(Document).filter(Document.id == job.document_id).first()
            if doc:
                doc.status = "processing"
                db.commit()

            await self._publish(upload_id, "saved", {"progress": 10})

            # Step 1: Extract text
            if job.cancelled:
                return
            await self._job_manager.update(
                upload_id, current_stage="extracting", progress=15
            )
            await self._publish(upload_id, "extracting", {"progress": 15})
            ext = Path(job.file_path).suffix.lstrip(".").lower()
            pages = await asyncio.to_thread(extract_text, job.file_path, ext)
            await self._job_manager.update(upload_id, progress=25)
            await self._publish(
                upload_id,
                "extracted",
                {"progress": 25, "pages": len(pages)},
            )

            if job.cancelled:
                return

            # Step 2: Chunk
            t_chunk = time.monotonic()
            await self._job_manager.update(
                upload_id, current_stage="chunking", progress=30
            )
            await self._publish(upload_id, "chunking", {"progress": 30})
            from app.ingest.chunker import chunk_pages

            chunks = await asyncio.to_thread(chunk_pages, pages)
            if not chunks:
                raise ValueError("No extractable text found in document.")
            chunk_time = (time.monotonic() - t_chunk) * 1000
            await self._job_manager.update(
                upload_id,
                current_stage="chunked",
                progress=45,
                chunk_time_ms=chunk_time,
            )
            await self._publish(
                upload_id,
                "chunked",
                {"progress": 45, "chunks": len(chunks)},
            )

            if job.cancelled:
                return

            # Step 3: Embed with cache
            t_embed = time.monotonic()
            await self._job_manager.update(
                upload_id, current_stage="embedding", progress=50
            )
            await self._publish(upload_id, "embedding", {"progress": 50})
            texts = [c["content"] for c in chunks]
            from app.ingest.embed import embed_documents_with_cache

            embeddings = await embed_documents_with_cache(texts, self._embed_cache)
            embed_time = (time.monotonic() - t_embed) * 1000
            await self._job_manager.update(
                upload_id,
                current_stage="embedded",
                progress=75,
                embed_time_ms=embed_time,
            )
            await self._publish(
                upload_id,
                "embedded",
                {"progress": 75, "embeddings": len(embeddings)},
            )

            if job.cancelled:
                return

            # Step 4: Store in Chroma
            t_store = time.monotonic()
            await self._job_manager.update(
                upload_id, current_stage="indexing", progress=80
            )
            await self._publish(upload_id, "indexing", {"progress": 80})

            doc_title = doc.title if doc else (job.filename or "Document")
            doc_id = str(doc.id) if doc else job.document_id
            from app.ingest.store import add_chunks_with_embeddings

            await asyncio.wait_for(
                asyncio.to_thread(add_chunks_with_embeddings, doc_id, doc_title, chunks, embeddings),
                timeout=30.0,
            )
            store_time = (time.monotonic() - t_store) * 1000
            await self._job_manager.update(
                upload_id,
                current_stage="indexed",
                progress=95,
                store_time_ms=store_time,
            )
            await self._publish(upload_id, "indexed", {"progress": 95})

            # Update document
            if doc:
                doc.status = "ready"
                doc.chunk_count = len(chunks)
                doc.error = None
                db.commit()

            # If this was a Knowledge Sync job, update the SyncSource
            if job.metadata and job.metadata.get("sync_source_id"):
                try:
                    from app.models.sync_source import SyncSource
                    src = db.query(SyncSource).filter(
                        SyncSource.id == job.metadata["sync_source_id"]
                    ).first()
                    if src:
                        src.status = "ingested"
                        src.document_id = doc_id
                        src.error = None
                        db.commit()
                except Exception as exc:
                    log.warning(
                        "Could not update SyncSource for job %s: %s",
                        upload_id, exc,
                    )

            total_time = (time.monotonic() - t_start) * 1000
            await self._job_manager.mark_completed(
                upload_id,
                document_id=doc_id,
                chunks_count=len(chunks),
                metrics={
                    "upload_time_ms": 0,
                    "chunk_time_ms": chunk_time,
                    "embed_time_ms": embed_time,
                    "store_time_ms": store_time,
                    "total_time_ms": total_time,
                    "queue_wait_ms": queue_wait,
                },
            )
            await self._publish(
                upload_id,
                "completed",
                {
                    "progress": 100,
                    "document_id": doc_id,
                    "chunks": len(chunks),
                    "total_time_ms": total_time,
                },
            )
            log.info(
                "Worker: job %s completed (%d chunks, %.0fms)",
                upload_id,
                len(chunks),
                total_time,
            )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Worker: job %s failed: %s", upload_id, exc)
            await self._job_manager.mark_failed(upload_id, str(exc))
            await self._publish(upload_id, "failed", {"error": str(exc)[:300]})
            if doc:
                try:
                    doc.status = "failed"
                    doc.error = str(exc)[:500]
                    if db:
                        db.commit()
                except Exception:
                    pass
        finally:
            if db:
                try:
                    await asyncio.to_thread(self._embed_cache.save_sync)
                except Exception:
                    pass
                db.close()

    async def _publish(self, upload_id: str, event: str, data: dict) -> None:
        try:
            await self._sse.publish(upload_id, event, data)
        except Exception:
            pass


worker = BackgroundWorker()
