"""College alias resolution — maps every known alias to a college ID."""

from __future__ import annotations

import re

from app.college.data import COLLEGES

# Build reverse alias map: any alias -> college_id
_ALIAS_MAP: dict[str, str] = {}

# Pre-defined aliases per college
_ALIAS_ENTRIES: list[tuple[str, list[str]]] = [
    ("gcw_ma_road", [
        "gcw", "gcw ma road", "gcw m.a. road", "government college for women",
        "govt college for women", "govt college for women ma road",
        "government college for women ma road", "women's college srinagar",
        "gcw srinagar", "ma road college",
    ]),
    ("amar_singh", [
        "amar singh", "amar singh college", "asc", "amar singh college srinagar",
    ]),
    ("sp_college", [
        "sp college", "s.p. college", "sri pratap college", "sri pratap",
        "sp college srinagar", "s.p.c",
    ]),
    ("bemina", [
        "bemina", "gdc bemina", "abdul ahad azad", "abdul ahad azad memorial",
        "degree college bemina", "bemina college",
    ]),
    ("iase", [
        "iase", "iase srinagar", "institute of advanced studies",
        "institute of advanced studies in education",
        "government college of education",
    ]),
    ("gdc_anantnag", [
        "anantnag", "gdc anantnag", "government degree college anantnag",
        "degree college anantnag",
    ]),
    ("gdc_pulwama", [
        "pulwama", "gdc pulwama", "government degree college pulwama",
        "degree college pulwama",
    ]),
    ("gdc_kulgam", [
        "kulgam", "gdc kulgam", "government degree college kulgam",
        "degree college kulgam",
    ]),
]

# Build the map
for college_id, aliases in _ALIAS_ENTRIES:
    for alias in aliases:
        _ALIAS_MAP[alias.strip().lower()] = college_id

# Also register full/display names for direct matching
for cid, c in COLLEGES.items():
    _ALIAS_MAP[c["name"].strip().lower()] = cid
    short = c.get("short_name")
    if short:
        _ALIAS_MAP[short.strip().lower()] = cid
    # Add location-based: "college in srinagar" etc
    district = c.get("district", "").lower()
    if district:
        _ALIAS_MAP[f"{c['name'].lower()} {district}"] = cid

_SORTED_ALIASES: list[str] = sorted(_ALIAS_MAP.keys(), key=len, reverse=True)

# Compiled pattern for detecting college mentions in free text
COLLEGE_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in _SORTED_ALIASES) + r")\b",
    re.IGNORECASE,
)


def resolve(message: str) -> str | None:
    """Resolve a user message to a college ID.

    Tries exact alias match first, then checks if any alias appears
    as a whole word (word-boundary match) in the message.
    Returns college ID or None.
    """
    text = message.strip().lower()

    # Exact match
    if text in _ALIAS_MAP:
        return _ALIAS_MAP[text]

    # Word-boundary match — longest alias first, avoids false positives
    for alias in _SORTED_ALIASES:
        if re.search(r"\b" + re.escape(alias) + r"\b", text):
            return _ALIAS_MAP[alias]

    return None


def resolve_pattern(message: str) -> str | None:
    """Use regex to find a college mention in free text."""
    match = COLLEGE_PATTERN.search(message.strip().lower())
    if match:
        alias = match.group(0).lower().strip()
        return _ALIAS_MAP.get(alias)
    return None


def get_all_college_ids() -> list[str]:
    return list(COLLEGES.keys())


def is_college_reference(message: str) -> bool:
    """Quick check if message references any known college."""
    return resolve(message) is not None
