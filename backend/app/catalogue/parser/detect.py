"""
backend/app/catalogue/parser/detect.py

Metadata detection for uploaded curriculum documents:

  * academic scheme      (NEP 2020 / Traditional / CBCS / future codes from DB)
  * programme            (existing catalogue match first, then header heuristics)
  * programme level      (ug / pg / phd / integrated)

Everything scheme-related is database-driven: known schemes (and their alias
keywords) come from the academic_schemes table via the catalogue service, so
new schemes added by admins are detected automatically without code changes.
Unknown values are reported as None so the admin review screen can resolve
them before anything goes live.
"""

from __future__ import annotations

import re
from typing import Any

from app.catalogue import service

# ---------------------------------------------------------------------------
# Programme-level inference (independent of scheme — stays true for any scheme)
# ---------------------------------------------------------------------------

_LEVEL_WORD_MAP: dict[str, str] = {
    "ug": "ug", "undergraduate": "ug", "under graduate": "ug", "undergrad": "ug",
    "pg": "pg", "postgraduate": "pg", "post graduate": "pg",
    "phd": "phd", "ph.d": "phd", "doctorate": "phd", "doctor of philosophy": "phd",
    "doctoral": "phd", "m.phil": "phd", "integrated": "integrated",
    "dual degree": "integrated", "dual-degree": "integrated",
}

# Degree-level by programme code shape (used as fallback when no level keyword).
_CODE_LEVEL_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(ph\.?d|doctor(?:al)?|d\.?phil)\b", re.IGNORECASE), "phd"),
    (re.compile(r"\b(integrated|dual\s?degree)\b", re.IGNORECASE), "integrated"),
    (re.compile(r"\b(m\.?(?:sc|a|com|ba|tech|ca|phil|ed)|mba|mca|ll\.?m|m\.?a\.?)\b", re.IGNORECASE), "pg"),
    (re.compile(r"\b(b\.?(?:sc|a|com|tech|ba|ca|ed)|bca|bba|bcom|b\.?e\.?|b\.?tech)\b", re.IGNORECASE), "ug"),
]


def detect_level(programme_code: str | None, text: str | None) -> tuple[str | None, list[str]]:
    """Detect the programme level from code + document text keywords."""
    warnings: list[str] = []
    combined = f"{programme_code or ''} {text or ''}"
    lowered = combined.lower()

    for word, level in sorted(_LEVEL_WORD_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", lowered):
            return level, warnings

    for pattern, level in _CODE_LEVEL_HINTS:
        if pattern.search(lowered):
            return level, warnings

    if not programme_code and not text:
        warnings.append("Could not detect a programme level in the document.")
    return None, warnings


# ---------------------------------------------------------------------------
# Academic scheme detection (fully DB-driven)
# ---------------------------------------------------------------------------

def _scheme_hint_patterns(schemes: list[dict[str, Any]]) -> dict[str, list[re.Pattern]]:
    """Build regex patterns per scheme from its stored name/code plus generic
    domain keywords, so any future scheme is detected without code changes."""
    generic: dict[str, list[str]] = {
        "nep2020": ["nep 2020", "nep2020", "national education policy",
                    "new education policy", "nep-2020", "fyugp", "fygup"],
        "traditional": ["cbcs", "choice based credit system", "choice-based credit system",
                        "traditional curriculum", "conventional curriculum", "old curriculum"],
    }
    out: dict[str, list[re.Pattern]] = {}
    for scheme in schemes:
        code = (scheme.get("code") or "").lower()
        words = [str(scheme.get("name") or "").lower(), code] + generic.get(code, [])
        patterns = [re.compile(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", re.IGNORECASE)
                    for w in words if w]
        out[code] = patterns
    return out


def detect_scheme(text: str | None, db=None) -> dict[str, Any]:
    """Detect an academic scheme mentioned in the document.

    Returns {"code", "name", "id", "matched"} or {"matched": False} — the
    review screen then lets the admin choose a scheme explicitly.
    """
    if not text:
        return {"matched": False}
    lowered = " ".join(str(text).lower().split())
    try:
        schemes = service.list_academic_schemes(db=db)
    except Exception:
        schemes = []
    hint_map = _scheme_hint_patterns(schemes)
    for code, patterns in hint_map.items():
        for pattern in patterns:
            if pattern.search(lowered):
                for s in schemes:
                    if (s.get("code") or "").lower() == code:
                        return {
                            "matched": True,
                            "code": s["code"],
                            "name": s["name"],
                            "id": s["id"],
                        }
    return {"matched": False}


# ---------------------------------------------------------------------------
# Programme detection
# ---------------------------------------------------------------------------

# Full degree-title patterns seen at line starts: "Bachelor of ... (BCA)".
_DEGREE_TITLE = re.compile(
    r"(?im)^\s*(?P<degree>(?:bachelor|master|doctor|post\s?graduate)\s+of\s+"
    r"(?:[a-z][a-z0-9 &/\\'-]{2,60}))\s*(?:\((?P<code>[a-z][a-z0-9.\- ]{1,15})\))?"
)
_DEGREE_SIMPLE = re.compile(r"(?im)^\s*(?P<degree>(?:bachelor|master|doctor)(?:'s)?\s+[a-z][a-z0-9 &/\\'-]{2,60})")
# Bare code headers ("BCA", "B.Sc.", "MBA") on their own line.
_BARE_CODE = re.compile(
    r"(?im)^\s*(?P<code>"
    r"(?:b\.?(?:sc|a|com|tech|ba|ca|ed|phil)|m\.?(?:sc|a|com|ba|tech|ca|phil|ed)"
    r"|bca|bba|bcom|mba|mca|ll\.?m|ph\.?d|b\.?e\b|m\.?tech|b\.?tech)"
    r"(?:\.|\)|\s|-)?)\s*(?:[:.-]?\s*(?P<rest>[a-z0-9 ,&/'-]{3,80}))?\s*$",
    re.IGNORECASE,
)

_KNOWN_CODE_NAMES: dict[str, str] = {
    "bca": "Bachelor of Computer Applications",
    "bba": "Bachelor of Business Administration",
    "bcom": "Bachelor of Commerce",
    "b.sc": "Bachelor of Science",
    "b.a": "Bachelor of Arts",
    "b.tech": "Bachelor of Technology",
    "b.ed": "Bachelor of Education",
    "mca": "Master of Computer Applications",
    "mba": "Master of Business Administration",
    "mcom": "Master of Commerce",
    "m.sc": "Master of Science",
    "m.a": "Master of Arts",
    "m.tech": "Master of Technology",
    "phd": "Doctor of Philosophy",
    "ph.d": "Doctor of Philosophy",
    "ll.m": "Master of Laws",
}


def _normalise_code(raw: str) -> str:
    code = re.sub(r"[.\-\[\]]", "", str(raw or "")).strip().lower()
    code = re.sub(r"\s+", " ", code)
    return code


def detect_programme(text: str | None, db=None) -> dict[str, Any]:
    """Detect the programme the document belongs to.

    Order of resolution:
      1. existing catalogue programmes mentioned anywhere in the document
         (smart mapping — never duplicates a programme)
      2. degree-title header ("Bachelor of Computer Applications (BCA)")
      3. bare code header ("BCA", "B.Sc.")
    Returns {"name", "code", "level", "matched", "confidence", "existing": id|None}.
    """
    if not text:
        return {"matched": False, "confidence": 0.0}
    head = "\n".join(str(text)[:4000].split("\n")[:80])

    # 1) Existing catalogue match (full-document, DB-driven).
    try:
        existing = service.resolve_programme(str(text)[:6000], db=db)
    except Exception:
        existing = None
    if existing:
        return {
            "matched": True,
            "name": existing.get("name"),
            "code": existing.get("code"),
            "level": existing.get("level"),
            "confidence": 0.95,
            "existing": existing.get("id"),
        }

    # 2) Degree-title header.
    m = _DEGREE_TITLE.search(head) or _DEGREE_SIMPLE.search(head)
    if m:
        name = " ".join(m.group("degree").strip().title().split())
        code = m.groupdict().get("code")
        code = _normalise_code(code) if code else None
        if code and len(code) <= 12 and re.fullmatch(r"[a-z][a-z0-9.\- ]{1,11}", code):
            code = _normalise_code(code)
        else:
            code = None
        level, _ = detect_level(code, name)
        return {
            "matched": True,
            "name": name,
            "code": code,
            "level": level,
            "confidence": 0.8 if code else 0.6,
            "existing": None,
        }

    # 3) Bare code header.
    for m in _BARE_CODE.finditer(head):
        raw = m.group("code")
        code = _normalise_code(raw)
        if not code or code in ("b", "m", "a", "s", "c"):
            continue
        name = _KNOWN_CODE_NAMES.get(code)
        level, _ = detect_level(code, head)
        return {
            "matched": True,
            "name": name,
            "code": code,
            "level": level,
            "confidence": 0.7 if name else 0.5,
            "existing": None,
        }

    return {"matched": False, "confidence": 0.0}
