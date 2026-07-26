from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.utils.logging import log

_APPROVED_DOMAINS: list[str] = []


def _get_approved() -> list[str]:
    global _APPROVED_DOMAINS
    if not _APPROVED_DOMAINS:
        _APPROVED_DOMAINS = [
            d.strip().lower()
            for d in settings.KNOWLEDGE_SYNC_DOMAINS.split(",")
            if d.strip()
        ]
    return _APPROVED_DOMAINS


def _is_approved(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return any(domain in host for domain in _get_approved())


async def _download_single(
    client: httpx.AsyncClient,
    url: str,
    dest_dir: Path,
    *,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Download one file. Returns result dict."""
    result: dict[str, Any] = {"url": url, "success": False, "path": None, "error": None}

    if not _is_approved(url):
        result["error"] = "Domain not approved"
        return result

    try:
        resp = await client.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        data = resp.content

        if len(data) < 100:
            result["error"] = f"File too small ({len(data)} bytes)"
            return result

        parsed = urlparse(url)
        filename = Path(parsed.path).name or f"document_{len(data)}"
        if not filename or "." not in filename:
            filename += ".pdf"

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        dest.write_bytes(data)

        result["success"] = True
        result["path"] = str(dest)
        result["size"] = len(data)
        result["filename"] = filename
        log.info("Downloaded %s -> %s (%d bytes)", url, dest, len(data))
    except httpx.HTTPStatusError as exc:
        result["error"] = f"HTTP {exc.response.status_code}"
        log.warning("HTTP error downloading %s: %s", url, exc.response.status_code)
    except httpx.RequestError as exc:
        result["error"] = f"Request failed: {exc}"
        log.warning("Request failed for %s: %s", url, exc)
    except Exception as exc:
        result["error"] = str(exc)[:200]
        log.warning("Unexpected error downloading %s: %s", url, exc)

    return result


async def _crawl_page(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_pages: int = 50,
    same_domain_only: bool = True,
) -> set[str]:
    """Crawl a page and return discovered PDF/doc URLs."""
    found: set[str] = set()
    visited: set[str] = set()
    to_visit: set[str] = {url}
    parsed_base = urlparse(url)
    base_domain = parsed_base.netloc.lower()

    while to_visit and len(visited) < max_pages:
        current = to_visit.pop()
        if current in visited:
            continue
        visited.add(current)

        try:
            resp = await client.get(current, timeout=15.0, follow_redirects=True)
            if resp.status_code != 200:
                continue
            text = resp.text
        except Exception:
            continue

        for match in re.finditer(r'href=["\'](https?://[^"\']+)["\']', text, re.IGNORECASE):
            link = match.group(1)
            if link in visited or link in to_visit:
                continue
            parsed_link = urlparse(link)
            link_domain = parsed_link.netloc.lower()

            if same_domain_only and base_domain not in link_domain:
                continue

            if any(link.lower().endswith(ext) for ext in (".pdf", ".doc", ".docx", ".txt", ".md")):
                found.add(link)
            elif link.startswith(f"{parsed_link.scheme}://{link_domain}") and len(visited) < max_pages:
                to_visit.add(link)

        await asyncio.sleep(0.3)

    return found


class Fetcher:
    """Parallel downloader for approved knowledge sources."""

    def __init__(self, download_dir: str | Path | None = None):
        self.download_dir = Path(download_dir or settings.KNOWLEDGE_SYNC_DIR)

    async def fetch(
        self,
        urls: list[str],
        *,
        max_concurrent: int = 5,
        timeout: float = 120.0,
    ) -> list[dict[str, Any]]:
        """Download multiple URLs in parallel. Returns list of result dicts."""
        results: list[dict[str, Any]] = []
        sem = asyncio.Semaphore(max_concurrent)

        async def _limited(url: str) -> dict[str, Any]:
            async with sem:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    return await _download_single(client, url, self.download_dir, timeout=timeout)

        tasks = [_limited(url) for url in urls if _is_approved(url)]
        if not tasks:
            log.warning("No approved URLs to fetch")
            return results

        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)

        ok = sum(1 for r in results if r.get("success"))
        log.info("Fetcher: %d/%d downloaded successfully", ok, len(results))
        return results

    async def discover_and_fetch(
        self,
        seed_urls: list[str] | None = None,
        *,
        max_pages: int = 50,
        max_concurrent: int = 5,
    ) -> list[dict[str, Any]]:
        """Crawl seed pages, discover documents, then download them."""
        seeds = seed_urls or [f"https://{d}" for d in _get_approved()]
        all_urls: set[str] = set()

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, verify=False) as client:
            for seed in seeds:
                found = await _crawl_page(client, seed, max_pages=max_pages)
                all_urls.update(found)
                await asyncio.sleep(0.5)

        log.info("Discovered %d document URLs from %d seeds", len(all_urls), len(seeds))
        return await self.fetch(list(all_urls), max_concurrent=max_concurrent)

    def discover_and_fetch_sync(
        self,
        seed_urls: list[str] | None = None,
        *,
        max_pages: int = 50,
        max_concurrent: int = 5,
    ) -> list[dict[str, Any]]:
        """Synchronous wrapper for discover_and_fetch."""
        return asyncio.run(self.discover_and_fetch(seed_urls, max_pages=max_pages, max_concurrent=max_concurrent))
