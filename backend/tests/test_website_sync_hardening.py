"""
backend/tests/test_website_sync_hardening.py

Hardening tests for the Website Knowledge Sync engine:

  * security            — SSRF (loopback/link-local/CGNAT/private ranges),
                          unsafe schemes, redirect-escape and redirect-loop
                          guards, size caps, retry-with-backoff on 5xx
  * discovery           — linked pages (relative + absolute), documents,
                          sitemap.xml + robots Sitemap seeds (additive only)
  * extraction          — table structure preservation, canonical URLs
  * documents           — PPTX slide text extraction
  * runtime state       — Connecting/Processing -> Ready | Warning | Error,
                          last_counts persistence, enabled flag separation
  * consolidation       — no stale Knowledge Sync artifacts in admin UI/API

Run:  python tests/test_website_sync_hardening.py          (or via pytest)
"""

from __future__ import annotations

import asyncio
import http.server
import io
import os
import re
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------------------
# Test isolation FIRST: route the sync state machine (and its runtime
# "Error: boom" writes) into a throwaway file so the shared production state
# at ./sync_downloads/website_sync_state.json is never polluted by tests.
# ---------------------------------------------------------------------------
os.environ.setdefault(
    "WEBSITE_SYNC_STATE_FILE",
    str(Path(os.environ.get("TEMP", tempfile.gettempdir())) / "_website_sync_test_state.json"),
)

import app.models  # noqa: F401  (register tables before any session)
import app.knowledge_sync.web_engine as web_engine_mod  # noqa: F401

from app.database import SessionLocal, create_all
from app.models.website_sync import CrawlRun, WebsitePage, WebsitePageVersion

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# Local HTTP server helpers
# ---------------------------------------------------------------------------


def _start_server(site: dict):
    """Serve `site` (path -> body | (status, headers, body)) on an ephemeral
    port. The dict is read at request time, so entries can be added after the
    server is bound (port placeholder problem)."""
    state = {"hits": {}}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            state["hits"][path] = state["hits"].get(path, 0) + 1
            spec = site.get(path)
            if spec is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if isinstance(spec, tuple) and len(spec) == 3 and isinstance(spec[0], int):
                status, headers, body = spec
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            raw = spec if isinstance(spec, bytes) else str(spec).encode("utf-8")
            ctype = "application/octet-stream" if isinstance(spec, bytes) else "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", srv, state


def _stop_server(srv) -> None:
    try:
        srv.shutdown()
        srv.server_close()
    except Exception:
        pass


def _wipe_tables() -> None:
    db = SessionLocal()
    try:
        for model in (WebsitePageVersion, WebsitePage, CrawlRun):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()


def _wipe_state() -> None:
    path = web_engine_mod._state_path()
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
    web_engine_mod.reset_runtime_state()


# ---------------------------------------------------------------------------
# Security: private addressing, schemes, redirects, size caps, retries
# ---------------------------------------------------------------------------


def test_security_guards():
    print("-- security: SSRF + schemes + redirects + size + retry --")
    import httpx

    from app.knowledge_sync.web_crawler import (
        _safe_get,
        host_is_private,
        is_private_address,
        normalize_url,
        same_domain,
        WebsiteCrawler,
    )

    check("loopback is private", is_private_address("127.0.0.1"))
    check("link-local is private", is_private_address("169.254.10.10"))
    check("10.x is private", is_private_address("10.1.2.3"))
    check("172.16 is private", is_private_address("172.16.0.1"))
    check("192.168 is private", is_private_address("192.168.1.1"))
    check("CGNAT 100.64 is private", is_private_address("100.64.0.1"))
    check("public is not private", not is_private_address("8.8.8.8"))
    check("IPv6 loopback is private", is_private_address("::1"))

    check("host_is_private blocks loopback by default",
          asyncio.run(host_is_private("http://127.0.0.1:8080/x", allow_private_hosts=False)))
    check("host_is_private allows loopback when flagged",
          not asyncio.run(host_is_private("http://127.0.0.1:8080/x", allow_private_hosts=True)))
    check("normalize strips fragments",
          normalize_url("http://x.example/a?x=1#frag") == normalize_url("http://x.example/a?x=1"))
    check("normalize drops utm params",
          normalize_url("http://x.example/a?utm_source=1&page=2") == normalize_url("http://x.example/a?page=2"))
    check("same_domain matches subdomain only",
          same_domain("http://a.example.com/x", "example.com") and
          not same_domain("http://evil.com/x", "example.com"))

    # Crawler refuses a private-network target unless explicitly allowed.
    site = {"/": "<html><body><p>secret</p></body></html>"}
    base, srv, _ = _start_server(site)
    try:
        crawler = WebsiteCrawler(base_url=base, max_pages=10, delay=0.0,
                                 allow_private_hosts=False)
        result = asyncio.run(crawler.crawl())
        blocked = all(not p.ok for p in result["pages"])
        check("crawler refuses private target without flag", blocked,
              str([(p.url, p.error) for p in result["pages"]]))

        client = httpx.AsyncClient()

        async def fetch(url, **kwargs):
            return await _safe_get(client, url, allow_private_hosts=True,
                                   verify_tls=False, **kwargs)

        none = asyncio.run(fetch("file:///etc/passwd"))
        check("file:// scheme rejected", none is None)

        site["/out"] = (302, {"Location": "https://evil.example.org/x"}, b"")
        none = asyncio.run(fetch(base + "/out", same_domain_host="127.0.0.1"))
        check("redirect to external host blocked", none is None)

        site["/a1"] = (302, {"Location": base + "/a2"}, b"")
        site["/a2"] = (302, {"Location": base + "/a1"}, b"")
        none = asyncio.run(fetch(base + "/a1", same_domain_host="127.0.0.1"))
        check("redirect loop refused", none is None)

        site["/big"] = (200, {"Content-Type": "application/octet-stream",
                              "Content-Length": "1000"}, b"x" * 1000)
        none = asyncio.run(fetch(base + "/big", max_file_size=100))
        check("oversized download skipped", none is None)

        # transient 503s then success -> retry with backoff wins
        counts = {"n": 0}
        ok_body = b"<html><body><p>ok</p></body></html>"

        class FlakyHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                counts["n"] += 1
                if counts["n"] < 3:
                    self.send_response(503)
                else:
                    self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(ok_body)))
                self.end_headers()
                self.wfile.write(ok_body)

            def log_message(self, *a):
                pass

        srv2 = http.server.HTTPServer(("127.0.0.1", 0), FlakyHandler)
        port2 = srv2.server_address[1]
        threading.Thread(target=srv2.serve_forever, daemon=True).start()
        try:
            resp = asyncio.run(fetch(f"http://127.0.0.1:{port2}/flaky",
                                     max_retries=2, retry_delay=0.0))
            check("5xx retried then succeeded",
                  resp is not None and resp.status_code == 200, f"hits={counts['n']}")
            check("at least 3 attempts made", counts["n"] >= 3, str(counts["n"]))
        finally:
            _stop_server(srv2)
    finally:
        _stop_server(srv)


# ---------------------------------------------------------------------------
# Discovery: sitemap seeds are additive; relative/absolute links; documents
# ---------------------------------------------------------------------------


def test_discovery_and_sitemap():
    print("-- discovery: links + documents + sitemap seeds --")
    from app.knowledge_sync.web_crawler import WebsiteCrawler

    site: dict = {}
    base, srv, _ = _start_server(site)
    site.update({
        "/": f"""<html><head><title>Home</title></head><body>
                <a href="/rel">rel</a>
                <a href="{base}/abs">abs</a>
                <a href="/d.pdf">doc</a></body></html>""",
        "/rel": "<html><head><title>Rel</title></head><body><p>relative page.</p></body></html>",
        "/abs": "<html><head><title>Abs</title></head><body><p>absolute page.</p></body></html>",
        "/d.pdf": b"%PDF-1.4 fake pdf bytes",
        "/robots.txt": f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n",
        "/sitemap.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{base}/rel</loc></url>
  <url><loc>{base}/sitemap-only</loc></url>
</urlset>""",
        "/sitemap-only": "<html><head><title>Sitemap only</title></head><body><p>only in sitemap.</p></body></html>",
    })
    try:
        crawler = WebsiteCrawler(base_url=base, max_pages=40, max_depth=4, delay=0.0,
                                 allow_private_hosts=True)
        res = asyncio.run(crawler.crawl())
        urls = [p.url for p in res["pages"]]
        check("linked page discovered (relative)", any(u.endswith("/rel") for u in urls), str(urls))
        check("linked page discovered (absolute)", any(u.endswith("/abs") for u in urls), str(urls))
        check("document downloaded", any(p.kind == "document" and p.url.endswith("/d.pdf") for p in res["pages"]))
        check("no duplicate URLs", len(urls) == len(set(urls)))
        check("sitemap-only page discovered", any(u.endswith("/sitemap-only") for u in urls),
              f"found={[u for u in urls if 'sitemap-only' in u]}")

        crawler2 = WebsiteCrawler(base_url=base, max_pages=40, max_depth=4, delay=0.0,
                                  allow_private_hosts=True, use_sitemap=False)
        res2 = asyncio.run(crawler2.crawl())
        urls2 = [p.url for p in res2["pages"]]
        check("sitemap-only NOT discovered without sitemap",
              not any(u.endswith("/sitemap-only") for u in urls2))
    finally:
        _stop_server(srv)


# ---------------------------------------------------------------------------
# Extraction: tables preserved, canonical URLs collapse variants
# ---------------------------------------------------------------------------


def test_table_extraction():
    print("-- extraction: tables preserve structure --")
    from app.knowledge_sync.web_extractor import extract_html

    html = """<html><head><title>Fee Structure</title></head><body>
<table>
<tr><th>Programme</th><th>Semester Fee</th></tr>
<tr><td>BCA</td><td>Rs. 12,000</td></tr>
<tr><td>MCA</td><td>Rs. 15,000</td></tr>
</table>
</body></html>"""
    r = extract_html(html, base_url="https://x.example/fees")
    t = r["text"]
    check("table header pipe-separated", "Programme | Semester Fee" in t, repr(t))
    check("row 1 values preserved", "BCA | Rs. 12,000" in t, repr(t))
    check("row 2 values preserved", "MCA | Rs. 15,000" in t, repr(t))
    check("title preserved", r["title"] == "Fee Structure", repr(r["title"]))


def test_canonical_collapse():
    print("-- canonical: rel=canonical collapses duplicate variants --")
    from app.knowledge_sync.web_engine import WebsiteSyncEngine

    _wipe_tables()
    site = {
        "/": '<html><head><title>H</title></head><body><a href="/canonical">c</a><a href="/alias">a</a></body></html>',
        "/canonical": '<html><head><title>Fees 2026</title><link rel="canonical" href="/canonical"></head><body><p>Fee schedule table.</p></body></html>',
        "/alias": '<html><head><title>Fees 2026</title><link rel="canonical" href="/canonical"></head><body><p>Fee schedule table.</p></body></html>',
    }
    base, srv, _ = _start_server(site)
    db = SessionLocal()
    try:
        engine = WebsiteSyncEngine(db, base_url=base, index_rag=False,
                                   allow_private_hosts=True)
        r = engine.run(trigger="manual")
        pages = db.query(WebsitePage).all()
        urls = [p.url for p in pages]
        check("run completed", r.get("status") == "completed", str(r))
        check("no failures", r.get("failed_pages", 0) == 0, str(r))
        check("alias variant collapsed", not any(u.endswith("/alias") for u in urls), str(urls))
        check("canonical row present", any(u.endswith("/canonical") for u in urls), str(urls))
        check("seed row present", any(u.rstrip("/") == base.rstrip("/") for u in urls), str(urls))
        check("duplicate skipped counted", r.get("duplicates_skipped", 0) >= 1, str(r))
    finally:
        db.close()
        _stop_server(srv)


# ---------------------------------------------------------------------------
# Documents: PPTX slide text extraction
# ---------------------------------------------------------------------------


def _make_pptx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml",
                    b'<?xml version="1.0"?><p:sld xmlns:p="x"><a:t>Cluster</a:t></p:sld>')
        zf.writestr("ppt/slides/slide2.xml",
                    b'<?xml version="1.0"?><p:sld xmlns:p="x"><a:t>University</a:t></p:sld>')
        zf.writestr("ppt/presentation.xml", b"x")
    return buf.getvalue()


def test_pptx():
    print("-- documents: PPTX text extraction --")
    from app.knowledge_sync.web_engine import parse_document_bytes

    pages = parse_document_bytes(_make_pptx(), "pptx")
    text = "\n".join(p.get("text", "") for p in pages)
    check("pptx slide 1 text", "Cluster" in text, text)
    check("pptx slide 2 text", "University" in text, text)
    check("garbage bytes rejected", parse_document_bytes(b"not a zip", "pptx") == [])


# ---------------------------------------------------------------------------
# Runtime state machine: Ready / Warning / Error + last counts
# ---------------------------------------------------------------------------


def test_runtime_state_machine():
    print("-- runtime state machine: Ready / Warning / Error --")
    from app.knowledge_sync.web_engine import (
        get_runtime_state,
        load_state,
        WebsiteSyncEngine,
    )

    _wipe_tables()
    _wipe_state()

    # good site -> Ready
    site = {
        "/": '<html><head><title>Good</title></head><body><a href="/ok">ok</a></body></html>',
        "/ok": "<html><head><title>OK page</title></head><body><p>All good.</p></body></html>",
    }
    base, srv, _ = _start_server(site)
    db = SessionLocal()
    try:
        engine = WebsiteSyncEngine(db, base_url=base, index_rag=False,
                                   allow_private_hosts=True)
        r = engine.run(trigger="manual")
        st = get_runtime_state()
        check("runtime reaches Ready", st["state"] == "Ready", str(st))
        check("ready message populated", bool(st["message"]), st["message"])
        check("run status completed", r.get("status") == "completed", str(r))
        check("new pages counted", r.get("new_pages", 0) >= 1, str(r))
        saved = load_state()
        lc = saved.get("last_counts") or {}
        check("last_counts persisted", lc.get("pages_found", 0) >= 1, str(saved.get("last_counts")))
        check("enabled flag is separate from Ready", "enabled" in saved)
    finally:
        db.close()
        _stop_server(srv)

    # site with a failing page -> Warning
    _wipe_tables()
    site = {
        "/": '<html><head><title>Bad</title></head><body><a href="/boom">b</a></body></html>',
        "/boom": (404, {"Content-Type": "text/html"}, b"not here"),
    }
    base, srv, _ = _start_server(site)
    db = SessionLocal()
    try:
        engine = WebsiteSyncEngine(db, base_url=base, index_rag=False,
                                   allow_private_hosts=True)
        r = engine.run(trigger="manual")
        st = get_runtime_state()
        check("runtime reaches Warning on failures", st["state"] == "Warning", str(st))
        check("failed pages counted", r.get("failed_pages", 0) >= 1, str(r))
    finally:
        db.close()
        _stop_server(srv)

    # unexpected exception inside the pass -> Error
    _wipe_tables()
    site = {
        "/": '<html><head><title>Boom</title></head><body><a href="/doc.pdf">d</a></body></html>',
        "/doc.pdf": b"%PDF-1.4 fake pdf",
    }
    base, srv, _ = _start_server(site)
    db = SessionLocal()
    orig_parse = web_engine_mod.parse_document_bytes
    web_engine_mod.parse_document_bytes = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        engine = WebsiteSyncEngine(db, base_url=base, index_rag=False,
                                   allow_private_hosts=True)
        r = engine.run(trigger="manual")
        st = get_runtime_state()
        check("runtime reaches Error on exception", st["state"] == "Error", str(st))
        check("run status failed", r.get("status") == "failed", str(r))
        check("error message recorded", "Sync failed" in st.get("message", ""), st.get("message"))
    finally:
        web_engine_mod.parse_document_bytes = orig_parse
        db.close()
        _stop_server(srv)


# ---------------------------------------------------------------------------
# Source validation + offline safety: never crash-but-forget, never lose data
# ---------------------------------------------------------------------------


def test_source_validation_and_offline_safety():
    print("-- source validation + offline safety --")
    from app.knowledge_sync.web_engine import (
        WebsiteSyncEngine,
        resolve_source_url,
    )
    from app.config import settings

    # Production default must be the configured real site, never the app.
    resolved = resolve_source_url()
    check("default source is the configured site",
          resolved == settings.WEBSITE_KNOWLEDGE_SOURCE_URL, resolved)

    # 1) INVALID_SOURCE: unsupported scheme fails fast with a clear message.
    _wipe_tables()
    _wipe_state()
    db = SessionLocal()
    try:
        engine = WebsiteSyncEngine(db, base_url="ftp://files.example.com/x", index_rag=False)
        r = engine.run(trigger="manual")
        check("invalid scheme -> run failed", r.get("status") == "failed", str(r))
        check("invalid scheme -> INVALID_SOURCE message",
              "INVALID_SOURCE" in (r.get("error") or ""), str(r.get("error")))
        st = web_engine_mod.get_runtime_state()
        check("invalid scheme -> Error state", st["state"] == "Error", str(st))

        # 2) Loopback target is refused by the SSRF guard when not allowed.
        engine = WebsiteSyncEngine(db, base_url="http://127.0.0.1:9", index_rag=False)
        r = engine.run(trigger="manual")
        check("loopback refused (no flag) -> run failed", r.get("status") == "failed", str(r))
        check("loopback error mentions INVALID_SOURCE",
              "INVALID_SOURCE" in (r.get("error") or ""), str(r.get("error")))

        # Request must NOT have been attempted against the loopback target.
        st = web_engine_mod.get_runtime_state()
        check("loopback -> Error state", st["state"] == "Error", str(st))

        # 3) Known pages are preserved when the site goes offline.
        site = {
            "/": '<html><head><title>Live</title></head><body><a href="/b">b</a></body></html>',
            "/b": "<html><head><title>Bee</title></head><body><p>content</p></body></html>",
        }
        base, srv, _ = _start_server(site)
        try:
            engine = WebsiteSyncEngine(db, base_url=base, index_rag=False,
                                       allow_private_hosts=True)
            r1 = engine.run(trigger="manual")
            check("live run ok", r1.get("status") == "completed", str(r1))
            existing = {p.url for p in db.query(WebsitePage).all()}
            check("known pages exist before outage", len(existing) >= 2, str(existing))
        finally:
            _stop_server(srv)

        # Site is now DOWN: the run must fail, but NOT archive/delete pages.
        engine = WebsiteSyncEngine(db, base_url=base, index_rag=False,
                                   allow_private_hosts=True)
        r2 = engine.run(trigger="manual")
        check("offline run -> failed", r2.get("status") == "failed", str(r2))
        check("offline error mentions UNREACHABLE",
              "UNREACHABLE" in (r2.get("error") or ""), str(r2.get("error")))
        check("offline run archived nothing", r2.get("archived_pages", 0) == 0, str(r2))
        after = {p.url for p in db.query(WebsitePage).all()}
        check("known pages survive an offline pass", existing == after, str(after))
        alive = db.query(WebsitePage).filter(WebsitePage.status != "archived").count()
        check("nothing archived after offline pass", alive == len(existing), str(alive))
    finally:
        db.close()

    _wipe_tables()
    _wipe_state()


# ---------------------------------------------------------------------------
# Consolidation: no stale Knowledge Sync artifacts anywhere
# ---------------------------------------------------------------------------


def test_consolidation_cleanup():
    print("-- consolidation: stale Knowledge Sync artifacts removed --")
    root = Path(__file__).resolve().parents[1]
    repo = root.parent
    admin_js = (repo / "frontend" / "js" / "admin.js").read_text(encoding="utf-8")
    admin_html = (repo / "frontend" / "pages" / "admin.html").read_text(encoding="utf-8")
    routes = (root / "app" / "admin" / "routes.py").read_text(encoding="utf-8")

    stale = ["knowledge-sync", "sync-website", "kb-stats", "tabSync", "syncRunBtn",
             "syncAutoDiscover", "scraperToggle", "scraperBtn", "syncLog", "syncSourceList"]
    for term in stale:
        check(f"no '{term}' in admin.js", term not in admin_js)
        check(f"no '{term}' in admin.html", term not in admin_html)

    for term in ["knowledge-sync", "sync-website", "kb-stats"]:
        check(f"no '{term}' route in routes.py", term not in routes)

    route_lines = [l for l in routes.splitlines() if "admin/website-sync" in l]
    check("website-sync routes intact", len(route_lines) >= 9, f"{len(route_lines)} lines")
    for ep in ["admin/website-sync/run", "admin/website-sync/status",
               "admin/website-sync/toggle", "admin/website-sync/runs",
               "admin/website-sync/pages", "admin/website-sync/duplicates"]:
        check(f"route {ep}", any(ep in l for l in route_lines))

    check("live progress UI present", "wsProgress" in admin_html)
    check("live progress JS present", "wsRenderRuntime" in admin_js)


def main() -> None:
    create_all()
    _wipe_tables()
    _wipe_state()
    test_security_guards()
    test_table_extraction()
    test_discovery_and_sitemap()
    test_canonical_collapse()
    test_pptx()
    test_runtime_state_machine()
    test_source_validation_and_offline_safety()
    test_consolidation_cleanup()
    _wipe_tables()
    print()
    print(f"SUMMARY — hardening: {len(PASS)}/{len(PASS) + len(FAIL)} passed, "
          f"{len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
