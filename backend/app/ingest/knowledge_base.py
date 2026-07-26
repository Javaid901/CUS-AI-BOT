"""
backend/app/ingest/knowledge_base.py

Knowledge Base Downloader & Sync.

Crawls the official Cluster University Srinagar website (cusrinagar.edu.in)
for official PDF documents, downloads new ones, and organizes them into
backend/data/documents/ with category folders.

Usage:
    python -c "from app.ingest.knowledge_base import sync_all; sync_all()"
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from app.utils.logging import log

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "documents"
MANIFEST_FILE = DATA_DIR / ".manifest.json"
CRAWL_INTERVAL = 1.0  # seconds between requests to be polite

# Known PDF URLs from the official CUS website (verified via crawling)
KNOWN_PDFS = {
    "Act & Statutes": [
        "https://www.cusrinagar.edu.in/FolderManager/TheAct/statutes.pdf",
    ],
    "Admissions": [
        "https://www.cusrinagar.edu.in/FolderManager/Notification/DateExtensionNoticePGProgrammesIGB.EdMEd.pdf",
        "https://www.cusrinagar.edu.in/FolderManager/Notification/SyllabusPG2026.pdf",
        "https://www.cusrinagar.edu.in/FolderManager/Notification/AdmissionNotificationNo.02forDYDprogrammes.pdf",
        "https://www.cusrinagar.edu.in/FolderManager/Notification/AdmissionNotificationforPGProgrammes..pdf",
        "https://www.cusrinagar.edu.in/FolderManager/Notification/CommencementofClasswork4thsem.P.G2024.pdf",
        "http://www.cusrinagar.edu.in/foldermanager/notification/lateralentrysyllabus.pdf",
    ],
    "Notices": [
        "https://www.cusrinagar.edu.in/FolderManager/Notification/AdmissionNotificationNo.02forDYDprogrammes.pdf",
        "https://www.cusrinagar.edu.in/FolderManager/Notification/DateExtensionNoticePGProgrammesIGB.EdMEd.pdf",
    ],
    "Syllabus": [
        "https://www.cusrinagar.edu.in/FolderManager/Notification/SyllabusPG2026.pdf",
        "http://www.cusrinagar.edu.in/foldermanager/notification/lateralentrysyllabus.pdf",
    ],
    "Previous_Papers": [
        "https://www.cusrinagar.edu.in/FolderManager/IGPGEntranceSyllabus_Papers/PreviousPapers/2019/IMCA.pdf",
        "https://www.cusrinagar.edu.in/FolderManager/IGPGEntranceSyllabus_Papers/PreviousPapers/2018/BCA.pdf",
        "https://www.cusrinagar.edu.in/FolderManager/IGPGEntranceSyllabus_Papers/PreviousPapers/2019/biohem.pdf",
        "https://www.cusrinagar.edu.in/FolderManager/IGPGEntranceSyllabus_Papers/PreviousPapers/2018/Biochemistry.pdf",
    ],
}

PDF_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt"}


def _load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"files": {}}


def _save_manifest(manifest: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _download_pdf(url: str, category: str, manifest: dict) -> bool:
    """Download a PDF if it's new/changed. Returns True if downloaded."""
    url_clean = url.strip()
    parsed = urlparse(url_clean)
    filename = os.path.basename(parsed.path)
    if not filename:
        filename = f"document_{_file_hash(url_clean.encode())}.pdf"
    if not filename.endswith(tuple(PDF_EXTENSIONS)):
        filename += ".pdf"

    cat_dir = DATA_DIR / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    dest = cat_dir / filename

    # Check manifest for existing hash
    existing = manifest["files"].get(url_clean, {})

    try:
        client = httpx.Client(timeout=60.0, follow_redirects=True)
        resp = client.get(url_clean)
        resp.raise_for_status()
        data = resp.content
        if not data or len(data) < 100:
            log.warning("Skipping %s — too small (%d bytes)", url_clean, len(data))
            return False

        fhash = _file_hash(data)
        if existing.get("hash") == fhash:
            log.info("Skipping %s — unchanged", url_clean)
            return False

        dest.write_bytes(data)
        manifest["files"][url_clean] = {
            "hash": fhash,
            "size": len(data),
            "category": category,
            "filename": filename,
            "url": url_clean,
            "downloaded_at": time.time(),
        }
        _save_manifest(manifest)
        log.info("Downloaded %s -> %s (%d bytes)", url_clean, dest, len(data))
        return True
    except Exception as exc:
        log.warning("Failed to download %s: %s", url_clean, exc)
        return False


def _crawl_website(manifest: dict) -> list[tuple[str, str]]:
    """Crawl the CUS website for PDF links. Returns [(url, category), ...]."""
    found: list[tuple[str, str]] = []
    base_url = "https://www.cusrinagar.edu.in"
    visited = set()
    to_visit = {base_url}
    pdf_urls = set()

    client = httpx.Client(timeout=30.0, follow_redirects=True, verify=False)

    while to_visit and len(visited) < 50:
        url = to_visit.pop()
        if url in visited:
            continue
        visited.add(url)
        try:
            resp = client.get(url, timeout=15.0)
            if resp.status_code != 200:
                continue
            text = resp.text
            # Find all links
            for match in re.finditer(r'href=["\'](https?://[^"\']+)["\']', text, re.IGNORECASE):
                link = match.group(1)
                if link in visited or link in to_visit:
                    continue
                parsed = urlparse(link)
                # Only stay on the CUS domain
                if "cusrinagar.edu.in" not in parsed.netloc and "cusrinagar" not in parsed.netloc:
                    continue
                if any(link.lower().endswith(ext) for ext in PDF_EXTENSIONS):
                    pdf_urls.add(link)
                elif link.startswith(base_url) and len(visited) < 50:
                    to_visit.add(link)
            time.sleep(0.5)
        except Exception as exc:
            log.debug("Crawl error %s: %s", url, exc)

    log.info("Crawl found %d PDFs", len(pdf_urls))
    # Categorize by URL path
    for url in pdf_urls:
        path = url.lower()
        if "syllabus" in path or "syllabi" in path:
            found.append((url, "Syllabus"))
        elif "admission" in path or "admision" in path or "prospectus" in path:
            found.append((url, "Admissions"))
        elif "notice" in path or "notification" in path:
            found.append((url, "Notices"))
        elif "act" in path or "statute" in path:
            found.append((url, "Act_Statutes"))
        elif "exam" in path or "result" in path:
            found.append((url, "Examinations"))
        elif "fee" in path:
            found.append((url, "Fee_Structure"))
        elif "scholarship" in path:
            found.append((url, "Scholarships"))
        elif "previous" in path or "paper" in path:
            found.append((url, "Previous_Papers"))
        else:
            found.append((url, "General"))
    return found


def sync_all() -> dict:
    """
    Main sync function. Crawls website, downloads new PDFs, returns summary.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest()
    stats = {"downloaded": 0, "skipped": 0, "failed": 0, "categories": {}}

    # Phase 1: Download known PDFs from curated list
    for category, urls in KNOWN_PDFS.items():
        for url in urls:
            if _download_pdf(url, category, manifest):
                stats["downloaded"] += 1
                stats["categories"][category] = stats["categories"].get(category, 0) + 1
            else:
                stats["skipped"] += 1

    # Phase 2: Crawl website for new PDFs
    log.info("Crawling CUS website for new documents...")
    found = _crawl_website(manifest)
    for url, category in found:
        if url not in manifest["files"]:
            if _download_pdf(url, category, manifest):
                stats["downloaded"] += 1
                stats["categories"][category] = stats["categories"].get(category, 0) + 1
            else:
                stats["failed"] += 1
        else:
            stats["skipped"] += 1

    _save_manifest(manifest)

    # Count files
    stats["total_files"] = len(manifest["files"])
    stats["data_dir"] = str(DATA_DIR)
    log.info(
        "Sync complete: %d downloaded, %d skipped, %d failed. Total: %d files",
        stats["downloaded"],
        stats["skipped"],
        stats["failed"],
        stats["total_files"],
    )
    return stats


def get_knowledge_stats() -> dict:
    """Return knowledge base statistics without syncing."""
    manifest = _load_manifest()
    files = manifest.get("files", {})
    total_size = sum(f.get("size", 0) for f in files.values())
    categories: dict[str, int] = {}
    for f in files.values():
        cat = f.get("category", "Unknown")
        categories[cat] = categories.get(cat, 0) + 1
    return {
        "total_files": len(files),
        "total_size_bytes": total_size,
        "categories": categories,
        "data_dir": str(DATA_DIR),
    }


if __name__ == "__main__":
    result = sync_all()
    print(json.dumps(result, indent=2))
