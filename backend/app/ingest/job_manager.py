from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class UploadJob:
    upload_id: str
    status: str = "queued"
    progress: float = 0.0
    current_stage: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    filename: str = ""
    file_size: int = 0
    file_path: str = ""
    error: str | None = None
    document_id: str | None = None
    chunks_count: int = 0
    is_duplicate: bool = False
    sha256: str = ""
    source: str = "upload"
    upload_time_ms: float = 0.0
    chunk_time_ms: float = 0.0
    embed_time_ms: float = 0.0
    store_time_ms: float = 0.0
    total_time_ms: float = 0.0
    queue_wait_ms: float = 0.0
    cancelled: bool = False
    enqueued_at: datetime | None = None
    enqueued_at_mono: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "upload_id": self.upload_id,
            "status": self.status,
            "progress": self.progress,
            "current_stage": self.current_stage,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "filename": self.filename,
            "file_size": self.file_size,
            "error": self.error,
            "document_id": self.document_id,
            "chunks_count": self.chunks_count,
            "is_duplicate": self.is_duplicate,
            "sha256": self.sha256,
            "source": self.source,
            "upload_time_ms": self.upload_time_ms,
            "chunk_time_ms": self.chunk_time_ms,
            "embed_time_ms": self.embed_time_ms,
            "store_time_ms": self.store_time_ms,
            "total_time_ms": self.total_time_ms,
            "queue_wait_ms": self.queue_wait_ms,
            "enqueued_at": self.enqueued_at.isoformat() if self.enqueued_at else None,
            "metadata": self.metadata,
        }


class JobManager:
    """In-memory job registry with async-safe access."""

    def __init__(self):
        self._jobs: dict[str, UploadJob] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        filename: str,
        file_size: int,
        file_path: str,
        sha256: str,
        source: str = "upload",
        document_id: str | None = None,
        metadata: dict | None = None,
    ) -> UploadJob:
        job = UploadJob(
            upload_id=uuid.uuid4().hex,
            filename=filename,
            file_size=file_size,
            file_path=file_path,
            sha256=sha256,
            source=source,
            document_id=document_id,
            metadata=metadata or {},
        )
        async with self._lock:
            self._jobs[job.upload_id] = job
        return job

    async def get(self, upload_id: str) -> UploadJob | None:
        async with self._lock:
            return self._jobs.get(upload_id)

    async def update(self, upload_id: str, **kwargs) -> None:
        async with self._lock:
            job = self._jobs.get(upload_id)
            if job:
                for k, v in kwargs.items():
                    setattr(job, k, v)

    async def cancel(self, upload_id: str) -> bool:
        async with self._lock:
            job = self._jobs.get(upload_id)
            if job and job.status in (
                "queued", "saving", "saved", "extracting",
                "chunking", "embedding", "indexing",
            ):
                job.cancelled = True
                job.status = "cancelled"
                job.finished_at = datetime.now(timezone.utc)
                return True
            return False

    async def mark_completed(
        self, upload_id: str, document_id: str, chunks_count: int, metrics: dict
    ) -> None:
        async with self._lock:
            job = self._jobs.get(upload_id)
            if job:
                job.status = "completed"
                job.progress = 100.0
                job.finished_at = datetime.now(timezone.utc)
                job.document_id = document_id
                job.chunks_count = chunks_count
                for k, v in metrics.items():
                    setattr(job, k, v)

    async def mark_failed(self, upload_id: str, error: str) -> None:
        async with self._lock:
            job = self._jobs.get(upload_id)
            if job:
                job.status = "failed"
                job.error = error[:500]
                job.finished_at = datetime.now(timezone.utc)

    async def list_jobs(self, limit: int = 50) -> list[dict]:
        async with self._lock:
            sorted_jobs = sorted(
                self._jobs.values(), key=lambda j: j.created_at, reverse=True
            )
            return [j.to_dict() for j in sorted_jobs[:limit]]

    async def get_metrics(self) -> dict:
        async with self._lock:
            total = len(self._jobs)
            completed = sum(1 for j in self._jobs.values() if j.status == "completed")
            failed = sum(1 for j in self._jobs.values() if j.status == "failed")
            cancelled = sum(1 for j in self._jobs.values() if j.status == "cancelled")
            queued = sum(1 for j in self._jobs.values() if j.status == "queued")
            running = sum(
                1
                for j in self._jobs.values()
                if j.status
                in ("saving", "extracting", "chunking", "embedding", "indexing")
            )
            avg_upload = 0.0
            avg_chunk = 0.0
            avg_embed = 0.0
            avg_store = 0.0
            avg_total = 0.0
            done = [j for j in self._jobs.values() if j.status == "completed"]
            if done:
                avg_upload = sum(j.upload_time_ms for j in done) / len(done)
                avg_chunk = sum(j.chunk_time_ms for j in done) / len(done)
                avg_embed = sum(j.embed_time_ms for j in done) / len(done)
                avg_store = sum(j.store_time_ms for j in done) / len(done)
                avg_total = sum(j.total_time_ms for j in done) / len(done)
            return {
                "total_jobs": total,
                "completed": completed,
                "failed": failed,
                "cancelled": cancelled,
                "queued": queued,
                "running": running,
                "avg_upload_time_ms": round(avg_upload, 1),
                "avg_chunk_time_ms": round(avg_chunk, 1),
                "avg_embed_time_ms": round(avg_embed, 1),
                "avg_store_time_ms": round(avg_store, 1),
                "avg_total_time_ms": round(avg_total, 1),
            }

    def clean_old_jobs(self, max_age_hours: int = 24) -> int:
        now = datetime.now(timezone.utc)
        to_remove = []
        for uid, job in list(self._jobs.items()):
            if job.finished_at and (now - job.finished_at).total_seconds() > max_age_hours * 3600:
                to_remove.append(uid)
        for uid in to_remove:
            del self._jobs[uid]
        return len(to_remove)


job_manager = JobManager()
