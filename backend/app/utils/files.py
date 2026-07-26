"""
backend/app/utils/files.py

Safe file handling for uploads:
  - extension validation
  - size limits
  - filename sanitization (prevents path traversal / clobbering)
  - text extraction dispatch per file type
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from app.config import settings
from fastapi import HTTPException

_ALLOWED_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\. ]*$")


def validate_upload(filename: str, size: int) -> str:
    """Validate extension + size. Returns the lowercased extension (no dot)."""
    if not filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in settings.allowed_extensions_list:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '.{ext}'. Allowed: {', '.join(settings.allowed_extensions_list)}",
        )
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({size} bytes). Max {settings.MAX_UPLOAD_MB} MB.",
        )
    return ext


def sanitize_filename(original: str) -> str:
    """Produce a safe stored filename: <uuid>_<sanitized>.<ext>."""
    base, dot, ext = original.rpartition(".")
    clean = re.sub(r"[^A-Za-z0-9_\- ]", "_", base).strip().replace(" ", "_")
    clean = clean[:60] or "document"
    token = uuid.uuid4().hex[:12]
    ext = ext.lower() if dot else ""
    return f"{token}_{clean}.{ext}" if ext else f"{token}_{clean}"


def ensure_dir(path: str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def coerce_uuid(value: str):
    """Safely coerce a string to a UUID, returning the original string if invalid."""
    import uuid
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return value


def extract_text(path: str, ext: str) -> list[dict]:
    """
    Extract text from a file. Returns a list of page dicts:
        [{"page": int, "text": str}, ...]
    For non-paginated formats (txt/md/docx may be single-section) page is sequential.
    """
    ext = ext.lower()
    if ext == "pdf":
        return _extract_pdf(path)
    if ext == "docx":
        return _extract_docx(path)
    if ext in ("txt", "md"):
        return _extract_text_plain(path)
    raise HTTPException(status_code=400, detail=f"Unsupported extension: {ext}")


def _extract_pdf(path: str) -> list[dict]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail="PDF library unavailable") from exc

    pages: list[dict] = []
    try:
        reader = PdfReader(path)
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append({"page": i, "text": text})
    except Exception as exc:  # pragma: no cover - malformed pdf
        raise HTTPException(status_code=422, detail=f"Could not read PDF: {exc}") from exc
    return pages


def _extract_docx(path: str) -> list[dict]:
    try:
        from docx import Document as DocxDocument
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail="DOCX library unavailable") from exc

    try:
        doc = DocxDocument(path)
        paras = [p.text for p in doc.paragraphs if p.text.strip()]
        full = "\n".join(paras)
        # DOCX has no reliable page numbers via python-docx; present as one section.
        return [{"page": 1, "text": full}]
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=422, detail=f"Could not read DOCX: {exc}") from exc


def _extract_text_plain(path: str) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=422, detail=f"Could not read file: {exc}") from exc
    return [{"page": 1, "text": text}]
