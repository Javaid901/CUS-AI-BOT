from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from pathlib import Path

from app.utils.logging import log

_MAX_ENTRIES = 200_000


class EmbeddingCache:
    """Persistent cache mapping SHA256(chunk_text) → embedding vector."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or "./data/embed_cache.json")
        self._cache: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                raw = self.path.read_text(encoding="utf-8")
                self._cache = json.loads(raw) if raw.strip() else {}
                log.info("Embedding cache loaded (%d entries)", len(self._cache))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Embedding cache corrupt, starting fresh: %s", exc)
                self._cache = {}

    def _do_save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._cache), encoding="utf-8")

    def _key(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def get(self, text: str) -> list[float] | None:
        key = self._key(text)
        with self._lock:
            return self._cache.get(key)

    async def set(self, text: str, vector: list[float]) -> None:
        key = self._key(text)
        with self._lock:
            self._cache[key] = vector
            self._dirty = True
            self._prune()

    def _prune(self) -> None:
        """Bound memory/disk growth — drop oldest entries past the cap."""
        while len(self._cache) > _MAX_ENTRIES:
            self._cache.pop(next(iter(self._cache)))

    async def has(self, text: str) -> bool:
        key = self._key(text)
        with self._lock:
            return key in self._cache

    async def get_many(
        self, texts: list[str]
    ) -> tuple[list[int], list[list[float]]]:
        """Return (indices_of_hits, embeddings_for_hits) aligned with input."""
        hits_idx: list[int] = []
        hits_emb: list[list[float]] = []
        with self._lock:
            for i, text in enumerate(texts):
                key = self._key(text)
                vec = self._cache.get(key)
                if vec is not None:
                    hits_idx.append(i)
                    hits_emb.append(vec)
        return hits_idx, hits_emb

    async def set_many(self, texts: list[str], vectors: list[list[float]]) -> None:
        with self._lock:
            for text, vec in zip(texts, vectors):
                key = self._key(text)
                self._cache[key] = vec
            self._dirty = True
            self._prune()

    async def save(self) -> None:
        with self._lock:
            if self._dirty:
                self._do_save()
                self._dirty = False

    def save_sync(self) -> None:
        if self._dirty:
            self._do_save()
            self._dirty = False

    async def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def size_sync(self) -> int:
        return len(self._cache)


cache = EmbeddingCache()
