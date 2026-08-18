"""
backend/app/ingest/store.py

ChromaDB persistent vector store wrapper.

Chroma is used in persistent (on-disk) mode so no separate server is required.
Each document's chunks are stored in a collection keyed by collection name.
Metadata stored alongside vectors enables filtering and citation rendering.
"""

from __future__ import annotations

import re
import threading
from typing import Any

from app.config import settings
from app.utils.logging import log

_COLLECTION = "cus_knowledge"
_CLIENT = None
_COL = None
_LOCK = threading.RLock()


def _client():
    global _CLIENT
    if _CLIENT is None:
        with _LOCK:
            if _CLIENT is None:
                import chromadb
                _CLIENT = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
    return _CLIENT


def get_collection():
    global _COL
    if _COL is None:
        with _LOCK:
            if _COL is None:
                client = _client()
                _COL = client.get_or_create_collection(
                    name=_COLLECTION,
                    metadata={"hnsw:space": "cosine"},
                )
    return _COL


def reset_collection_cache():
    global _COL
    _COL = None


def _safe_id(raw: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "_", raw)
    return cleaned[:64]


# Metadata keys persisted per chunk that enable scheme/semester-aware filtering.
FILTER_METADATA_KEYS = (
    "academic_scheme",
    "programme",
    "department",
    "batch",
    "semester",
    "document_type",
    "category",
    "source_url",
    "source",
    "college_id",
    "college_name",
    "scope",
    "source_kind",
)


def _merge_doc_metadata(base: dict[str, Any], extra: dict[str, Any] | None) -> dict[str, Any]:
    """Merge document-level metadata (scheme, programme, etc.) into chunk metadata."""
    if not extra:
        return base
    merged = dict(base)
    for key, value in extra.items():
        if key in FILTER_METADATA_KEYS and value not in (None, ""):
            merged[key] = str(value)
    return merged


def add_chunks(
    document_id: str,
    title: str,
    chunks: list[dict[str, Any]],
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    if not chunks:
        return
    col = get_collection()
    ids, docs, metas = [], [], []
    for c in chunks:
        cid = _safe_id(f"{document_id}_{c['chunk_index']}")
        ids.append(cid)
        docs.append(c["content"])
        heading = c.get("heading") or ""
        metas.append(
            _merge_doc_metadata(
                {
                    "document_id": document_id,
                    "document_title": title,
                    "page_number": int(c.get("page_number") or 0),
                    "chunk_index": int(c.get("chunk_index") or 0),
                    "heading": heading[:200],
                    "sha256": c.get("sha256", ""),
                },
                extra_metadata,
            )
        )
    col.add(ids=ids, documents=docs, metadatas=metas)
    log.info("Stored %d chunks for document %s", len(ids), document_id)


def add_chunks_with_embeddings(
    document_id: str,
    title: str,
    chunks: list[dict[str, Any]],
    embeddings: list[list[float]],
    extra_metadata: dict[str, Any] | None = None,
) -> int:
    """Store chunks with precomputed embeddings, skipping dupes by sha256.
    Returns the number of newly stored chunks."""
    if not chunks or not embeddings:
        return 0
    if len(chunks) != len(embeddings):
        # Silent partial-write here previously dropped data with only a log
        # line; surface it so the worker marks the job failed.
        raise ValueError(
            f"Chunk/embedding count mismatch: {len(chunks)} vs {len(embeddings)}"
        )
    col = get_collection()
    existing_hashes = _existing_chunk_hashes(embeddings, col)
    ids, docs, metas, embeds_list = [], [], [], []
    skipped = 0
    for c, emb in zip(chunks, embeddings):
        chunk_sha = c.get("sha256", "")
        if chunk_sha and chunk_sha in existing_hashes:
            skipped += 1
            continue
        cid = _safe_id(f"{document_id}_{c['chunk_index']}")
        ids.append(cid)
        docs.append(c["content"])
        heading = c.get("heading") or ""
        metas.append(
            _merge_doc_metadata(
                {
                    "document_id": document_id,
                    "document_title": title,
                    "page_number": int(c.get("page_number") or 0),
                    "chunk_index": int(c.get("chunk_index") or 0),
                    "heading": heading[:200],
                    "sha256": chunk_sha,
                },
                extra_metadata,
            )
        )
        embeds_list.append(emb)
    if ids:
        col.add(ids=ids, documents=docs, metadatas=metas, embeddings=embeds_list)
    if skipped:
        log.info("Skipped %d duplicate chunks for %s", skipped, document_id)
    log.info("Stored %d chunks for document %s (%d skipped)", len(ids), document_id, skipped)
    return len(ids)


def _existing_chunk_hashes(
    embeddings: list[list[float]], col: Any = None
) -> set[str]:
    """Fetch all sha256 metadata values already in Chroma (up to a limit)."""
    if col is None:
        col = get_collection()
    try:
        result = col.get(include=["metadatas"], limit=100000)
        if not result or not result.get("metadatas"):
            return set()
        hashes: set[str] = set()
        for m in result["metadatas"]:
            if m and m.get("sha256"):
                hashes.add(m["sha256"])
        log.debug("Loaded %d existing chunk hashes from Chroma", len(hashes))
        return hashes
    except Exception as exc:
        log.warning("Could not load existing chunk hashes: %s", exc)
        return set()


def delete_document(document_id: str) -> None:
    col = get_collection()
    try:
        col.delete(where={"document_id": document_id})
        log.info("Deleted vectors for document %s", document_id)
    except Exception as exc:
        log.warning("Chroma delete failed for %s: %s", document_id, exc)
    try:
        # The in-memory BM25 index must not keep serving deleted content until
        # its next scheduled refresh.
        from app.ingest.retriever import force_bm25_refresh
        force_bm25_refresh()
    except Exception:
        pass


def backfill_scope_metadata(scope: str = "university") -> int:
    """Tag legacy chunk metadata with a scope value (idempotent, boot-time).

    Chunks stored before college scoping existed carry no `scope` key; this
    stamps them as university-wide so scope-aware retrieval can isolate the
    college corpus without dropping legacy content. Returns chunks updated.
    """
    col = get_collection()
    try:
        result = col.get(include=["metadatas"])
    except Exception as exc:
        log.warning("backfill_scope_metadata: get failed: %s", exc)
        return 0
    if not result or not result.get("ids"):
        return 0
    ids, metas = result["ids"], result["metadatas"] or []
    ids_to_update: list[str] = []
    new_metas: list[dict] = []
    for i, cid in enumerate(ids):
        meta = metas[i] if i < len(metas) and metas[i] else {}
        if meta and meta.get("scope") is None:
            patched = dict(meta)
            patched["scope"] = scope
            ids_to_update.append(cid)
            new_metas.append(patched)
    if not ids_to_update:
        return 0
    try:
        col.update(ids=ids_to_update, metadatas=new_metas)
        log.info("Backfilled scope=%s on %d legacy chunks", scope, len(ids_to_update))
    except Exception as exc:
        log.warning("backfill_scope_metadata update failed: %s", exc)
        return 0
    return len(ids_to_update)


def get_all_chunks(limit: int = 10000) -> list[dict[str, Any]]:
    """Retrieve all stored chunks (up to limit) for BM25 indexing."""
    col = get_collection()
    try:
        result = col.get(include=["documents", "metadatas"], limit=limit)
        if not result or not result.get("ids"):
            return []
        out: list[dict[str, Any]] = []
        for i, doc_id in enumerate(result["ids"]):
            meta = (result["metadatas"] or [{}])[i] or {}
            doc_text = (result["documents"] or [""])[i] or ""
            chunk = {
                "id": doc_id,
                "content": doc_text,
                "document_id": meta.get("document_id"),
                "document_title": meta.get("document_title"),
                "page_number": meta.get("page_number") or None,
                "chunk_index": meta.get("chunk_index"),
                "heading": meta.get("heading") or "",
                "sha256": meta.get("sha256", ""),
            }
            for key in FILTER_METADATA_KEYS:
                if key in meta:
                    chunk[key] = meta[key]
            out.append(chunk)
        return out
    except Exception as exc:
        log.warning("get_all_chunks failed: %s", exc)
        return []


def query(embedding: list[float], top_k: int = 6, where: dict | None = None) -> list[dict[str, Any]]:
    col = get_collection()
    res = col.query(
        query_embeddings=[embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
        where=where,
    )
    out: list[dict[str, Any]] = []
    if not res or not res.get("ids") or not res["ids"][0]:
        return out
    ids = res["ids"][0]
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res.get("distances", [[]])[0]
    for i, doc in enumerate(docs):
        meta = metas[i] or {}
        chunk: dict[str, Any] = {
            "id": ids[i],
            "content": doc,
            "document_id": meta.get("document_id"),
            "document_title": meta.get("document_title"),
            "page_number": meta.get("page_number") or None,
            "chunk_index": meta.get("chunk_index"),
            "heading": meta.get("heading") or "",
            "distance": dists[i] if i < len(dists) else None,
        }
        for key in FILTER_METADATA_KEYS:
            if key in meta:
                chunk[key] = meta[key]
        out.append(chunk)
    return out
