"""
backend/app/ingest/retriever.py

Hybrid Retrieval Engine — production-grade RAG pipeline.

Pipeline:
  raw_query
  → query_rewrite()          — expand short/vague queries using conversation context
  → detect_topic()            — classify intent (admission, fee, results, etc.)
  → build_metadata_filter()   — derive optional metadata filter from topic
  → hybrid_search()           — embedding + BM25 keyword, combined & reranked
  → rerank()                  — deterministic relevance scoring
  → compress_context()        — group by document, deduplicate, trim
  → verify_evidence()         — refuse if score below threshold
  → final_context()           — structured context block for LLM

No LLM calls in the retrieval path — pure deterministic scoring.
"""

from __future__ import annotations

import math
import re
import threading
import time
from typing import Any

from app.config import settings
from app.ingest.embed import embed_query
from app.ingest.store import get_all_chunks
from app.ingest.store import query as chroma_query
from app.utils.logging import log

# ---------------------------------------------------------------------------
# Debug / diagnostic state (thread-safe)
# ---------------------------------------------------------------------------

_DIAG = {}
_DIAG_LOCK = threading.RLock()


def _record_diag(key: str, value: Any) -> None:
    with _DIAG_LOCK:
        _DIAG[key] = value


def get_diagnostics() -> dict[str, Any]:
    with _DIAG_LOCK:
        return dict(_DIAG)


def clear_diagnostics() -> None:
    with _DIAG_LOCK:
        _DIAG.clear()


# ---------------------------------------------------------------------------
# Topic detection
# ---------------------------------------------------------------------------

TOPIC_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(admission|admissions|apply|enrol|enroll|admit)\b", re.IGNORECASE), "admission", "Admissions"),
    (re.compile(r"\bfee\w*\b", re.IGNORECASE), "fee", "Fee"),
    (re.compile(r"\b(result|results|marksheet|grade|semester\s*result)\b", re.IGNORECASE), "results", "Results"),
    (re.compile(r"\b(eligib|eligibility|qualify|qualification|prerequisite)\b", re.IGNORECASE), "eligibility", "Eligibility"),
    (re.compile(r"\b(college|colleges|campus)\b", re.IGNORECASE), "college", "College"),
    (re.compile(r"\b(exam|examination|examinations|datesheet|date\s*sheet|admit\s*card)\b", re.IGNORECASE), "examination", "Examination"),
    (re.compile(r"\b(scholarship|scholarships|financial\s*aid)\b", re.IGNORECASE), "scholarship", "Scholarship"),
    (re.compile(r"\b(document|documents|docs|required\s*docs)\b", re.IGNORECASE), "documents", "Documents"),
    (re.compile(r"\b(course|courses|program|programme|programmes|subject|subjects)\b", re.IGNORECASE), "courses", "Courses"),
    (re.compile(r"\b(syllabus|syllabi|curricul)\b", re.IGNORECASE), "syllabus", "Syllabus"),
    (re.compile(r"\b(placement|placements|career|job)\b", re.IGNORECASE), "placement", "Placement"),
    (re.compile(r"\b(duration|years?\s*programme|semester)\b", re.IGNORECASE), "duration", "Duration"),
    (re.compile(r"\b(hostel|accommodation|boarding)\b", re.IGNORECASE), "hostel", "Hostel"),
    (re.compile(r"\b(contact|phone|email|call|reach)\b", re.IGNORECASE), "contact", "Contact"),
    (re.compile(r"\b(scholarship|scholarships)\b", re.IGNORECASE), "scholarship", "Scholarship"),
    (re.compile(r"\b(notice|notices|notification|circular)\b", re.IGNORECASE), "notices", "Notices"),
    (re.compile(r"\b(prospectus|brochure|handbook)\b", re.IGNORECASE), "prospectus", "Prospectus"),
    (re.compile(r"\b(facility|facilities|infrastructure)\b", re.IGNORECASE), "facilities", "Facilities"),
    (re.compile(r"\b(seat|seats|intake|capacity)\b", re.IGNORECASE), "seats", "Seats"),
    (re.compile(r"\b(faculty|teacher|professor|staff|lecturer)\b", re.IGNORECASE), "faculty", "Faculty"),
    (re.compile(r"\b(library|lab|laboratory|computer\s*center)\b", re.IGNORECASE), "library", "Library"),
    (re.compile(r"\b(sport|sports|games|athletic)\b", re.IGNORECASE), "sports", "Sports"),
    (re.compile(r"\b(download|downloads|form|forms)\b", re.IGNORECASE), "downloads", "Downloads"),
    (re.compile(r"\b(transfer|migration|TC|leaving\s*certificate)\b", re.IGNORECASE), "transfer", "Transfer"),
    (re.compile(r"\b(backlog|backlogs|supplementary|compartment)\b", re.IGNORECASE), "backlog", "Backlog"),
    (re.compile(r"\b(transcript|transcripts|degree|certificate|certificates)\b", re.IGNORECASE), "transcript", "Transcript"),
]

# Programme keywords for detection
PROGRAMME_KEYWORDS: set[str] = {
    "ba", "bsc", "bcom", "bba", "bca", "btech", "bed", "b.ed",
    "ma", "msc", "mcom", "mba", "mca", "med", "m.ed", "phd",
    "integrated", "ug", "pg", "dyd",
}

# College name patterns — detect if user is asking about a specific college
COLLEGE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(gcw|gdc|govt?\s*college|degree\s*college|women'?s?\s*college|amar\s*singh|sri\s*pratap|bemina|iase)\b", re.IGNORECASE),
    re.compile(r"\b(college\s+of\s+\w+)\b", re.IGNORECASE),
]


def detect_topic(query: str) -> dict[str, Any]:
    """Detect query intent and context flags from the raw query.
    
    Returns:
        topic_key: str | None  — the matched topic category
        topic_label: str       — human-readable label
        programmes: list[str]  — detected programme references
        colleges: list[str]    — detected college references
        is_short: bool         — query is < 4 words (likely a follow-up)
    """
    q = query.strip().lower()
    topic_key = None
    topic_label = "General"

    for pattern, key, label in TOPIC_PATTERNS:
        if pattern.search(q):
            topic_key = key
            topic_label = label
            break

    programmes = [p for p in PROGRAMME_KEYWORDS if re.search(r"\b" + re.escape(p) + r"\b", q)]
    colleges = []
    for cp in COLLEGE_PATTERNS:
        m = cp.search(q)
        if m:
            colleges.append(m.group(0))

    return {
        "topic_key": topic_key,
        "topic_label": topic_label,
        "programmes": programmes,
        "colleges": colleges,
        "is_short": len(q.split()) < 4,
    }


# ---------------------------------------------------------------------------
# Query rewriting
# ---------------------------------------------------------------------------

# Short query → expanded query mappings for common patterns
QUERY_EXPANSIONS: dict[str, str] = {
    "fee": "fee structure tuition fees admission fee examination fee",
    "fees": "fee structure tuition fees admission fee examination fee",
    "eligibility": "eligibility criteria qualification admission requirements",
    "eligible": "eligibility criteria qualification admission requirements",
    "documents": "documents required for admission application checklist",
    "docs": "documents required for admission application checklist",
    "admission": "admission process application eligibility documents fee",
    "admissions": "admission process application eligibility documents fee",
    "result": "result examination marksheet grade semester result",
    "results": "result examination marksheet grade semester result",
    "syllabus": "syllabus course curriculum subjects topics",
    "college": "college details information about college",
    "placement": "placement record companies recruiting career opportunities",
    "duration": "duration years programme length semester system",
    "hostel": "hostel accommodation facilities boarding",
    "scholarship": "scholarship financial aid scheme eligibility",
    "contact": "contact information phone email address office",
    "facilities": "facilities infrastructure library lab sports",
    "seats": "seats intake capacity available seats",
    "courses": "courses programmes offered subjects specializations",
    "prospectus": "prospectus admission brochure information booklet",
    "notices": "notices notifications circulars announcements",
    "examination": "examination datesheet schedule admit card",
    "datesheet": "datesheet examination schedule date sheet",
    "apply": "application process how to apply admission procedure",
    "intake": "intake seats capacity admission quota",
    "transfer": "transfer migration TC leaving certificate procedure",
    "backlog": "backlog supplementary examination improvement",
    "transcript": "transcript degree certificate duplicate document",
    "certificate": "degree certificate transcript duplicate document",
    "migration": "migration certificate transfer leaving certificate",
    "admit card": "admit card hall ticket examination entry pass",
    "admit": "admit card hall ticket examination entry pass",
    "library": "library facilities timing books resources",
    "sports": "sports facilities games physical education",
    "faculty": "faculty teachers professors departments staff",
    "download": "download form application prospectus syllabus",
    "what": "",
    "which": "",
    "how": "",
    "tell": "",
    "list": "",
    "show": "",
    "can": "",
    "do": "",
    "does": "",
    "is": "",
    "are": "",
    "was": "",
    "were": "",
}


def rewrite_query(query: str, context: dict[str, Any] | None = None) -> str:
    """Rewrite a user query into a stronger retrieval query.
    
    If context (from conversation) is provided, it is used to augment
    short follow-up queries. Common single-word queries are expanded.
    """
    if not settings.ENABLE_QUERY_REWRITE:
        return query

    q = query.strip().lower()
    # Truncate excessively long queries
    if len(q) > settings.MAX_QUERY_LENGTH:
        q = q[:settings.MAX_QUERY_LENGTH]

    is_short = len(q.split()) <= 3

    # Build expansion from context
    context_parts = []
    if context:
        if context.get("programme"):
            context_parts.append(context["programme"])
        if context.get("college_name"):
            context_parts.append(context["college_name"])
        if context.get("topic"):
            context_parts.append(context["topic"])

    # Expand known short queries
    had_expansion = False
    if is_short and q in QUERY_EXPANSIONS:
        expansion = QUERY_EXPANSIONS[q]
        if expansion:
            q = expansion
            had_expansion = True

    # Detect keywords in the query and append contextual terms
    q.lower()
    if not had_expansion:
        topic_info = detect_topic(q)
        if topic_info["topic_key"]:
            # If query already has a topic but is short, add programme context
            if is_short and context_parts:
                pass  # context will be added below

    # If context is available and query is short, prepend context
    if (is_short or had_expansion) and context_parts:
        q = " ".join(context_parts) + " " + q

    _record_diag("original_query", query)
    _record_diag("rewritten_query", q)
    _record_diag("query_context", context)

    return q


# ---------------------------------------------------------------------------
# BM25 index (in-memory, rebuilt periodically)
# ---------------------------------------------------------------------------

class BM25Index:
    """Simple BM25-OK index over chunk contents."""
    
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._chunks: list[dict[str, Any]] = []
        self._avg_doc_len: float = 0.0
        self._doc_lens: list[int] = []
        self._term_freqs: list[dict[str, int]] = []
        self._idf: dict[str, float] = {}
        self._k1: float = 1.5
        self._b: float = 0.75
        self._last_refresh: float = 0.0
        self._ready = False

    def _tokenize(self, text: str) -> list[str]:
        text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
        return [w for w in text.split() if len(w) > 1]

    def refresh(self) -> None:
        """Rebuild the BM25 index from all chunks in Chroma."""
        now_t = time.time()
        if self._ready and (now_t - self._last_refresh) < settings.BM25_INDEX_REFRESH_INTERVAL:
            return
        with self._lock:
            chunks = get_all_chunks(limit=20000)
            if not chunks:
                return
            self._chunks = chunks
            self._doc_lens = []
            self._term_freqs = []
            doc_freq: dict[str, int] = {}
            total_terms = 0

            for c in chunks:
                tokens = self._tokenize(c.get("content", ""))
                self._doc_lens.append(len(tokens))
                total_terms += len(tokens)
                tf: dict[str, int] = {}
                seen_terms: set[str] = set()
                for t in tokens:
                    tf[t] = tf.get(t, 0) + 1
                    if t not in seen_terms:
                        doc_freq[t] = doc_freq.get(t, 0) + 1
                        seen_terms.add(t)
                self._term_freqs.append(tf)

            n_docs = len(chunks)
            self._avg_doc_len = total_terms / n_docs if n_docs else 0.0
            self._idf = {t: math.log(1 + (n_docs - df + 0.5) / (df + 0.5)) for t, df in doc_freq.items()}
            self._last_refresh = now_t
            self._ready = True
            log.info("BM25 index refreshed: %d docs, %d terms", n_docs, len(self._idf))

    def search(self, query: str, top_k: int = 20) -> list[dict[str, Any]]:
        """Search BM25 index, return top_k results with BM25 scores."""
        if not self._ready:
            self.refresh()
            if not self._ready:
                return []
        with self._lock:
            q_tokens = self._tokenize(query)
            if not q_tokens:
                return []
            scores = [0.0] * len(self._chunks)
            for qt in q_tokens:
                idf = self._idf.get(qt, 0.0)
                if idf == 0.0:
                    continue
                for i in range(len(self._chunks)):
                    tf = self._term_freqs[i].get(qt, 0)
                    if tf > 0:
                        dl = self._doc_lens[i]
                        scores[i] += idf * (tf * (self._k1 + 1)) / (tf + self._k1 * (1 - self._b + self._b * dl / self._avg_doc_len))

            ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
            results = []
            for idx, score in ranked[:top_k]:
                if score <= 0:
                    continue
                c = dict(self._chunks[idx])
                c["bm25_score"] = round(score, 4)
                results.append(c)
            return results


_bm25 = BM25Index()


def get_bm25() -> BM25Index:
    return _bm25


# ---------------------------------------------------------------------------
# Hybrid search
# ---------------------------------------------------------------------------

def _normalize_distance(dist: float | None) -> float:
    """Convert Chroma cosine distance [0,2] to similarity score [0,1]."""
    if dist is None:
        return 0.0
    return round(max(0.0, 1.0 - float(dist)), 4)


def hybrid_search(query: str, top_k_expand: int | None = None) -> list[dict[str, Any]]:
    """Run hybrid search: embedding + BM25, merge and score.
    
    Returns candidate list with fields:
        content, document_id, document_title, page_number, chunk_index,
        heading, embedding_score, bm25_score, combined_score
    """
    tk = top_k_expand or settings.TOP_K_EXPAND

    # 1. Embedding search
    embedding = embed_query(query)
    emb_results = chroma_query(embedding, top_k=tk)
    emb_map: dict[str, dict[str, Any]] = {}
    for c in emb_results:
        cid = c.get("id", "")
        c["embedding_score"] = _normalize_distance(c.get("distance"))
        c["bm25_score"] = 0.0
        emb_map[cid] = c

    # 2. BM25 search
    bm25 = get_bm25()
    bm25.refresh()
    bm25_results = bm25.search(query, top_k=tk)
    bm25_map: dict[str, dict[str, Any]] = {}
    for c in bm25_results:
        cid = c.get("id", "")
        c["embedding_score"] = 0.0
        bm25_map[cid] = c

    # 3. Merge results with combined score
    merged: dict[str, dict[str, Any]] = {}
    all_ids = set(emb_map.keys()) | set(bm25_map.keys())

    # Normalize BM25 scores to [0, 1] range across results
    max_bm25 = max((c["bm25_score"] for c in bm25_map.values()), default=0.0)
    if max_bm25 > 0:
        for c in bm25_map.values():
            c["bm25_score"] = round(c["bm25_score"] / max_bm25, 4)

    for cid in all_ids:
        emb_c = emb_map.get(cid)
        bm25_c = bm25_map.get(cid)

        if emb_c and bm25_c:
            # Both available — use the richer dict from emb_c with bm25 score merged
            candidate = dict(emb_c)
            candidate["bm25_score"] = bm25_c.get("bm25_score", 0.0)
        elif emb_c:
            candidate = dict(emb_c)
        else:
            candidate = dict(bm25_c)

        # Combined score: weighted average
        emb_score = candidate.get("embedding_score", 0.0)
        bm25_score = candidate.get("bm25_score", 0.0)

        if settings.ENABLE_HYBRID_RETRIEVAL:
            # Weight: 0.6 embedding + 0.4 BM25
            combined = 0.6 * emb_score + 0.4 * bm25_score
        else:
            combined = emb_score

        candidate["combined_score"] = round(combined, 4)
        merged[cid] = candidate

    candidates = list(merged.values())
    candidates.sort(key=lambda x: x.get("combined_score", 0), reverse=True)

    _record_diag("hybrid_candidates_count", len(candidates))
    _record_diag("embedding_results", len(emb_results))
    _record_diag("bm25_results", len(bm25_results))

    return candidates


# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------

def rerank(candidates: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Deterministic reranking that boosts chunks with keyword overlap,
    heading matches, and query term coverage.
    """
    if not candidates or not settings.ENABLE_RERANKING:
        return candidates

    q_lower = query.lower()
    q_terms = set(re.sub(r"[^a-z0-9\s]", " ", q_lower).split())
    q_terms.discard("")

    boosted: list[dict[str, Any]] = []
    for c in candidates:
        score = c.get("combined_score", 0.0)
        content = c.get("content", "").lower()
        heading = c.get("heading", "").lower()
        doc_title = c.get("document_title", "").lower()

        # 1. Keyword overlap boost (up to +0.1)
        if q_terms:
            content_terms = set(re.sub(r"[^a-z0-9\s]", " ", content).split())
            overlap = len(q_terms & content_terms)
            score += min(0.1, overlap * 0.02)

        # 2. Heading match bonus (up to +0.08)
        if heading:
            heading_match = sum(1 for t in q_terms if t in heading)
            score += min(0.08, heading_match * 0.04)

        # 3. Document title match bonus (up to +0.05)
        if doc_title:
            title_match = sum(1 for t in q_terms if t in doc_title)
            score += min(0.05, title_match * 0.015)

        # 4. Exact heading match big bonus (+0.15)
        if heading and q_lower.strip() in heading:
            score += 0.15

        # 5. Penalty for very short chunks (< 50 chars) that are unlikely to be useful
        if len(content) < 50:
            score -= 0.1

        c["rerank_score"] = round(score, 4)
        boosted.append(c)

    boosted.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
    _record_diag("reranked_count", len(boosted))
    return boosted


# ---------------------------------------------------------------------------
# Context compression (group by document)
# ---------------------------------------------------------------------------

def compress_context(candidates: list[dict[str, Any]], top_k: int | None = None) -> list[dict[str, Any]]:
    """Group chunks by source document, deduplicate, and keep the strongest.
    
    Strategy:
    1. Keep at most 2 chunks per document (the highest scored)
    2. Sort documents by their highest chunk score
    3. Limit to top_k total chunks
    """
    if not candidates:
        return []

    tk = top_k or settings.RERANK_K
    score_key = "rerank_score" if settings.ENABLE_RERANKING else "combined_score"

    # Group by document
    doc_groups: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        doc_id = c.get("document_id") or c.get("document_title") or "unknown"
        doc_groups.setdefault(doc_id, []).append(c)

    # Max 2 per document, sorted within group
    per_doc_max = 2
    selected: list[dict[str, Any]] = []
    for doc_id, chunks in doc_groups.items():
        chunks.sort(key=lambda x: x.get(score_key, 0), reverse=True)
        selected.extend(chunks[:per_doc_max])

    # Sort all selected by score descending
    selected.sort(key=lambda x: x.get(score_key, 0), reverse=True)

    compressed = selected[:tk]

    _record_diag("compressed_count", len(compressed))
    _record_diag("source_documents", len(doc_groups))

    return compressed


# ---------------------------------------------------------------------------
# Answer verification
# ---------------------------------------------------------------------------

def verify_evidence(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify that retrieved evidence is strong enough to answer.
    
    Returns:
        passed: bool — whether evidence is sufficient
        max_score: float — highest evidence score
        avg_score: float — average evidence score
        reason: str — explanation of the verdict
    """
    if not candidates:
        return {
            "passed": False,
            "max_score": 0.0,
            "avg_score": 0.0,
            "reason": "No evidence retrieved",
        }

    score_key = "rerank_score" if settings.ENABLE_RERANKING else "combined_score"
    scores = [c.get(score_key, 0) for c in candidates]
    max_score = max(scores)
    avg_score = sum(scores) / len(scores)
    threshold = settings.SCORE_THRESHOLD_STRICT

    if max_score < threshold:
        return {
            "passed": False,
            "max_score": max_score,
            "avg_score": avg_score,
            "reason": f"Max evidence score ({max_score:.2f}) below threshold ({threshold})",
        }

    return {
        "passed": True,
        "max_score": max_score,
        "avg_score": avg_score,
        "reason": f"Evidence score {max_score:.2f} meets threshold {threshold}",
    }


# ---------------------------------------------------------------------------
# Main retrieval pipeline
# ---------------------------------------------------------------------------

def retrieve_hybrid(
    query: str,
    context: dict[str, Any] | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Full hybrid retrieval pipeline.
    
    Returns the final list of chunks to be sent to the LLM.
    If evidence fails verification, returns empty list.
    """
    clear_diagnostics()
    t0 = time.perf_counter()

    # 1. Rewrite query
    rewritten = rewrite_query(query, context)

    # 2. Detect topic
    topic_info = detect_topic(rewritten)
    _record_diag("topic", topic_info)

    # 3. Hybrid search (candidate expansion)
    candidates = hybrid_search(rewritten)

    if not candidates:
        log.info("Retriever: no candidates found for '%s'", rewritten[:60])
        return []

    # 4. Rerank
    if settings.ENABLE_RERANKING:
        candidates = rerank(candidates, rewritten)

    # 5. Compress context (group by document)
    candidates = compress_context(candidates, top_k=top_k or settings.RERANK_K)

    # 6. Verify evidence
    verification = verify_evidence(candidates)
    _record_diag("verification", verification)

    elapsed = time.perf_counter() - t0
    _record_diag("elapsed_ms", round(elapsed * 1000, 1))

    log.info(
        "Retriever: query=%s topic=%s candidates=%d verified=%s (%.1fms)",
        rewritten[:60],
        topic_info["topic_key"] or "none",
        len(candidates),
        verification["passed"],
        elapsed * 1000,
    )

    if not verification["passed"] and settings.ENABLE_ANSWER_VERIFICATION:
        log.info("Retriever: evidence too weak, returning empty — %s", verification["reason"])
        return []

    return candidates
