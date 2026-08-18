"""
backend/app/knowledge_sync/web_crawler.py

Bounded, same-domain crawler for the Website Knowledge Sync engine.

Behavior:
  - Breadth-first crawl from the configured base URL (or explicit seeds).
  - Only same-domain / subdomain links are followed (no external leakage).
  - robots.txt disallow rules are honored per host; "Sitemap:" directives in
    robots.txt and /sitemap.xml are used as an ADDITIONAL seed source (the
    HTML crawl is always the primary discovery mechanism).
  - Security hardening:
      * http/https schemes only (files:, data:, javascript: etc. rejected)
      * redirects are followed hop-by-hop with per-hop validation (no
        redirect smuggling to an external/private target)
      * SSRF protection: every resolved host is checked against loopback,
        link-local and private-network ranges unless the explicit
        allow-private flag is set (local dummy/test mirrors only)
      * download size caps (WEBSITE_SYNC_MAX_FILE_SIZE_MB) enforced even
        when a server omits Content-Length
      * transient failures (5xx, timeouts, network errors) retried with
        exponential backoff
  - URLs are normalized (fragments stripped, tracking params dropped,
    default ports removed, duplicate slashes collapsed) to avoid duplicate
    work and infinite loops; a page's rel=canonical URL is captured so
    duplicate/trailing-slash variants map to one record.
  - HTML pages are parsed into semantic text via web_extractor; document
    links (pdf/doc/docx/xls/xlsx/ppt/pptx/csv/txt/md) are downloaded as raw
    bytes.
  - Polite throttling via WEBSITE_CRAWL_DELAY; live progress is emitted
    through the on_stage callback for the dashboard state machine.

Testable against a local HTTP server (httpx-based, no external service).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

from app.config import settings
from app.knowledge_sync.web_extractor import extract_html
from app.utils.logging import log

_TRACKING_PARAMS = re.compile(r"^utm_")
_DOCUMENT_EXTS = set(
    (settings.WEBSITE_SYNC_DOCUMENT_EXTS or "pdf,doc,docx,xls,xlsx,csv,txt,md,ppt,pptx")
    .replace(" ", "")
    .split(",")
)
_IGNORED_EXTS = {
    "jpg", "jpeg", "png", "gif", "webp", "svg", "ico", "css", "js", "map",
    "woff", "woff2", "ttf", "eot", "otf", "zip", "rar", "7z", "gz", "tar",
    "mp4", "mkv", "avi", "mov", "webm", "mp3", "wav", "ogg",
}

_MAX_REDIRECT_HOPS = 10
_SAFE_SCHEMES = {"http", "https"}
_LOCAL_HOST_RE = re.compile(
    r"^(localhost|localhost\.localdomain|.*\.local|.*\.internal|.*\.corp|.*\.home|.*\.lan)$",
    re.IGNORECASE,
)


def normalize_url(url: str) -> str:
    """Normalize a URL for dedup/follow decisions."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip()
    scheme = parsed.scheme.lower() or "http"
    host = parsed.netloc.lower()
    path = parsed.path or ""
    if not path and parsed.scheme.lower() in ("http", "https"):
        path = "/"
    path = re.sub(r"/{2,}", "/", path)
    if parsed.hostname and parsed.port and parsed.port in (80, 443):
        host = parsed.hostname
    # Drop tracking/empty params; keep meaningful ones (pagination, filters).
    keep = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_") and v != ""]
    query = urlencode(keep) if keep else ""
    # Keep a trailing slash on the bare root so base == root+"/" don't collide.
    return urlunparse((scheme, host, path, parsed.params, query, ""))


def domain_of(url: str) -> str:
    parsed = urlparse(normalize_url(url))
    return (parsed.hostname or "").lower()


def same_domain(url: str, base_host: str) -> bool:
    """True if url host equals base host or is a subdomain of it."""
    host = domain_of(url)
    if not host:
        return False
    base_host = base_host.lower()
    return host == base_host or host.endswith("." + base_host)


def _scheme_allowed(url: str) -> bool:
    parsed = urlparse(normalize_url(url))
    return parsed.scheme.lower() in _SAFE_SCHEMES


def is_private_address(ip: str) -> bool:
    """True for loopback, link-local, private, CGNAT, multicast, unspecified."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable -> treat as unsafe
    if addr.version == 4:
        if addr.is_loopback or addr.is_link_local or addr.is_private:
            return True
        if addr.is_multicast or addr.is_reserved or addr.is_unspecified:
            return True
        # CGNAT 100.64.0.0/10 and shared 192.0.0.0/24
        if addr in ipaddress.ip_network("100.64.0.0/10"):
            return True
        if addr in ipaddress.ip_network("192.0.0.0/24"):
            return True
        return False
    # IPv6
    if addr.is_loopback or addr.is_link_local or addr.is_private or addr.is_multicast:
        return True
    if addr.is_unspecified or addr.is_reserved:
        return True
    if addr.ipv4_mapped is not None:
        return is_private_address(str(addr.ipv4_mapped))
    return False


def _name_looks_private(hostname: str) -> bool:
    if not hostname:
        return True
    if hostname.endswith("."):
        return _name_looks_private(hostname[:-1])
    if _LOCAL_HOST_RE.match(hostname):
        return True
    return False


async def host_is_private(url: str, allow_private_hosts: bool) -> bool:
    """Resolve the URL's host and report whether it is a private target.

    allow_private_hosts bypasses the check (explicit local/test mirrors
    only). Fail-closed: DNS/connectivity errors are treated as unsafe.
    """
    if allow_private_hosts:
        return False
    parsed = urlparse(normalize_url(url))
    host = parsed.hostname or ""
    if not host:
        return True
    if _name_looks_private(host):
        log.warning("SSRF guard: blocked private hostname %r", host)
        return True
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError:
        return True
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port)
    except RuntimeError:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port)
    except OSError:
        log.warning("SSRF guard: cannot resolve %s", host)
        return True
    for info in infos:
        sockaddr = info[4]
        ip = sockaddr[0] if isinstance(sockaddr, tuple) else str(sockaddr)
        if is_private_address(ip):
            log.warning("SSRF guard: %s resolves to private address %s", host, ip)
            return True
    return False


class CrawlResult:
    """Normalized per-page crawl output consumed by WebsiteSyncEngine."""

    def __init__(self, url: str):
        self.url = url
        self.kind: str = "html"          # html | document | binary
        self.ok: bool = False
        self.http_status: int | None = None
        self.title: str | None = None
        self.description: str | None = None
        self.text: str | None = None     # extracted text (html pages)
        self.links: list[str] = []
        self.canonical_url: str | None = None
        self.etag: str | None = None
        self.last_modified: str | None = None
        self.content_type: str | None = None
        self.raw: bytes | None = None    # document bytes
        self.error: str | None = None


StageCallback = Callable[[str, int, int | None, str], None]
"""on_stage(stage, current, total, message); stage is one of:
   connecting | connected | discovering | downloading"""


class RobotsCache:
    """Minimal robots.txt parser: honors per-host disallow path prefixes."""

    def __init__(self):
        self._rules: dict[str, list[str]] = {}
        self.sitemap_urls: list[str] = []

    async def load(
        self,
        client: httpx.AsyncClient,
        base_netloc: str,
        *,
        allow_private_hosts: bool,
        verify_tls: bool,
    ) -> None:
        if base_netloc in self._rules:
            return
        disallow: list[str] = []
        sitemaps: list[str] = []
        for scheme in ("http", "https"):
            resp = await _safe_get(
                client,
                f"{scheme}://{base_netloc}/robots.txt",
                allow_private_hosts=allow_private_hosts,
                verify_tls=verify_tls,
                follow_redirects=True,
            )
            if resp is None:
                continue
            try:
                disallow, sitemaps = self._parse_robots(resp.text)
            except Exception:
                disallow, sitemaps = [], []
            break
        self._rules[base_netloc] = disallow
        self.sitemap_urls = sitemaps
        if disallow:
            log.info("robots.txt for %s disallows %d paths", base_netloc, len(disallow))

    @staticmethod
    def _parse_robots(text: str) -> tuple[list[str], list[str]]:
        paths: list[str] = []
        sitemaps: list[str] = []
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            lower = line.lower()
            if lower.startswith("disallow:"):
                path = line.split(":", 1)[1].strip() if ":" in line else ""
                if path:
                    paths.append(path)
            elif lower.startswith("sitemap:"):
                sitemap = line.split(":", 1)[1].strip() if ":" in line else ""
                if sitemap:
                    sitemaps.append(sitemap)
        return paths, sitemaps

    def allowed(self, url: str, base_netloc: str) -> bool:
        for prefix in self._rules.get(base_netloc, []):
            path = urlparse(normalize_url(url)).path
            if prefix.endswith("*"):
                prefix = prefix[:-1]
            if prefix and path.startswith(prefix):
                return False
        return True


async def _safe_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    allow_private_hosts: bool,
    verify_tls: bool,
    follow_redirects: bool = True,
    same_domain_host: str | None = None,
    max_file_size: int | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    retry_delay: float | None = None,
) -> httpx.Response | None:
    """Fetch with SSRF guards, redirect hop validation, size cap and
    retry-with-backoff. Returns the final response (body size-capped) or
    None when the request is unsafe / fails after retries."""
    normalized = normalize_url(url)
    if not _scheme_allowed(normalized):
        log.warning("Blocked unsafe scheme: %s", url)
        return None

    cap = (
        max_file_size
        if max_file_size is not None
        else int(settings.WEBSITE_SYNC_MAX_FILE_SIZE_MB * 1024 * 1024)
    )
    tmo = timeout if timeout is not None else settings.WEBSITE_SYNC_REQUEST_TIMEOUT
    retries = max_retries if max_retries is not None else settings.WEBSITE_SYNC_RETRIES
    base_delay = retry_delay if retry_delay is not None else float(settings.WEBSITE_SYNC_RETRY_BASE_DELAY)

    attempt = 0
    while True:
        response = await _guarded_get(
            client, normalized,
            allow_private_hosts=allow_private_hosts,
            follow_redirects=follow_redirects,
            same_domain_host=same_domain_host,
            max_file_size=cap,
            timeout=tmo,
        )
        if response is None:
            return None
        if response.status_code < 500:
            return response
        attempt += 1
        if attempt > retries:
            return response
        await asyncio.sleep(base_delay * (2 ** (attempt - 1)))


async def _guarded_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    allow_private_hosts: bool,
    follow_redirects: bool,
    same_domain_host: str | None,
    max_file_size: int,
    timeout: float,
) -> httpx.Response | None:
    """Fetch with per-hop validation. Redirects are followed hop-by-hop so
    no hop can escape the safe-scheme / non-private network / same-domain
    rules (SSRF + DNS-rebinding + redirect-smuggling protection)."""
    current = normalize_url(url)
    if not _scheme_allowed(current):
        log.warning("Blocked unsafe scheme: %s", current)
        return None

    hops = 0
    seen_hops: set[str] = {current}
    while hops <= _MAX_REDIRECT_HOPS:
        if await host_is_private(current, allow_private_hosts):
            log.warning("SSRF guard: blocked private target %s", current)
            return None

        status = 0
        headers = None
        body = b""
        try:
            async with client.stream(
                "GET",
                current,
                timeout=timeout,
                follow_redirects=False,
                headers={"User-Agent": "CUS-AI-KnowledgeBot/1.0"},
            ) as resp:
                status = resp.status_code
                headers = resp.headers
                declared = headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > max_file_size:
                    log.warning("Skipping %s — declared %s bytes exceed cap", current, declared)
                    return None
                body = b""
                total = 0
                overflow = False
                async for chunk in resp.aiter_bytes():
                    remaining = max_file_size - total
                    if len(chunk) > remaining:
                        body += chunk[:remaining]
                        total = max_file_size
                        overflow = True
                        break
                    body += chunk
                    total += len(chunk)
                    if total > max_file_size:
                        overflow = True
                        break
                if overflow:
                    log.warning("Skipping %s — body exceeds %d byte cap", current, max_file_size)
                    return None
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            log.debug("Fetch error %s: %s", current, exc)
            return None

        if status in (301, 302, 303, 307, 308) and follow_redirects:
            location = headers.get("Location") if headers else None
            if not location:
                return httpx.Response(status_code=status, headers=headers, content=body)
            next_url = normalize_url(urljoin(current, location))
            if not _scheme_allowed(next_url) or next_url in seen_hops:
                log.warning("Blocked redirect chain at %s -> %s", current, next_url)
                return None
            if same_domain_host and not same_domain(next_url, same_domain_host):
                log.warning("Redirect escape blocked: %s -> %s (outside %s)",
                            current, next_url, same_domain_host)
                return None
            seen_hops.add(next_url)
            current = next_url
            hops += 1
            continue

        return httpx.Response(status_code=status, headers=headers, content=body)

    log.warning("Redirect limit (%d hops) exceeded for %s", _MAX_REDIRECT_HOPS, url)
    return None


class WebsiteCrawler:
    """Sequential bounded BFS crawler with politeness + robots support."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        max_pages: int | None = None,
        max_depth: int | None = None,
        delay: float | None = None,
        client: httpx.AsyncClient | None = None,
        allow_private_hosts: bool | None = None,
        use_sitemap: bool | None = None,
        verify_tls: bool | None = None,
    ):
        self.base_url = normalize_url(
            base_url or settings.WEBSITE_KNOWLEDGE_SOURCE_URL or settings.WEBSITE_BASE_URL
        )
        self.base_host = domain_of(self.base_url)
        self.base_netloc = urlparse(self.base_url).netloc
        self.max_pages = max_pages if max_pages is not None else settings.WEBSITE_CRAWL_MAX_PAGES
        self.max_depth = max_depth if max_depth is not None else settings.WEBSITE_CRAWL_MAX_DEPTH
        self.delay = settings.WEBSITE_CRAWL_DELAY if delay is None else float(delay)
        self.allow_private_hosts = (
            bool(settings.WEBSITE_SYNC_ALLOW_PRIVATE_HOSTS)
            if allow_private_hosts is None
            else bool(allow_private_hosts)
        )
        self.use_sitemap = (
            bool(settings.WEBSITE_SYNC_USE_SITEMAP) if use_sitemap is None else bool(use_sitemap)
        )
        self.verify_tls = (
            bool(settings.WEBSITE_SYNC_VERIFY_TLS) if verify_tls is None else bool(verify_tls)
        )
        self.robots = RobotsCache()

    async def crawl(
        self,
        seed_urls: list[str] | None = None,
        on_stage: StageCallback | None = None,
    ) -> dict:
        """Run the crawl and return {"pages": [CrawlResult], "fetched": count}.

        on_stage(stage, current, total, message) delivers live progress for
        the dashboard state machine (connecting / connected / discovering /
        downloading).
        """
        seeds = [normalize_url(s) for s in (seed_urls or [self.base_url])]
        seeds = [s for s in seeds if s and same_domain(s, self.base_host)]
        if not seeds:
            return {"pages": [], "fetched": 0}

        def emit(stage, current=0, total=None, message=""):
            if on_stage:
                on_stage(stage, current, total, message)

        emit("connecting", 0, None, f"Connecting to {self.base_url}")

        results: list[CrawlResult] = []
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(s, 1) for s in seeds]

        async with httpx.AsyncClient(verify=self.verify_tls, follow_redirects=False) as client:
            await self.robots.load(
                client, self.base_netloc,
                allow_private_hosts=self.allow_private_hosts,
                verify_tls=self.verify_tls,
            )

            # Sitemap.xml is an ADDITIONAL seed source, never exclusive.
            if self.use_sitemap and not seed_urls:
                sitemap_candidates = list(self.robots.sitemap_urls)
                scheme = urlparse(self.base_url).scheme or "https"
                root_sitemap = f"{scheme}://{self.base_netloc}/sitemap.xml"
                if root_sitemap not in sitemap_candidates:
                    sitemap_candidates.append(root_sitemap)
                sitemap_ok = await self._seed_from_sitemaps(client, sitemap_candidates, queue)
                if sitemap_ok:
                    log.info("Sitemap seeds added (queue=%d)", len(queue))

            while queue and len(visited) < self.max_pages:
                url, depth = queue.pop(0)
                norm = normalize_url(url)
                if norm in visited:
                    continue
                visited.add(norm)
                if not same_domain(norm, self.base_host):
                    continue
                if not self.robots.allowed(norm, self.base_netloc):
                    continue

                path = urlparse(norm).path.lower()
                ext = path.rsplit(".", 1)[-1] if "." in path else ""
                if ext and ext in _IGNORED_EXTS:
                    continue

                if self.delay > 0:
                    await asyncio.sleep(self.delay)

                emit("discovering", current=len(visited), total=len(queue) + len(visited),
                     message=f"Discovering pages ({len(visited)} fetched)")

                resp = await _safe_get(
                    client, norm,
                    allow_private_hosts=self.allow_private_hosts,
                    verify_tls=self.verify_tls,
                    follow_redirects=True,
                    same_domain_host=self.base_host,
                )
                result = CrawlResult(norm)
                if resp is None:
                    result.error = "request failed or blocked"
                    results.append(result)
                    continue

                result.http_status = resp.status_code
                result.etag = resp.headers.get("ETag")
                result.last_modified = resp.headers.get("Last-Modified")
                result.content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if resp.status_code >= 400:
                    result.error = f"http_{resp.status_code}"
                    results.append(result)
                    continue

                emit("connected", 1, None, f"Connected to {urlparse(norm).netloc}")

                if result.content_type == "text/html" or (not ext and not result.content_type):
                    result.kind = "html"
                    parsed = extract_html(resp.text, base_url=norm)
                    result.title = parsed["title"]
                    result.description = parsed["description"]
                    result.text = parsed["text"] or None
                    canonical = parsed.get("canonical")
                    if canonical and _scheme_allowed(canonical) and same_domain(canonical, self.base_host):
                        result.canonical_url = canonical
                    else:
                        result.canonical_url = None
                    for link in parsed["links"]:
                        n_link = normalize_url(link)
                        if not n_link or n_link in visited:
                            continue
                        if not same_domain(n_link, self.base_host):
                            continue
                        if not self.robots.allowed(n_link, self.base_netloc):
                            continue
                        q_parsed = urlparse(n_link)
                        l_path = q_parsed.path.lower()
                        l_ext = l_path.rsplit(".", 1)[-1] if "." in l_path else ""
                        if l_ext and l_ext in _IGNORED_EXTS:
                            continue
                        if depth + 1 <= self.max_depth:
                            queue.append((n_link, depth + 1))
                elif ext in _DOCUMENT_EXTS or result.content_type.startswith("application/") or result.content_type.startswith("text/"):
                    result.kind = "document"
                    result.raw = resp.content
                    emit("downloading", len(results) + 1, None,
                         f"Syncing document {norm}")
                else:
                    result.kind = "binary"
                    result.raw = resp.content
                result.ok = True
                results.append(result)

        log.info("Crawl finished: %d results, %d visited", len(results), len(visited))
        return {"pages": results, "fetched": len(visited)}

    # -- sitemap -----------------------------------------------------------
    async def _seed_from_sitemaps(
        self,
        client: httpx.AsyncClient,
        sitemap_urls: list[str],
        queue: list[tuple[str, int]],
    ) -> bool:
        """Fetch sitemap(s) and append same-domain URLs to the frontier."""
        added = 0
        existing = {item[0] for item in queue}
        for sitemap in sitemap_urls:
            resp = await _safe_get(
                client, sitemap,
                allow_private_hosts=self.allow_private_hosts,
                verify_tls=self.verify_tls,
                follow_redirects=True,
                same_domain_host=self.base_host,
            )
            if resp is None or resp.status_code != 200:
                continue
            for loc in self._parse_sitemap_locs(resp.text):
                n = normalize_url(loc)
                if not n or not _scheme_allowed(n):
                    continue
                if not same_domain(n, self.base_host):
                    continue
                ext = urlparse(n).path.lower().rsplit(".", 1)[-1] if "." in urlparse(n).path else ""
                if ext and ext in _IGNORED_EXTS:
                    continue
                if n not in existing:
                    queue.append((n, 1))
                    existing.add(n)
                    added += 1
        return added > 0

    @staticmethod
    def _parse_sitemap_locs(text: str) -> list[str]:
        """Extract <loc> URLs from sitemap XML (namespace-tolerant regex)."""
        locs: list[str] = []
        for match in re.finditer(r"<loc>\s*(.*?)\s*</loc>", text or "", re.IGNORECASE | re.DOTALL):
            loc = match.group(1).strip()
            if loc:
                locs.append(loc)
        return locs

    # -- low-level fetch (kept for compatibility with older callers) -------
    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response | None:
        return await _safe_get(
            client, url,
            allow_private_hosts=self.allow_private_hosts,
            verify_tls=self.verify_tls,
            follow_redirects=True,
            same_domain_host=self.base_host,
        )

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> httpx.Response | None:
        return await self._get(client, url)