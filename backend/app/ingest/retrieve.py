"""
backend/app/ingest/retrieve.py

Retrieval — entry-point that delegates to the hybrid retriever.

Kept as a thin wrapper so existing callers (chat/service.py)
continue to work without changes.
"""

from __future__ import annotations

from typing import Any

from app.ingest.retriever import retrieve_hybrid


def retrieve(
    question: str,
    top_k: int | None = None,
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Retrieve relevant chunks using the hybrid pipeline.
    
    Args:
        question: user query (may be rewritten)
        top_k: final number of chunks to return
        context: optional conversation context for query rewriting
    
    Returns:
        list of chunk dicts with scores, citations, and metadata.
        Empty list if evidence is too weak.
    """
    return retrieve_hybrid(question, context=context, top_k=top_k)
