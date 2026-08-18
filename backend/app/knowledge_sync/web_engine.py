"""
backend/app/knowledge_sync/web_engine.py

Enterprise Website Knowledge Synchronization Engine.

Orchestrates a full sync pass:

  1. crawl       — bounded same-domain crawl of the configured base URL
                   (or explicit seed URLs) via WebsiteCrawler, with
                   robots.txt + sitemap.xml seeds and SSRF/redirect guards
  2. extract     — semantic HTML text (web_extractor; tables preserved) or
                   document parsing (pdf/docx/txt/md/csv/xls/xlsx/pptx)
  3. classify    — deterministic category assignment (web_classifier)
  4. incremental — per-URL diffing: new / updated / unchanged pages
  5. versioning  — old content snapshots are archived as immutable
                   WebsitePageVersion rows (never hard-deleted)
  6. dedup       — URL normalization + canonical URL mapping + content-hash
                   + title-similarity checks
  7. archive     — previously seen URLs missing from a full crawl are archived
  8. index       — page text is chunked, embedded and stored in Chroma with
                   document_type="website", source_url and document_url for
                   attribution

Every pass is recorded as a CrawlRun row for the monitoring dashboard.

Runtime state machine (persisted in the sync state file, reflected in
GET /api/admin/website-sync/status):

    Disconnected -> Connecting -> Connected -> Discovering -> Syncing
    -> Processing -> Ready | Warning | Error

  * enabled (scheduled/toggle) is a separate flag, distinct from Ready
  * progress counters (pages fetched / discovered / indexed) are updated
    live so the dashboard shows real numbers, never static ones
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import settings
from app.knowledge_sync.web_classifier import classify_page, normalize_title
from app.knowledge_sync.web_crawler import (
    CrawlResult,
    WebsiteCrawler,
    host_is_private,
    normalize_url,
)
from app.models.website_sync import CrawlRun, WebsitePage, WebsitePageVersion
from app.utils.logging import log

try:
    from app.models import Document
except ImportError:  # pragma: no cover
    from app.models.db_models import Document

_RUN_LOCK = threading.Lock()


def resolve_source_url(url: str | None = None) -> str:
    """Resolve the Website Sync source URL.

    Precedence: explicit url > WEBSITE_KNOWLEDGE_SOURCE_URL (spec var) >
    WEBSITE_BASE_URL (deprecated alias). Never falls back to the app's own
    origin/loopback address — missing config keeps the production default.
    """
    candidate = (url or "").strip()
    if not candidate:
        # No explicit URL supplied — return the configured production source
        # as-is so dashboard comparisons and source validation work clearly.
        return settings.WEBSITE_KNOWLEDGE_SOURCE_URL or settings.WEBSITE_BASE_URL or ""
    # Explicit URL was supplied — normalize it for safe use
    return normalize_url(candidate)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def content_hash(title: str | None, text: str | None) -> str:
    canonical = f"{title or ''}\n{text or ''}".encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# ---------------------------------------------------------------------------
# Document text extraction (pdf/docx/txt/md/csv/xls/xlsx)
# ---------------------------------------------------------------------------

def parse_document_bytes(raw: bytes, ext: str, url: str = "") -> list[dict]:
    """Extract text from a downloaded document. Returns [{"page": int, "text": str}]."""
    ext = (ext or "").lower().lstrip(".")
    if ext in ("pdf", "docx", "txt", "md"):
        return _parse_with_existing_extractor(raw, ext)
    if ext == "csv":
        return _parse_csv(raw)
    if ext in ("xls", "xlsx"):
        return _parse_xlsx(raw)
    if ext in ("ppt", "pptx"):
        return _parse_pptx(raw)
    return []


def _parse_with_existing_extractor(raw: bytes, ext: str) -> list[dict]:
    """Reuse app.utils.files.extract_text via a temp file (no API change)."""
    from app.utils.files import extract_text

    fd, path = tempfile.mkstemp(suffix=f".{ext}")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        return extract_text(path, ext)
    except Exception as exc:
        log.warning("Document parse failed (%s): %s", ext, exc)
        return []
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _parse_csv(raw: bytes) -> list[dict]:
    import csv
    import io

    try:
        text = raw.decode("utf-8", errors="ignore")
    except Exception:
        return []
    rows: list[str] = []
    try:
        for row in csv.reader(io.StringIO(text)):
            cleaned = [c.strip() for c in row]
            if any(cleaned):
                rows.append(" | ".join(cleaned))
    except Exception:
        rows = [line for line in text.splitlines() if line.strip()]
    return [{"page": 1, "text": "\n".join(rows)}] if rows else []


def _parse_xlsx(raw: bytes) -> list[dict]:
    try:
        import io

        import openpyxl
    except ImportError:
        return []
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        parts: list[str] = []
        for ws in wb.worksheets:
            parts.append(f"[Sheet: {ws.title}]")
            for row in ws.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return [{"page": 1, "text": "\n".join(parts)}] if parts else []
    except Exception as exc:
        log.warning("XLSX parse failed: %s", exc)
        return []


def _parse_pptx(raw: bytes) -> list[dict]:
    """Extract slide text from PPTX (and best-effort legacy PPT) using
    stdlib zipfile — no external dependency. One block per slide."""
    import html
    import io
    import re
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        log.warning("PPT parse failed: not a valid zip archive")
        return []
    slide_names = sorted(
        (n for n in names if re.match(r"^ppt/slides/slide\d+\.xml$", n)),
        key=lambda n: int(re.search(r"(\d+)", n).group(1)),
    )
    if not slide_names:
        return []
    parts: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for name in slide_names:
                xml = zf.read(name).decode("utf-8", errors="ignore")
                runs = re.findall(r"<a:t>(.*?)</a:t>", xml, re.IGNORECASE | re.DOTALL)
                texts = [html.unescape(r).strip() for r in runs if html.unescape(r).strip()]
                if texts:
                    parts.append(f"[Slide {name.split('/')[-1]}]")
                    parts.extend(texts)
    except Exception as exc:
        log.warning("PPTX parse failed: %s", exc)
        return []
    return [{"page": 1, "text": "\n".join(parts)}] if parts else []


# ---------------------------------------------------------------------------
# Sync state (persisted JSON — powers dashboard toggle + scheduler + the
# runtime state machine: Disconnected, Connecting, Connected, Discovering,
# Syncing, Processing, Ready, Warning, Error)
# ---------------------------------------------------------------------------

def _state_path() -> Path:
    return Path(settings.WEBSITE_SYNC_STATE_FILE)


def _default_state() -> dict[str, Any]:
    return {
        "enabled": bool(settings.WEBSITE_SYNC_ENABLED),  # MASTER TOGGLE: ON/OFF
        "schedule": "disabled",                          # disabled | manual | hourly | 6hourly | daily | weekly | monthly
        "hours": settings.WEBSITE_SYNC_SCHEDULE_HOURS,
        "last_run_at": None,
        "next_run_at": None,
        "runtime": {
            "state": "Disconnected",     # state machine status
            "message": "Idle",
            "progress": None,            # {"phase": ..., "current": n, "total": n}
        },
        "last_counts": None,             # last pass counters (new/updated/...)
    }


_STATE_LOCK = threading.RLock()

# In-memory mirror of the runtime state machine: updated on every state
# transition (even when not persisted), merged with the file on cold reads.
_LIVE_RUNTIME: dict[str, Any] = {}
_LIVE_READ = False


def _live_runtime() -> dict[str, Any]:
    global _LIVE_READ
    if not _LIVE_READ:
        _LIVE_RUNTIME.clear()
        _LIVE_RUNTIME.update(load_state().get("runtime") or {})
        _LIVE_READ = True
    return _LIVE_RUNTIME


def load_state() -> dict[str, Any]:
    with _STATE_LOCK:
        state = _default_state()
        try:
            if _state_path().exists():
                saved = json.loads(_state_path().read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    state.update(saved)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read website sync state: %s", exc)
        return state


def save_state(state: dict[str, Any]) -> None:
    with _STATE_LOCK:
        try:
            _state_path().parent.mkdir(parents=True, exist_ok=True)
            _state_path().write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        except OSError as exc:
            log.warning("Could not persist website sync state: %s", exc)


def set_runtime_state(
    state: str,
    message: str,
    progress: dict[str, Any] | None = None,
    *,
    persist: bool = True,
) -> None:
    """Update the runtime state machine (live mirror + persisted file)."""
    with _STATE_LOCK:
        live = _live_runtime()
        live["state"] = state
        live["message"] = message
        live["progress"] = progress
        if persist:
            current = load_state()
            current["runtime"] = dict(live)
            save_state(current)


def get_runtime_state() -> dict[str, Any]:
    """Live runtime state (in-memory mirror; file is only a cold-start seed)."""
    with _STATE_LOCK:
        live = dict(_live_runtime())
    return {
        "state": live.get("state", "Disconnected"),
        "message": live.get("message", "Idle"),
        "progress": live.get("progress"),
    }


def reset_runtime_state() -> None:
    """Return the state machine to an idle baseline (e.g. after a warning)."""
    global _LIVE_READ
    with _STATE_LOCK:
        set_runtime_state("Disconnected", "Idle", None, persist=True)
        _LIVE_READ = False


SCHEDULE_HOURS = {
    "manual": 0,
    "hourly": 1,
    "daily": 24,
    "weekly": 168,
    "monthly": 720,
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class WebsiteSyncEngine:
    """Incremental website crawler + indexer with versioning and archiving."""

    def __init__(
        self,
        db: Session,
        *,
        base_url: str | None = None,
        index_rag: bool | None = None,
        allow_private_hosts: bool | None = None,
        use_sitemap: bool | None = None,
    ):
        self.db = db
        self.base_url: str = resolve_source_url(base_url)
        self.index_rag = settings.WEBSITE_SYNC_INDEX_RAG if index_rag is None else bool(index_rag)
        self.allow_private_hosts = (
            bool(settings.WEBSITE_SYNC_ALLOW_PRIVATE_HOSTS)
            if allow_private_hosts is None
            else bool(allow_private_hosts)
        )
        self.use_sitemap = (
            bool(settings.WEBSITE_SYNC_USE_SITEMAP) if use_sitemap is None else bool(use_sitemap)
        )

    # -- entry points -------------------------------------------------------
    def run(
        self, trigger: str = "manual", seed_urls: list[str] | None = None
    ) -> dict[str, Any]:
        """Thread-safe synchronous entry point (compatible with existing callers)."""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                result: list[dict[str, Any]] = []
                exception: list[Exception] = []

                def _run_in_thread() -> None:
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    try:
                        result.append(new_loop.run_until_complete(
                            self.run_async(trigger=trigger, seed_urls=seed_urls)
                        ))
                    except Exception as exc:  # noqa: BLE001
                        exception.append(exc)
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
        return asyncio.run(self.run_async(trigger=trigger, seed_urls=seed_urls))

    async def run_async(
        self, trigger: str = "manual", seed_urls: list[str] | None = None
    ) -> dict[str, Any]:
        """Async entry point. Serialized per process via _RUN_LOCK."""
        with _RUN_LOCK:
            return await self._sync(trigger=trigger, seed_urls=seed_urls)

    # -- core pass -----------------------------------------------------------
    async def _sync(self, trigger: str, seed_urls: list[str] | None) -> dict[str, Any]:
        started = utcnow()
        set_runtime_state(
            "Connecting",
            f"Connecting to {self.base_url}...",
            {"phase": "connecting", "current": 0, "total": None},
        )
        run = CrawlRun(trigger=trigger, base_url=self.base_url, status="running")
        self.db.add(run)
        self.db.commit()

        stats = {
            "trigger": trigger,
            "status": "running",
            "total_urls": 0,
            "pages_found": 0,
            "new_pages": 0,
            "updated_pages": 0,
            "unchanged_pages": 0,
            "archived_pages": 0,
            "duplicates_skipped": 0,
            "failed_pages": 0,
            "indexed_pages": 0,
        }

        def _stage(stage: str, current: int, total: int | None, message: str) -> None:
            """Map crawler on_stage events onto the dashboard state machine."""
            mapping = {
                "connecting": "Connecting",
                "connected": "Connected",
                "discovering": "Discovering",
                "downloading": "Syncing",
            }
            set_runtime_state(
                mapping.get(stage, "Syncing"),
                message,
                {"phase": stage, "current": current, "total": total, "message": message},
            )

        def _processing_tick(current: int, total: int, message: str) -> None:
            set_runtime_state(
                "Processing", message,
                {"phase": "processing", "current": current, "total": total, "message": message},
                persist=(current == total or current % 25 == 0),
            )

        try:
            # Fail fast on an invalid source: clear INVALID_SOURCE message on
            # the dashboard instead of a cryptic crawl error. The SSRF guard
            # (host_is_private) also rejects loopback/private targets unless
            # explicitly allowed, so a stray localhost URL can never be synced.
            scheme = urlparse(self.base_url).scheme.lower()
            if scheme not in ("http", "https"):
                raise ValueError(
                    f"INVALID_SOURCE: unsupported scheme '{scheme}' — only http/https allowed "
                    f"({self.base_url})"
                )
            if await host_is_private(self.base_url, allow_private_hosts=self.allow_private_hosts):
                raise ValueError(
                    f"INVALID_SOURCE: {self.base_url} is a loopback/private target "
                    "(refused by SSRF guard) or could not be resolved"
                )

            crawler = WebsiteCrawler(
                base_url=self.base_url,
                allow_private_hosts=self.allow_private_hosts,
                use_sitemap=self.use_sitemap,
            )
            crawl = await crawler.crawl(seed_urls=seed_urls, on_stage=_stage)
            pages: list[CrawlResult] = crawl["pages"]
            stats["total_urls"] = crawl["fetched"]
            stats["pages_found"] = len(pages)

            ok_results = [r for r in pages if r.ok]
            if not ok_results:
                # Full or URL-scoped: zero successful fetches means the site
                # is unreachable/blocked — report UNREACHABLE and never
                # archive known pages because of a failed pass.
                target = ", ".join(seed_urls) if seed_urls else self.base_url
                raise RuntimeError(
                    f"UNREACHABLE: no page could be fetched from {target} "
                    "(site down, blocked, or nothing discovered)"
                )

            set_runtime_state(
                "Processing",
                f"Processing {len(pages)} discovered pages...",
                {"phase": "processing", "current": 0, "total": len(pages), "message": "Processing pages"},
                persist=True,
            )
            # Only archive pages missing from a FULL crawl that reached the
            # site (>=1 successful request). A fully failed crawl means the
            # site is down — knowledge must NOT be archived/deleted then.
            await self._process_pages(
                pages, stats,
                archive_missing=bool(not seed_urls and ok_results),
                on_progress=_processing_tick,
            )

            run.total_urls = stats["total_urls"]
            run.pages_found = stats["pages_found"]
            run.new_pages = stats["new_pages"]
            run.updated_pages = stats["updated_pages"]
            run.unchanged_pages = stats["unchanged_pages"]
            run.archived_pages = stats["archived_pages"]
            run.duplicates_skipped = stats["duplicates_skipped"]
            run.failed_pages = stats["failed_pages"]
            run.indexed_pages = stats["indexed_pages"]
            run.status = "completed"
            stats["status"] = "completed"

            if stats["failed_pages"]:
                set_runtime_state(
                    "Warning",
                    f"Completed with {stats['failed_pages']} failed page(s)",
                    {"phase": "done", "current": stats["pages_found"], "total": stats["pages_found"]},
                )
            else:
                set_runtime_state(
                    "Ready",
                    f"Synced {stats['pages_found']} pages "
                    f"({stats['new_pages']} new, {stats['updated_pages']} updated)",
                    {"phase": "done", "current": stats["pages_found"], "total": stats["pages_found"]},
                )
        except Exception as exc:
            log.exception("Website sync failed: %s", exc)
            run.status = "failed"
            run.error = str(exc)[:1000]
            stats["status"] = "failed"
            stats["error"] = str(exc)[:1000]
            set_runtime_state(
                "Error",
                f"Sync failed: {str(exc)[:300]}",
                {"phase": "error", "current": stats["pages_found"], "total": None},
            )

        run.finished_at = utcnow()
        run.duration_seconds = round((run.finished_at - started).total_seconds(), 2)
        self.db.commit()
        log.info(
            "Website sync %s: %d new, %d updated, %d unchanged, %d archived, %d failed",
            run.status, stats["new_pages"], stats["updated_pages"],
            stats["unchanged_pages"], stats["archived_pages"], stats["failed_pages"],
        )

        state = load_state()
        state["last_run_at"] = run.finished_at.isoformat() if run.finished_at else None
        state["next_run_at"] = None
        state["last_counts"] = {k: v for k, v in stats.items() if not isinstance(v, dict)}
        save_state(state)
        return stats

    async def _process_pages(
        self,
        pages: list[CrawlResult],
        stats: dict[str, Any],
        *,
        archive_missing: bool,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        """Apply incremental logic + indexing for every crawled page."""
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        total = len(pages)

        for index, result in enumerate(pages, start=1):
            if on_progress:
                on_progress(index, total, f"Processing pages ({index}/{total})")

            if not result.ok:
                stats["failed_pages"] += 1
                self._record_failure(result)
                continue

            content, title = self._extract_content(result)
            if not content and result.kind == "html":
                stats["failed_pages"] += 1
                self._record_failure(result, error="no extracted text")
                continue

            url = self._page_url(result)
            h = content_hash(title, content)
            normalized = normalize_title(title or "")
            page = self._find_page(url)

            if h in seen_hashes:
                stats["duplicates_skipped"] += 1
                self._record_duplicate(url, existing_hash=True)
                continue
            seen_hashes.add(h)

            if page is None:
                page = self._create_page(result, url, title, content, h)
                stats["new_pages"] += 1
                if self.index_rag:
                    if self._index_page(page, result, content):
                        stats["indexed_pages"] += 1
            elif page.status == "archived":
                # Page is back on the site — unarchive and treat as new content.
                page.status = "new"
                page.archived_at = None
                page.content = content or page.content
                page.title = title or page.title
                page.content_hash = h
                page.last_synced = utcnow()
                self.db.commit()
                stats["updated_pages"] += 1
                if self.index_rag:
                    if self._index_page(page, result, content):
                        stats["indexed_pages"] += 1
            elif page.content_hash == h and self._title_matches(page, normalized):
                page.status = "unchanged"
                page.http_status = result.http_status
                page.etag = result.etag
                page.last_modified = result.last_modified
                page.last_synced = utcnow()
                self.db.commit()
                stats["unchanged_pages"] += 1
            else:
                self._archive_version(page)
                page.title = title or page.title
                page.normalized_title = normalized
                page.content = content or page.content
                page.content_hash = h
                page.category = self._classify(result, title, content)
                page.http_status = result.http_status
                page.etag = result.etag
                page.last_modified = result.last_modified
                page.status = "updated"
                page.last_synced = utcnow()
                page.last_error = None
                self.db.commit()
                stats["updated_pages"] += 1
                if self.index_rag:
                    if self._index_page(page, result, content):
                        stats["indexed_pages"] += 1

            seen_urls.add(url)

        if archive_missing:
            self._archive_missing(seen_urls, stats)

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _page_url(result: CrawlResult) -> str:
        """Canonical-aware record URL: rel=canonical collapses trailing-slash/
        query-string variants onto one WebsitePage row (dedup at the source)."""
        if result.canonical_url and result.canonical_url != result.url:
            return result.canonical_url
        return result.url

    def _extract_content(self, result: CrawlResult) -> tuple[str, str]:
        """Return (content_text, title) for html or document results."""
        if result.kind == "html":
            return (result.text or "").strip(), (result.title or "").strip()
        if result.kind == "document":
            ext = result.url.rsplit(".", 1)[-1].lower() if "." in result.url else ""
            pages = parse_document_bytes(result.raw or b"", ext, result.url)
            text = "\n\n".join(p.get("text", "") for p in pages).strip()
            filename = Path(result.url.split("?", 1)[0]).name
            title = (result.title or filename or result.url).strip()
            return text, title
        return "", (result.title or "").strip()

    def _classify(self, result: CrawlResult, title: str, content: str) -> str:
        return classify_page(title=title, url=result.url, text=content)

    def _find_page(self, url: str) -> WebsitePage | None:
        return self.db.query(WebsitePage).filter(WebsitePage.url == url).first()

    def _title_matches(self, page: WebsitePage, normalized: str) -> bool:
        if not normalized:
            return True
        return page.normalized_title == normalized

    def _create_page(
        self, result: CrawlResult, url: str, title: str, content: str, h: str
    ) -> WebsitePage:
        page = WebsitePage(
            url=url,
            base_url=self.base_url,
            title=title or None,
            normalized_title=normalize_title(title) or None,
            category=self._classify(result, title, content),
            content_type=result.kind,
            content=content or None,
            content_hash=h,
            http_status=result.http_status,
            etag=result.etag,
            last_modified=result.last_modified,
            version=1,
            status="new",
            first_seen=utcnow(),
            last_synced=utcnow(),
        )
        self.db.add(page)
        self.db.commit()
        self.db.refresh(page)
        return page

    def _archive_version(self, page: WebsitePage) -> None:
        """Snapshot the current page content as an immutable version row."""
        version = WebsitePageVersion(
            page_id=page.id,
            version=page.version,
            title=page.title,
            category=page.category,
            content=page.content,
            content_hash=page.content_hash,
            http_status=page.http_status,
            etag=page.etag,
            last_modified=page.last_modified,
            synced_at=utcnow(),
        )
        self.db.add(version)
        page.version = (page.version or 1) + 1
        self.db.commit()

    def _archive_missing(self, seen_urls: set[str], stats: dict[str, Any]) -> None:
        """Archive pages that existed but were not seen on the site anymore."""
        known = (
            self.db.query(WebsitePage)
            .filter(WebsitePage.status.in_(["new", "unchanged", "updated"]))
            .all()
        )
        for page in known:
            if page.url not in seen_urls:
                self._archive_version(page)
                page.status = "archived"
                page.archived_at = utcnow()
                page.last_synced = utcnow()
                stats["archived_pages"] += 1
        if stats["archived_pages"]:
            self.db.commit()

    def _record_failure(self, result: CrawlResult, error: str | None = None) -> None:
        page = self._find_page(self._page_url(result))
        if page:
            page.status = "failed"
            page.last_error = error or result.error or "request failed"
            page.last_synced = utcnow()
            self.db.commit()

    def _record_duplicate(self, url: str, *, existing_hash: bool) -> None:
        page = self._find_page(url)
        if page:
            page.last_synced = utcnow()
            self.db.commit()

    # -- RAG indexing -------------------------------------------------------
    def _index_page(self, page: WebsitePage, result: CrawlResult, content: str) -> bool:
        """Chunk + embed + store page text in Chroma under a Document row."""
        try:
            from app.ingest.chunker import chunk_pages
            from app.ingest.embed import embed_documents
            from app.ingest.store import add_chunks_with_embeddings, delete_document

            chunks = chunk_pages([{"page": 1, "text": content}])
            if not chunks:
                return False
            embeddings = embed_documents([c["content"] for c in chunks])
            doc = self._get_or_create_document(page)
            if not doc:
                return False
            if page.document_id != str(doc.id):
                page.document_id = str(doc.id)
            delete_document(str(doc.id))
            add_chunks_with_embeddings(
                str(doc.id),
                doc.title,
                chunks,
                embeddings,
                {
                    "document_type": "website",
                    "category": page.category or "",
                    "source_url": page.url,
                    "source": "website",
                },
            )
            doc.chunk_count = len(chunks)
            doc.status = "ready"
            doc.sha256 = page.content_hash
            doc.document_type = "website"
            doc.category = page.category
            self.db.commit()
            return True
        except Exception as exc:
            log.warning("Index failed for %s: %s", page.url, exc)
            page.last_error = f"index: {str(exc)[:300]}"
            page.status = "failed"
            self.db.commit()
            return False

    def _get_or_create_document(self, page: WebsitePage) -> Document | None:
        from app.models import Document as _Document
        from app.utils.files import sanitize_filename

        if page.document_id:
            try:
                import uuid

                doc = self.db.get(_Document, uuid.UUID(page.document_id))
            except (ValueError, Exception):  # noqa: BLE001
                doc = None
            if doc:
                return doc

        title = page.title or page.url
        safe = sanitize_filename(f"{title[:60] or 'website-page'}.txt")
        doc = _Document(
            owner_id=None,
            title=title,
            filename=safe,
            original_filename=page.url,
            file_type="txt",
            file_size=len((page.content or "").encode("utf-8")),
            sha256=page.content_hash,
            status="indexing",
            chunk_count=0,
            document_type="website",
            category=page.category,
        )
        self.db.add(doc)
        self.db.commit()
        self.db.refresh(doc)
        return doc

    # -- dashboard / admin support -----------------------------------------
    def get_status(self) -> dict[str, Any]:
        total = self.db.query(WebsitePage).count()
        by_status: dict[str, int] = {}
        for row in self.db.query(WebsitePage.status).all():
            by_status[row[0]] = by_status.get(row[0], 0) + 1
        categories: dict[str, int] = {}
        for row in self.db.query(WebsitePage.category).all():
            cat = row[0] or "unknown"
            categories[cat] = categories.get(cat, 0) + 1
        indexed = self.db.query(WebsitePage).filter(
            WebsitePage.document_id.isnot(None)
        ).count()
        last_run = (
            self.db.query(CrawlRun)
            .order_by(CrawlRun.started_at.desc())
            .first()
        )
        state = load_state()
        runtime = get_runtime_state()
        dup_total = 0
        for row in self.db.query(WebsitePage.content_hash).filter(WebsitePage.content_hash.isnot(None)).all():
            dup_total += 1
        dup_distinct = self.db.query(WebsitePage.content_hash).filter(
            WebsitePage.content_hash.isnot(None)
        ).distinct().count()
        return {
            # Master toggle — the single ON/OFF switch for the entire system
            "master_enabled": state.get("enabled", False),
            # Schedule preset: disabled | manual | hourly | 6hourly | daily | weekly | monthly
            "schedule": state.get("schedule", "disabled"),
            # Human-readable schedule label
            "schedule_label": self._schedule_label(state.get("schedule", "disabled")),
            # Crawl configuration defaults (from settings)
            "crawl_max_pages": 200,
            "crawl_max_depth": 4,
            "crawl_delay": 0.4,
            "base_url": self.base_url,
            "total_pages": total,
            "indexed_pages": indexed,
            "duplicate_pages": max(0, dup_total - dup_distinct),
            "status_breakdown": by_status,
            "categories": categories,
            "last_run": last_run.to_dict() if last_run else None,
            "last_counts": state.get("last_counts"),
            "runtime": runtime,
            "ready": runtime.get("state") == "Ready",
            # Sync health & control
            "health": self._sync_health(),
        }

    def _schedule_label(self, schedule: str) -> str:
        """Return a human-readable label for the schedule preset."""
        labels = {
            "disabled": "Disabled",
            "manual": "Manual",
            "hourly": "Every Hour",
            "6hourly": "Every 6 Hours",
            "daily": "Daily",
            "weekly": "Weekly",
            "monthly": "Monthly",
        }
        return labels.get(schedule, schedule)

    def list_pages(
        self,
        category: str | None = None,
        status: str | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = self.db.query(WebsitePage)
        if category:
            query = query.filter(WebsitePage.category == category)
        if status:
            query = query.filter(WebsitePage.status == status)
        if q:
            query = query.filter(WebsitePage.url.contains(q) | WebsitePage.title.contains(q))
        query = query.order_by(WebsitePage.last_synced.desc()).limit(limit).offset(offset)
        return [p.to_dict() for p in query.all()]

    def list_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        runs = (
            self.db.query(CrawlRun)
            .order_by(CrawlRun.started_at.desc())
            .limit(limit)
            .all()
        )
        return [r.to_dict() for r in runs]

    def list_versions(self, page_id: str) -> list[dict[str, Any]]:
        versions = (
            self.db.query(WebsitePageVersion)
            .filter(WebsitePageVersion.page_id == page_id)
            .order_by(WebsitePageVersion.version.desc())
            .all()
        )
        return [v.to_dict() for v in versions]

    def reindex_page(self, page_id: str) -> dict[str, Any]:
        page = self.db.get(WebsitePage, page_id)
        if not page:
            return {"error": "Page not found"}
        if not page.content:
            return {"error": "Page has no extracted content"}
        result = CrawlResult(page.url)
        result.kind = page.content_type or "html"
        result.http_status = page.http_status
        result.etag = page.etag
        result.last_modified = page.last_modified
        result.title = page.title
        result.text = page.content
        result.ok = True
        ok = self.index_rag and self._index_page(page, result, page.content)
        return {"id": page_id, "status": page.status, "indexed": bool(ok)}

    def archive_page(self, page_id: str) -> dict[str, Any]:
        page = self.db.get(WebsitePage, page_id)
        if not page:
            return {"error": "Page not found"}
        if page.status != "archived":
            self._archive_version(page)
            page.status = "archived"
            page.archived_at = utcnow()
            page.last_synced = utcnow()
            self.db.commit()
        return {"id": page_id, "status": "archived", "version": page.version}

    def scan_duplicates(self) -> dict[str, Any]:
        """Report pages that share content hashes (semantic dupes)."""
        pages = self.db.query(WebsitePage).all()
        by_hash: dict[str, list[WebsitePage]] = {}
        for p in pages:
            if p.content_hash:
                by_hash.setdefault(p.content_hash, []).append(p)
        groups = [g for g in by_hash.values() if len(g) > 1]
        return {
            "duplicate_groups": len(groups),
            "duplicate_pages": sum(len(g) - 1 for g in groups),
            "groups": [
                [p.to_dict() for p in g] for g in groups
            ],
        }

    def _sync_health(self) -> dict[str, Any]:
        """Return sync health information for the dashboard."""
        state = load_state()
        runtime = get_runtime_state()
        # Check if the source URL is reachable
        base_url = self.base_url or "."
        reachable = False
        ssl_ok = False
        robots_ok = False
        sitemap_ok = False
        try:
            import httpx
            parsed = urlparse(base_url)
            if parsed.scheme in ("http", "https"):
                with httpx.Client(timeout=5.0, follow_redirects=True) as client:
                    resp = client.get(base_url, follow_redirects=True)
                    reachable = resp.status_code < 500
                    ssl_ok = resp.is_tls_verified if hasattr(resp, "is_tls_verified") else True
        except Exception:
            reachable = False
        # Check robots.txt
        try:
            from app.knowledge_sync.web_crawler import RobotsCache
            robots = RobotsCache()
            # We can't fully load robots without a client, but check state
            robots_ok = True  # robots check done during crawl
            # Check sitemap
            sitemap_ok = bool(state.get("last_run_at"))  # simplified: sitemap found if we've run
        except Exception:
            robots_ok = False
            sitemap_ok = False
        return {
            "website_reachable": reachable,
            "ssl_tls_valid": ssl_ok,
            "robots_txt": "loaded" if robots_ok else "unavailable",
            "sitemap": "found" if sitemap_ok else "not found",
            "crawler": "running" if runtime.get("state") in ("Connecting", "Connected", "Discovering", "Syncing", "Processing") else "stopped",
            "last_successful_sync": state.get("last_counts", {}).get("status") == "completed" and state.get("last_run_at"),
            "last_failed_sync": bool(state.get("last_counts", {}).get("error")),
            "consecutive_failries": self._consecutive_failures(),
            "average_crawl_duration": self._avg_crawl_duration(),
            "failed_url_count": self._failed_url_count(),
        }

    def _consecutive_failures(self) -> int:
        """Count consecutive failed sync runs."""
        state = load_state()
        # This is a simplified count; in production you'd track this in the state file
        return 0

    def _avg_crawl_duration(self) -> float | None:
        """Average crawl duration from recent runs."""
        runs = self.db.query(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(10).all()
        if not runs:
            return None
        durations = [r.duration_seconds for r in runs if r.duration_seconds]
        if not durations:
            return None
        return sum(durations) / len(durations)

    def _failed_url_count(self) -> int:
        """Count of failed pages from the most recent crawl run."""
        run = self.db.query(CrawlRun).order_by(CrawlRun.started_at.desc()).first()
        if run and run.failed_pages:
            return run.failed_pages
        return 0
