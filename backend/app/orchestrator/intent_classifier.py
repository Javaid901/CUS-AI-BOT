"""
backend/app/orchestrator/intent_classifier.py

Semantic intent classifier using Sentence Transformers embeddings.

Replaces the brittle keyword-based classify() in intent_router.py with
a semantic approach: embed user query, compare against intent centroids,
return best match with confidence score.

Thread-safe lazy initialisation. Uses the same all-MiniLM-L6-v2 model as
the ingest pipeline (loaded lazily, shared across requests).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np

from app.config import settings
from app.orchestrator.intent_kb import (
    ALL_INTENTS,
    INTENT_PARAPHRASES,
    NAV_CATEGORY_INTENTS,
)

log = logging.getLogger("cus_ai")

_MODEL_LOCK = threading.RLock()
_MODEL = None
_INTENT_CENTROIDS: dict[str, np.ndarray] | None = None
_INTENT_PARAPHRASE_EMBEDDINGS: dict[str, list[np.ndarray]] | None = None

# Default confidence threshold — configurable via settings or env
SEMANTIC_CONFIDENCE_THRESHOLD: float = 0.35
# Higher threshold for accepting as a "broad" navigation intent
BROAD_CONFIDENCE_THRESHOLD: float = 0.40


def _get_model():
    """Lazy-load the SentenceTransformer model (thread-safe)."""
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                from sentence_transformers import SentenceTransformer
                _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
                log.info("Semantic intent classifier model loaded (all-MiniLM-L6-v2)")
    return _MODEL


def _compute_centroids() -> dict[str, np.ndarray]:
    """Compute centroid (mean) embedding for each intent's paraphrases."""
    model = _get_model()
    centroids: dict[str, np.ndarray] = {}
    for intent, paraphrases in INTENT_PARAPHRASES.items():
        if not paraphrases:
            continue
        embeddings = model.encode(paraphrases, normalize_embeddings=True)
        centroids[intent] = np.mean(embeddings, axis=0)
        # Re-normalize the centroid so cosine similarity works correctly
        norm = np.linalg.norm(centroids[intent])
        if norm > 0:
            centroids[intent] = centroids[intent] / norm
    return centroids


def _compute_paraphrase_embeddings() -> dict[str, list[np.ndarray]]:
    """Compute individual embeddings for every paraphrase (for max-sim scoring)."""
    model = _get_model()
    result: dict[str, list[np.ndarray]] = {}
    for intent, paraphrases in INTENT_PARAPHRASES.items():
        if not paraphrases:
            continue
        embeddings = model.encode(paraphrases, normalize_embeddings=True)
        result[intent] = [emb for emb in embeddings]
    return result


def _ensure_loaded():
    """Ensure intent centroids are computed (lazy, once)."""
    global _INTENT_CENTROIDS, _INTENT_PARAPHRASE_EMBEDDINGS
    if _INTENT_CENTROIDS is None:
        with _MODEL_LOCK:
            if _INTENT_CENTROIDS is None:
                t0 = time.time()
                _INTENT_CENTROIDS = _compute_centroids()
                _INTENT_PARAPHRASE_EMBEDDINGS = _compute_paraphrase_embeddings()
                elapsed = time.time() - t0
                log.info(
                    "Intent centroids computed: %d intents in %.2fs",
                    len(_INTENT_CENTROIDS), elapsed,
                )


def classify(
    message: str,
    threshold: float | None = None,
) -> tuple[str, float, dict[str, Any]]:
    """Classify a user message into a semantic intent.

    Args:
        message: Raw user message.
        threshold: Confidence threshold (default SEMANTIC_CONFIDENCE_THRESHOLD).

    Returns:
        (intent_name, confidence, debug_info)
        - intent_name: canonical intent from intent_kb, or "unknown"
        - confidence: cosine similarity score (0-1)
        - debug_info: dict with top 3 intents, scores, and timing
    """
    _ensure_loaded()
    model = _get_model()
    t0 = time.time()

    clean = message.strip().lower()
    if not clean:
        return "unknown", 0.0, {"reason": "empty_message", "elapsed_ms": 0}

    query_emb = model.encode([clean], normalize_embeddings=True)[0]
    thr = threshold if threshold is not None else SEMANTIC_CONFIDENCE_THRESHOLD

    # Score against centroids (average similarity)
    centroid_scores: dict[str, float] = {}
    for intent, centroid in _INTENT_CENTROIDS.items():
        sim = float(np.dot(query_emb, centroid))
        centroid_scores[intent] = sim

    # Score against individual paraphrases (max similarity per intent)
    max_scores: dict[str, float] = {}
    for intent, para_embs in _INTENT_PARAPHRASE_EMBEDDINGS.items():
        best = max(float(np.dot(query_emb, pe)) for pe in para_embs)
        max_scores[intent] = best

    # Blend: 0.6 * centroid + 0.4 * max_paraphrase for robustness
    blended: dict[str, float] = {}
    for intent in ALL_INTENTS:
        blended[intent] = 0.6 * centroid_scores.get(intent, 0.0) + 0.4 * max_scores.get(intent, 0.0)

    sorted_intents = sorted(blended.items(), key=lambda x: -x[1])
    best_intent, best_score = sorted_intents[0]

    elapsed_ms = round((time.time() - t0) * 1000, 1)

    debug_info = {
        "top_intents": sorted_intents[:5],
        "centroid_scores": {k: round(v, 4) for k, v in sorted(centroid_scores.items(), key=lambda x: -x[1])[:5]},
        "max_scores": {k: round(v, 4) for k, v in sorted(max_scores.items(), key=lambda x: -x[1])[:5]},
        "elapsed_ms": elapsed_ms,
        "threshold": thr,
    }

    if best_score < thr:
        return "unknown", round(best_score, 4), debug_info

    return best_intent, round(best_score, 4), debug_info


def classify_broad(
    message: str,
    threshold: float | None = None,
) -> tuple[str | None, float, dict[str, Any]]:
    """Classify intent but only return 'broad' navigable intents.

    Returns (category, confidence, debug_info) where category is None
    if no broad intent matches with sufficient confidence.
    """
    intent, conf, debug = classify(message, threshold or BROAD_CONFIDENCE_THRESHOLD)

    if intent == "unknown":
        return None, conf, debug

    # Check if this intent maps to a navigation category
    if intent in NAV_CATEGORY_INTENTS:
        return intent, conf, debug

    # Some general intent that doesn't have a structured response
    return None, conf, debug


def get_confidence_threshold() -> float:
    """Return the current confidence threshold (allows runtime tuning)."""
    return getattr(settings, "SEMANTIC_CONFIDENCE_THRESHOLD", SEMANTIC_CONFIDENCE_THRESHOLD)


# Warm up on import (optional, called explicitly from startup)
def warmup() -> None:
    """Pre-load model and compute centroids."""
    _ensure_loaded()
