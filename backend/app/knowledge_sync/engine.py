from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.knowledge_sync.dedup import SyncManifest
from app.knowledge_sync.fetcher import Fetcher, _is_approved
from app.models.sync_source import SyncSource
from app.utils.logging import log

_SYNC_LOCK = asyncio.Lock()


class SyncEngine:
    """Orchestrates: discover -> download -> dedup -> ingest -> manifest update.

    Ingestion now uses the background upload pipeline (UploadJob + BackgroundWorker)
    so downloaded files are processed asynchronously with real-time SSE status.
    """

    def __init__(self, db: Session):
        self.db = db
        self.fetcher = Fetcher()
        self.manifest = SyncManifest()

    def run(self, urls: list[str] | None = None, *, auto_discover: bool = False) -> dict[str, Any]:
        """Synchronous entry point (kept for backward compatibility).

        Safe to call from both sync and async contexts. When called from an
        already-running event loop, the coroutine is run in that loop via
        run_coroutine_threadsafe.
        """
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                import threading
                result: list[dict[str, Any]] = []
                exception: list[Exception] = []

                def _run_in_thread():
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    try:
                        r = new_loop.run_until_complete(self.run_async(urls, auto_discover=auto_discover))
                        result.append(r)
                    except Exception as e:
                        exception.append(e)
                    finally:
                        new_loop.close()

                thread = threading.Thread(target=_run_in_thread, daemon=True)
                thread.start()
                thread.join()
                if exception:
                    raise exception[0]
                return result[0] if result else {}
        except RuntimeError:
            pass
        return asyncio.run(self.run_async(urls, auto_discover=auto_discover))

    async def run_async(self, urls: list[str] | None = None, *, auto_discover: bool = False) -> dict[str, Any]:
        """Async entry point. Prefer this in async routes."""
        async with _SYNC_LOCK:
            return await self._sync(urls, auto_discover=auto_discover)

    async def _sync(self, urls: list[str] | None, *, auto_discover: bool) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "downloaded": 0,
            "duplicates": 0,
            "failed": 0,
            "ingested": 0,
            "pending_review": 0,
            "upload_ids": [],
            "files": [],
        }

        # Phase 1: Discover or use provided URLs
        if auto_discover:
            results = await self.fetcher.discover_and_fetch()
        elif urls:
            approved = [u for u in urls if _is_approved(u)]
            if not approved:
                return {**stats, "error": "No approved URLs provided"}
            results = await self.fetcher.fetch(approved)
        else:
            return {**stats, "error": "No URLs and auto_discover=False"}

        # Phase 2: Dedup + persist to DB + submit upload jobs
        for res in results:
            if not res.get("success"):
                stats["failed"] += 1
                stats["files"].append({"url": res["url"], "status": "failed", "error": res.get("error")})
                continue

            url = res["url"]
            path = res["path"]
            data = Path(path).read_bytes()

            if self.manifest.is_duplicate(url, data):
                log.info("Duplicate detected, skipping %s", url)
                stats["duplicates"] += 1
                stats["files"].append({"url": url, "status": "duplicate"})
                continue

            # Register in manifest
            entry = self.manifest.register(
                url, path, data,
                source="sync",
                category=self._categorize(url),
                year=None,
            )

            # Persist SyncSource row
            sync_source = SyncSource(
                url=url,
                filename=res.get("filename"),
                category=entry["category"],
                year=entry["year"],
                sha256=entry["hash"],
                status="downloaded",
                file_size=len(data),
                source="sync",
            )
            self.db.add(sync_source)
            self.db.commit()
            stats["downloaded"] += 1
            stats["files"].append({"url": url, "status": "downloaded", "id": sync_source.id})

            # Ingest via background pipeline unless review mode is on
            if not settings.KNOWLEDGE_SYNC_REVIEW_MODE:
                upload_id = await self._submit_upload_job(
                    sync_source, res.get("filename") or Path(path).name, path, data,
                )
                if upload_id:
                    stats["upload_ids"].append(upload_id)
                    sync_source.status = "processing"
                    self.db.commit()
                else:
                    stats["failed"] += 1

        stats["pending_review"] = len(self.manifest.pending_review())
        stats["total_files"] = len(self.manifest.files)
        log.info(
            "Sync complete: %d downloaded, %d duplicates, %d failed, %d uploads submitted",
            stats["downloaded"], stats["duplicates"], stats["failed"], len(stats["upload_ids"]),
        )
        return stats

    async def _submit_upload_job(
        self, sync_source: SyncSource, filename: str, file_path: str, data: bytes
    ) -> str | None:
        """Submit a downloaded file as a background upload job."""
        try:
            from app.database import SessionLocal
            from app.ingest.service import submit_upload_job

            db = SessionLocal()
            try:
                result = await submit_upload_job(
                    db, None, filename, data,
                    title=f"[Sync] {filename}",
                    metadata={
                        "sync_source_id": str(sync_source.id),
                        "sync_url": sync_source.url,
                    },
                )
                return result.get("upload_id")
            finally:
                db.close()
        except Exception as exc:
            log.warning("Failed to submit upload job for %s: %s", sync_source.url, exc)
            sync_source.status = "failed"
            sync_source.error = str(exc)[:500]
            self.db.commit()
            return None

    def _categorize(self, url: str) -> str:
        path = url.lower()
        if "syllabus" in path:
            return "Syllabus"
        if "admission" in path or "prospectus" in path:
            return "Admissions"
        if "notice" in path or "notification" in path:
            return "Notices"
        if "exam" in path or "result" in path:
            return "Examinations"
        if "fee" in path:
            return "Fee_Structure"
        if "scholarship" in path:
            return "Scholarships"
        if "previous" in path or "paper" in path:
            return "Previous_Papers"
        if "act" in path or "statute" in path:
            return "Act_Statutes"
        return "General"

    def approve_for_ingestion(self, sync_id: str) -> dict[str, Any]:
        """Admin review: approve a synced file for background ingestion.

        Safe to call from both sync and async contexts.
        """
        src = self.db.query(SyncSource).filter(SyncSource.id == sync_id).first()
        if not src:
            return {"error": "Sync source not found"}
        entry = self.manifest.files.get(src.url)
        if not entry or not Path(entry["path"]).exists():
            return {"error": "File no longer available"}
        src.status = "reviewed"
        self.db.commit()
        self.manifest.mark_reviewed(src.url)
        data = Path(entry["path"]).read_bytes()
        filename = src.filename or Path(entry["path"]).name

        upload_id = self._run_async(self._submit_upload_job(src, filename, entry["path"], data))
        return {
            "status": "processing" if upload_id else "failed",
            "upload_id": upload_id,
            "id": sync_id,
        }

    @staticmethod
    def _run_async(coro) -> Any:
        """Run a coroutine from a sync context, safe even with a running loop."""
        import threading

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                result: list[Any] = []
                def _run():
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    try:
                        result.append(new_loop.run_until_complete(coro))
                    finally:
                        new_loop.close()
                thread = threading.Thread(target=_run, daemon=True)
                thread.start()
                thread.join()
                return result[0] if result else None
        except RuntimeError:
            pass
        return asyncio.run(coro)

    def get_status(self) -> dict[str, Any]:
        """Return current sync status."""
        total = self.db.query(SyncSource).count()
        ingested = self.db.query(SyncSource).filter(SyncSource.status == "ingested").count()
        downloaded = self.db.query(SyncSource).filter(SyncSource.status == "downloaded").count()
        reviewed = self.db.query(SyncSource).filter(SyncSource.status == "reviewed").count()
        failed = self.db.query(SyncSource).filter(SyncSource.status == "failed").count()
        return {
            "total": total,
            "ingested": ingested,
            "downloaded": downloaded,
            "reviewed": reviewed,
            "failed": failed,
            "manifest": self.manifest.stats,
        }

    def list_sources(
        self, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = self.db.query(SyncSource).order_by(SyncSource.created_at.desc()).limit(limit)
        if status:
            query = query.filter(SyncSource.status == status)
        return [s.to_dict() for s in query.all()]
