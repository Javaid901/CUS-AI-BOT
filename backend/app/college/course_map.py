"""College-Course Map — structured bidirectional lookup between colleges and courses.

Builds indexes from existing COLLEGES data in data.py at import time.
Supports instant lookup without embeddings or LLM calls.
"""

from __future__ import annotations

from typing import Any

from app.college.data import COLLEGES

# ---------------------------------------------------------------------------
# Index: college_id → list of programme_ids
# Index: programme_id → list of college_ids
# ---------------------------------------------------------------------------

_COLLEGE_PROGRAMMES: dict[str, list[dict[str, str]]] = {}
"""college_id -> list of programme dicts (id, name, level, stream)"""

_COLLEGE_PROGRAMME_IDS: dict[str, list[str]] = {}
"""college_id -> list of programme IDs (e.g. 'bca', 'ba')"""

_PROGRAMME_TO_COLLEGES: dict[str, list[dict[str, str]]] = {}
"""programme_id -> list of college summaries {id, name, short_name, district}"""

_PROGRAMME_TO_STREAMS: dict[str, set[str]] = {}
"""programme_id -> set of stream names (e.g. 'Science', 'Arts')"""


def _build_indexes() -> None:
    for cid, c in COLLEGES.items():
        programmes = c.get("programmes", [])
        _COLLEGE_PROGRAMMES[cid] = programmes
        _COLLEGE_PROGRAMME_IDS[cid] = [p["id"] for p in programmes]

    for cid, c in COLLEGES.items():
        programmes = c.get("programmes", [])
        summary = {
            "id": cid,
            "name": c.get("name", ""),
            "short_name": c.get("short_name", ""),
            "district": c.get("district", ""),
            "naac": c.get("naac", "N/A"),
            "type": c.get("type", ""),
        }
        for p in programmes:
            pid = p["id"]
            if pid not in _PROGRAMME_TO_COLLEGES:
                _PROGRAMME_TO_COLLEGES[pid] = []
            _PROGRAMME_TO_COLLEGES[pid].append(summary)

            stream = p.get("stream", "")
            if stream:
                if pid not in _PROGRAMME_TO_STREAMS:
                    _PROGRAMME_TO_STREAMS[pid] = set()
                _PROGRAMME_TO_STREAMS[pid].add(stream)


_build_indexes()


def get_college_programmes(college_id: str) -> list[dict[str, str]]:
    """Get all programmes offered by a college.

    Returns list of dicts: {id, name, level, stream}
    """
    return _COLLEGE_PROGRAMMES.get(college_id, [])


def get_college_programme_ids(college_id: str) -> list[str]:
    """Get programme IDs offered by a college (e.g. ['ba', 'bca', 'bcom'])."""
    return _COLLEGE_PROGRAMME_IDS.get(college_id, [])


def get_colleges_for_programme(programme_id: str) -> list[dict[str, str]]:
    """Get all colleges that offer a given programme.

    Returns list of college summaries: {id, name, short_name, district, naac, type}
    """
    return _PROGRAMME_TO_COLLEGES.get(programme_id, [])


def has_college_programme(college_id: str, programme_id: str) -> bool:
    """Check if a college offers a specific programme."""
    pids = _COLLEGE_PROGRAMME_IDS.get(college_id, [])
    return programme_id in pids


def get_programme_streams(programme_id: str) -> list[str]:
    """Get unique stream names for a programme across all colleges."""
    streams = _PROGRAMME_TO_STREAMS.get(programme_id, set())
    return sorted(streams)


def get_all_programme_ids() -> list[str]:
    """Get all known programme IDs across all colleges."""
    return sorted(_PROGRAMME_TO_COLLEGES.keys())


def get_all_college_ids() -> list[str]:
    """Get all college IDs."""
    return sorted(_COLLEGE_PROGRAMMES.keys())


def search_colleges_by_programme(query: str) -> list[dict[str, Any]]:
    """Search for colleges offering a programme by partial name match.

    Example: "BCA" or "Computer Applications" -> colleges offering BCA

    Uses pre-built indexes for exact programme ID matches; falls back
    to linear name search for partial/programme-name queries.
    """
    q = query.strip().lower()
    results: list[dict[str, Any]] = []

    # Exact programme ID match — use the pre-built index
    if q in _PROGRAMME_TO_COLLEGES:
        colleges = _PROGRAMME_TO_COLLEGES[q]
        return [{"programme_id": q, "programme_name": q.upper(), "colleges": colleges}]

    # Collect matching programme IDs via partial name/id search
    seen_pids: set[str] = set()
    for programmes in _COLLEGE_PROGRAMMES.values():
        for p in programmes:
            if q in p["id"] and p["id"] not in seen_pids:
                seen_pids.add(p["id"])
                colleges = _PROGRAMME_TO_COLLEGES.get(p["id"], [])
                results.append({
                    "programme_id": p["id"],
                    "programme_name": p["name"],
                    "colleges": colleges,
                })
    if results:
        return results

    # Fallback: full programme name search
    seen_pids = set()
    for programmes in _COLLEGE_PROGRAMMES.values():
        for p in programmes:
            if q in p["name"].lower() and p["id"] not in seen_pids:
                seen_pids.add(p["id"])
                colleges = _PROGRAMME_TO_COLLEGES.get(p["id"], [])
                results.append({
                    "programme_id": p["id"],
                    "programme_name": p["name"],
                    "colleges": colleges,
                })
    return results
