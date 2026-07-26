from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.logging import log


def _full_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_year(url_or_path: str, filename: str = "") -> int | None:
    """Extract a 4-digit year from url/path/filename."""
    combined = f"{url_or_path} {filename}"
    matches = re.findall(r"(?:^|[\s_\-/])(19[5-9]\d|20[0-2]\d)(?:$|[\s_\-\.])", combined)
    if matches:
        return int(matches[0])
    return None


class SyncManifest:
    """Persistent JSON manifest tracking all synced files.

    Tracks SHA256 hash, URL, download time, source, and version year.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or settings.SYNC_MANIFEST_PATH)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning("Corrupt sync manifest at %s, starting fresh", self.path)
        return {"version": 2, "files": {}, "sources": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")

    def is_duplicate(self, url: str, data: bytes) -> bool:
        """True if this URL + content hash already exists."""
        fhash = _full_hash(data)
        existing = self._data["files"].get(url)
        if existing and existing.get("hash") == fhash:
            return True
        # Also check if any file has this exact hash
        for entry in self._data["files"].values():
            if entry.get("hash") == fhash:
                return True
        return False

    def register(
        self,
        url: str,
        path: str,
        data: bytes,
        *,
        source: str = "manual",
        category: str = "General",
        year: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register a downloaded file in the manifest."""
        fhash = _full_hash(data)
        if year is None:
            year = _extract_year(url, Path(path).name)
        entry = {
            "hash": fhash,
            "path": path,
            "size": len(data),
            "source": source,
            "category": category,
            "year": year,
            "url": url,
            "downloaded_at": time.time(),
            "ingested": False,
            "metadata": metadata or {},
        }
        self._data["files"][url] = entry
        self.save()
        return entry

    def mark_ingested(self, url: str, document_id: str) -> None:
        entry = self._data["files"].get(url)
        if entry:
            entry["ingested"] = True
            entry["document_id"] = document_id
            self.save()

    def mark_reviewed(self, url: str) -> None:
        entry = self._data["files"].get(url)
        if entry:
            entry["reviewed"] = True
            self.save()

    def pending_review(self) -> list[dict[str, Any]]:
        return [
            {**v, "url": k}
            for k, v in self._data["files"].items()
            if not v.get("reviewed")
        ]

    def pending_ingestion(self) -> list[dict[str, Any]]:
        return [
            {**v, "url": k}
            for k, v in self._data["files"].items()
            if not v.get("ingested") and v.get("reviewed", True)
        ]

    @property
    def files(self) -> dict[str, Any]:
        return dict(self._data["files"])

    @property
    def stats(self) -> dict[str, Any]:
        files = self._data["files"]
        total = len(files)
        ingested = sum(1 for f in files.values() if f.get("ingested"))
        reviewed = sum(1 for f in files.values() if f.get("reviewed"))
        pending = total - reviewed
        total_size = sum(f.get("size", 0) for f in files.values())
        years: set[int | None] = set()
        categories: dict[str, int] = {}
        for f in files.values():
            years.add(f.get("year"))
            cat = f.get("category", "Unknown")
            categories[cat] = categories.get(cat, 0) + 1
        return {
            "total_files": total,
            "ingested": ingested,
            "reviewed": reviewed,
            "pending_review": pending,
            "total_size_bytes": total_size,
            "years": sorted([y for y in years if y is not None]),
            "categories": categories,
        }
