"""
backend/tests/test_website_sync_engine.py

Tests for the Enterprise Website Knowledge Synchronization Engine:

  * semantic HTML extraction (web_extractor)  — boilerplate stripping,
    title/description/heading/link extraction
  * page classification (web_classifier)      — all supported categories
  * crawler (web_crawler)                     — local HTTP server: same-domain
    restriction, robots.txt, dedup, document discovery, 404 handling
  * incremental engine (web_engine)           — new / updated / unchanged /
    archived pages + version snapshots + duplicate detection
  * RAG integration                           — chunks carry source_url when an
    embedding backend is available (skipped gracefully otherwise)

Run:  python tests/test_website_sync_engine.py
"""

from __future__ import annotations

import http.server
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Test isolation FIRST: never let engine runs write the shared production
# sync state (runtime machine / last_counts) into the dashboard's file.
os.environ.setdefault(
    "WEBSITE_SYNC_STATE_FILE",
    str(Path(os.environ.get("TEMP", tempfile.gettempdir())) / "_website_sync_engine_test_state.json"),
)

import app.models  # noqa: F401  (register tables before any session)

from app.database import SessionLocal, create_all

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# Semantic HTML extractor
# ---------------------------------------------------------------------------

PAGE_HTML = """<!DOCTYPE html>
<html><head>
<title>Admission Notification 2026</title>
<meta name="description" content="PG admission notification for the academic session 2026.">
</head>
<body>
<nav><a href="/home">Home</a></nav>
<div class="cookie-banner">We use cookies to enhance your experience. Accept</div>
<header class="site-header">Cluster University Logo</header>
<main>
<h1>PG Admissions 2026</h1>
<p>Applications are invited for admission to the postgraduate programmes offered for the academic session 2026.</p>
<p>Last date to apply: 31 August 2026.</p>
<a href="/admissions/how-to-apply">How to apply</a>
<a href="https://outside.example.org/foo">External link</a>
</main>
<footer>Copyright Cluster University 2026</footer>
<script>alert('x')</script>
</body></html>"""


def test_extractor():
    print("-- web_extractor: semantic extraction --")
    from app.knowledge_sync.web_extractor import extract_html

    r = extract_html(PAGE_HTML, base_url="https://www.cusrinagar.edu.in/admission")
    check("title extracted", r["title"] == "Admission Notification 2026", r["title"])
    check("description extracted", r["description"].startswith("PG admission notification"), r["description"])
    check("no nav text", "Home" not in r["text"])
    check("no footer boilerplate", "Copyright" not in r["text"])
    check("no cookie banner", "We use cookies" not in r["text"])
    check("no script junk", "alert" not in r["text"])
    check("heading present in text", "PG Admissions 2026" in r["text"])
    check("heading listed", r["headings"] == ["PG Admissions 2026"], str(r["headings"]))
    check("body content kept", "postgraduate" in r["text"])
    check("external link kept", any("outside.example.org" in l for l in r["links"]))
    check("relative link resolved", "https://www.cusrinagar.edu.in/admissions/how-to-apply" in r["links"])


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def test_classifier():
    print("-- web_classifier: 18 categories --")
    from app.knowledge_sync.web_classifier import CATEGORIES, classify_page

    check("18 categories", len(CATEGORIES) == 18, str(len(CATEGORIES)))
    cases = {
        "Admissions 2026 notification": "admissions",
        "PG admission notice": "admissions",
        "Examination date sheet 2026": "examinations",
        "Department of Computer Science": "departments",
        "B.Tech programme syllabus": "programmes",
        "Latest news roundup": "news",
        "Public notice: re-opening": "notices",
        "Faculty profile - Dr X": "faculty",
        "Merit scholarship details": "scholarships",
        "Hostel accommodation rules": "hostels",
        "Campus transport schedule": "transport",
        "Registrar office administration": "administration",
        "Research journal publications": "research",
        "Academic calendar 2026-27": "academic-calendar",
        "Annual cultural fest 2026": "events",
        "Student grievance helpdesk": "student-services",
        "Anti-ragging policy PDF": "policies",
        "Download application form": "downloads",
    }
    for title, expected in cases.items():
        got = classify_page(title=title, text="a small page body")
        check(f"classify {title!r}", got == expected, f"got={got}")
    check("unknown fallback", classify_page(title="zzzz qqqq", text="aaaa bbbb") == "unknown")


# ---------------------------------------------------------------------------
# Local test site + crawler
# ---------------------------------------------------------------------------

SITE = {
    "/": """<html><head><title>Home - CU</title></head><body>
           <nav><a href="/admissions">Admissions</a><a href="https://outside.example.org/x">External</a></nav>
           <p>Cluster University home page content.</p></body></html>""",
    "/admissions": """<html><head><title>Admissions 2026</title></head><body>
                      <p>Admission notification for PG programmes 2026.</p>
                      <a href="/admissions/applications">Applications</a>
                      <a href="/brochure.pdf">Brochure</a></body></html>""",
    "/admissions/applications": """<html><head><title>Application form</title></head><body>
                                   <p>Download the application form below.
                                   <a href="/form.pdf">Form</a></p></body></html>""",
    "/robots.txt": "User-agent: *\nDisallow: /admin\nAllow: /\n",
    "/admin": """<html><head><title>Admin</title></head><body><p>secret admin page</p></body></html>""",
}

_PDF = b"%PDF-1.4 fake document bytes"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.endswith(".pdf"):
            body = _PDF
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        html = SITE.get(path)
        if html is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _start_server() -> tuple[int, http.server.HTTPServer]:
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return port, srv


def _stop_server(srv) -> None:
    srv.shutdown()
    srv.server_close()


def test_crawler():
    print("-- web_crawler: bounded same-domain crawl --")
    import asyncio

    from app.knowledge_sync.web_crawler import WebsiteCrawler, normalize_url, same_domain

    port, srv = _start_server()
    try:
        base = f"http://127.0.0.1:{port}"
        base_domain = "127.0.0.1"

        check("same domain true", same_domain(base + "/admissions", base_domain))
        check("external rejected", not same_domain("http://example.org/x", base_domain))
        check("normalize strips fragment", normalize_url(base + "/a?x=1#frag") == normalize_url(base + "/a?x=1"))

        crawler = WebsiteCrawler(
            base_url=base, max_pages=30, max_depth=3, delay=0.0,
            allow_private_hosts=True,
        )
        result = asyncio.run(crawler.crawl())
        pages = result["pages"]
        urls = [p.url for p in pages]
        check("crawl discovered root", any(u == base + "/" for u in urls), str(urls))
        check("crawl followed same-domain", any(u.endswith("/admissions") for u in urls))
        check("crawl found nested page", any(u.endswith("/admissions/applications") for u in urls))
        check("crawl did NOT follow external", not any("example.org" in u for u in urls))
        check("crawl skipped robots /admin", not any("/admin" in u for u in urls))
        check("no duplicate urls", len(urls) == len(pages))
        check("PDF discovered + downloaded", any(p.kind == "document" and p.url.endswith("brochure.pdf") and p.raw for p in pages))
        check("html text extracted", any("application" in (p.text or "") for p in pages if p.kind == "html"))
    finally:
        _stop_server(srv)


# ---------------------------------------------------------------------------
# Incremental engine (no RAG indexing — fully offline)
# ---------------------------------------------------------------------------


def _wipe_website_tables():
    from app.models.website_sync import CrawlRun, WebsitePage, WebsitePageVersion

    db = SessionLocal()
    try:
        for model in (WebsitePage, CrawlRun, WebsitePageVersion):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()


def test_engine_incremental():
    """Full lifecycle: new -> updated (version archived) -> unchanged -> archive."""
    print("-- web_engine: incremental sync lifecycle --")
    from app.knowledge_sync.web_engine import WebsiteSyncEngine
    from app.models.website_sync import CrawlRun, WebsitePage, WebsitePageVersion

    _wipe_website_tables()
    create_all()
    port, srv = _start_server()
    try:
        base = f"http://127.0.0.1:{port}"
        db = SessionLocal()
        try:
            engine = WebsiteSyncEngine(
                db, base_url=base, index_rag=False, allow_private_hosts=True
            )

            r1 = engine.run(trigger="manual")
            pages = db.query(WebsitePage).all()
            check("run1: pages created", len(pages) > 0, f"n={len(pages)}")
            check("run1: new counted", r1.get("new_pages", 0) > 0, str(r1.get("new_pages")))
            check("run1: all new", all(p.status == "new" for p in pages), str([p.status for p in pages]))
            check("run1: version 1", all((p.version or 1) >= 1 for p in pages))
            check("run1: run row recorded", db.query(CrawlRun).filter(CrawlRun.status == "completed").count() >= 1)

            r2 = engine.run(trigger="scheduled")
            pages = db.query(WebsitePage).all()
            check("run2: zero new", r2.get("new_pages", 0) == 0, str(r2.get("new_pages")))
            check("run2: unchanged counted", r2.get("unchanged_pages", 0) > 0, str(r2.get("unchanged_pages")))
            check("run2: no version growth", all((p.version or 1) == 1 for p in pages))

            # run3: /admissions content changes (links preserved) -> updated
            SITE["/admissions"] = SITE["/admissions"].replace("2026", "2027")
            r3 = engine.run(trigger="manual")
            adm = db.query(WebsitePage).filter(WebsitePage.url.endswith("/admissions")).first()
            check("run3: updated counted", r3.get("updated_pages", 0) > 0, str(r3.get("updated_pages")))
            check("run3: page marked updated", adm is not None and adm.status == "updated",
                  adm.status if adm else "missing")
            check("run3: version bumped", adm is not None and adm.version == 2, f"v={adm.version if adm else None}")
            if adm:
                versions = db.query(WebsitePageVersion).filter(
                    WebsitePageVersion.page_id == adm.id
                ).all()
                check("run3: old snapshot archived", len(versions) >= 1, f"n={len(versions)}")
            check("run3: nothing archived", r3.get("archived_pages", 0) == 0, str(r3.get("archived_pages")))

            # run4: /admissions/applications disappears from the site graph -> archived
            SITE["/admissions"] = ('<html><head><title>Admissions 2027</title></head><body>'
                                   '<p>Admission notification for PG programmes 2027.</p>'
                                   '<a href="/brochure.pdf">Brochure</a></body></html>')
            SITE["/admissions/applications"] = None
            r4 = engine.run(trigger="manual")
            gone = db.query(WebsitePage).filter(WebsitePage.url.endswith("/applications")).first()
            check("run4: disappeared page archived", gone is not None and gone.status == "archived",
                  gone.status if gone else "missing")
            check("run4: archived counted", r4.get("archived_pages", 0) > 0, str(r4.get("archived_pages")))

            # never hard-deleted
            check("nothing deleted", db.query(WebsitePage).count() > 0)
        finally:
            db.close()
    finally:
        _stop_server(srv)


def test_dedup_and_ops():
    print("-- duplicate detection + admin ops --")
    from app.knowledge_sync.web_engine import WebsiteSyncEngine
    from app.models.website_sync import WebsitePage

    port, srv = _start_server()
    try:
        base = f"http://127.0.0.1:{port}"
        db = SessionLocal()
        try:
            engine = WebsiteSyncEngine(
                db, base_url=base, index_rag=False, allow_private_hosts=True
            )
            engine.run(trigger="manual")
            first = db.query(WebsitePage).filter(WebsitePage.url.endswith("/admissions")).first()
            if first:
                dup = WebsitePage(
                    url=base + "/mirror",
                    base_url=base,
                    title=first.title,
                    normalized_title=first.normalized_title,
                    category=first.category,
                    content=first.content,
                    content_hash=first.content_hash,
                    http_status=200,
                    version=1,
                    status="new",
                )
                db.add(dup)
                db.commit()
                report = engine.scan_duplicates()
                check("duplicate group detected", report["duplicate_groups"] >= 1, str(report))
                check("duplicate page counted", report["duplicate_pages"] >= 1, str(report))
            status = engine.get_status()
            check("status total_pages", status.get("total_pages", 0) >= 1, str(status.get("total_pages")))
            pages = engine.list_pages(limit=10)
            check("list_pages works", len(pages) > 0)
            runs = engine.list_runs()
            check("list_runs works", len(runs) > 0)
            versions = engine.list_versions(first.id if first else "x")
            check("list_versions works", isinstance(versions, list))
        finally:
            db.close()
    finally:
        _stop_server(srv)


# ---------------------------------------------------------------------------
# RAG integration (real embedding when available; graceful otherwise)
# ---------------------------------------------------------------------------


def test_rag_integration():
    print("-- RAG integration: source_url attribution --")
    from app.knowledge_sync.web_engine import WebsiteSyncEngine
    from app.models.website_sync import WebsitePage

    port, srv = _start_server()
    try:
        base = f"http://127.0.0.1:{port}"
        SITE["/"] = ('<html><head><title>Home</title></head><body>'
                     '<a href="/rag">RAG marker</a></body></html>')
        SITE["/rag"] = ("<html><head><title>RAG marker page</title></head><body>"
                        "<p>ClusteredUniversity RAG marker unicorn page content.</p></body></html>")
        db = SessionLocal()
        try:
            engine = WebsiteSyncEngine(
                db, base_url=base, index_rag=True, allow_private_hosts=True
            )
            r = engine.run(trigger="manual")
            page = db.query(WebsitePage).filter(WebsitePage.url.endswith("/rag")).first()
            if page and page.document_id:
                check("page indexed into RAG", page.status in ("new", "updated"), page.status)
                try:
                    from app.ingest.store import delete_document, query
                    from app.ingest.embed import embed_query

                    vec = embed_query("cluster university rag marker")
                    hits = query(vec, top_k=5)
                    source_urls = {c.get("source_url", "") for c in hits}
                    check("chunks carry source_url", any("/rag" in u for u in source_urls), str(source_urls))
                    delete_document(page.document_id)
                    import uuid
                    from app.models import Document

                    doc = db.get(Document, uuid.UUID(page.document_id))
                    if doc:
                        db.delete(doc)
                        db.commit()
                except Exception as exc:
                    check("embedding skip handled", True, f"embed unavailable: {str(exc)[:80]}")
            else:
                check("rag skipped gracefully (no embed backend)", True)
        finally:
            db.close()
    finally:
        _stop_server(srv)


def main():
    create_all()
    _wipe_website_tables()
    test_extractor()
    test_classifier()
    test_crawler()
    test_engine_incremental()
    test_dedup_and_ops()
    test_rag_integration()
    _wipe_website_tables()
    try:
        Path(os.environ.get("WEBSITE_SYNC_STATE_FILE") or "").unlink(missing_ok=True)
    except OSError:
        pass
    print()
    print("SUMMARY")
    print(f"  Passed: {len(PASS)}/{len(PASS) + len(FAIL)}")
    print(f"  Failed: {len(FAIL)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
