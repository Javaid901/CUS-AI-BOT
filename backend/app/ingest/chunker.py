"""
backend/app/ingest/chunker.py

Text cleaning and semantic-aware chunking.

Strategy:
  - Normalize whitespace, strip control characters, collapse repeated blank lines.
  - Split into overlapping character chunks with sentence boundary awareness.
  - Enrich metadata with headings, section info, and document type where detectable.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.config import settings

_WS = re.compile(r"[ \t]+")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_BLANK = re.compile(r"\n{3,}")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.?!])\s+(?=[A-Z0-9])")
_HEADING_RE = re.compile(r"^(#{1,3}\s+|\d+[\.\)]\s+|[A-Z][^a-z\n]{2,50}:)", re.MULTILINE)
# Additional heading patterns: common section headers in university docs
_SECTION_HEADING_RE = re.compile(
    r"^\s*("
    r"eligibility\s+(criteria|requirements?)?"
    r"|fee\s+(structure|details|particulars)"
    r"|admission\s+(process|procedure|criteria|requirements?)"
    r"|important\s+(dates|instructions|information)"
    r"|documents?\s+(required|needed|enclosed)"
    r"|course\s+(structure|details|duration)"
    r"|programme\s+(structure|details|duration)"
    r"|duration|intake|seats"
    r"|examination\s+(scheme|pattern|details)"
    r"|subjects?\s+(offered|taught)"
    r"|specializations?\s+(offered|available)"
    r"|placement|career|opportunities"
    r"|scholarship|financial\s+assistance"
    r"|hostel|accommodation"
    r"|facilities|infrastructure"
    r"|faculty|teaching\s+staff"
    r"|contact|address"
    r"|how\s+to\s+apply|application\s+procedure"
    r"|note:|important:|please\s+note:"
    r")\s*[:.\n]",
    re.IGNORECASE | re.MULTILINE,
)


def chunk_hash(text: str) -> str:
    """SHA256 hex digest of chunk content (for dedup)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = _CTRL.sub(" ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()


def _detect_headings(text: str) -> list[dict[str, Any]]:
    """Extract heading positions for metadata enrichment.
    
    Detects both standard markdown-style headings and common
    section headers found in university documents.
    """
    headings: list[dict[str, Any]] = []
    seen_positions: set[int] = set()

    # Standard heading patterns
    for match in _HEADING_RE.finditer(text):
        pos = match.start()
        if pos not in seen_positions:
            headings.append({
                "text": match.group(0).strip()[:60],
                "pos": pos,
            })
            seen_positions.add(pos)

    # Section-specific heading patterns
    for match in _SECTION_HEADING_RE.finditer(text):
        pos = match.start()
        if pos not in seen_positions:
            headings.append({
                "text": match.group(0).strip()[:60],
                "pos": pos,
            })
            seen_positions.add(pos)

    headings.sort(key=lambda h: h["pos"])
    return headings


def chunk_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    pages: [{"page": int, "text": str}, ...]
    Returns: [{"page_number": int, "content": str, "chunk_index": int,
               "char_start": int, "char_end": int, "heading": str | None,
               "section": str | None}, ...]
    """
    size = max(100, settings.CHUNK_SIZE)
    overlap = max(0, min(settings.CHUNK_OVERLAP, size - 1))
    out: list[dict[str, Any]] = []
    idx = 0

    for pg in pages:
        page_no = pg.get("page") or 1
        text = clean_text(pg.get("text", ""))
        if not text:
            continue

        n = len(text)
        start = 0
        current_heading = None
        headings = _detect_headings(text)
        hi = 0

        while start < n:
            end = min(start + size, n)

            # Try to break at a sentence boundary near the chunk end
            if end < n:
                tail = text[max(start, end - 60):end + 60]
                breaks = list(_SENTENCE_BOUNDARY.finditer(tail))
                if breaks:
                    best = None
                    for b in breaks:
                        abs_pos = max(start, end - 60) + b.start()
                        if abs_pos <= end + 20:
                            best = abs_pos + 1
                    if best and best > start:
                        end = min(best, n)

            piece = text[start:end].strip()
            if piece:
                # Find the heading active at this position
                while hi < len(headings) and headings[hi]["pos"] < start:
                    current_heading = headings[hi]["text"]
                    hi += 1

                chunk = {
                    "page_number": page_no,
                    "content": piece,
                    "chunk_index": idx,
                    "char_start": start,
                    "char_end": end,
                    "heading": current_heading,
                    "sha256": chunk_hash(piece),
                }
                out.append(chunk)
                idx += 1

            if end >= n:
                break
            start = max(end - overlap, start + 1)
    return out
