"""
backend/app/catalogue/parser/extract.py

Turn raw curriculum text + tables into the structured payload the catalogue
understands. The extractor is scheme-agnostic on purpose: semester/credit/
category/outcome detection works for NEP 2020, Traditional, CBCS and future
schemes without code changes.

Returned payload (stored in curriculum_uploads.payload):

  title            - suggested document title
  scheme           - {"code", "name"} or None
  academic_session - e.g. "2024-2026" or None
  revision         - revision/year or None
  programme        - {"name","code","level","duration_years","total_credits",
                       "eligibility","fee_structure","major_disciplines",
                       "description"}
  semesters        - [{"number": int, "subjects":[{category, code, name,
                       credits, hours}]}]
  minors           - [{"name": str, "subjects":[...]}]
  outcomes         - list[str]
  summary          - {"semesters", "subjects", "outcomes", "minors", "fee_entries"}
  warnings         - list[str]
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Semester helpers
# ---------------------------------------------------------------------------

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
          "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12}
_WORD_NUM = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
             "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}

# "Semester 1 | Sem-IV | 3rd Semester | Fourth Semester | Semester: II"
_SEM_HEADER = re.compile(
    r"(?ix)^\s*(?:semester|sem)\s*[-_:)\.]?\s*(?P<n1>[ivx]{1,4}|\d{1,2})\b"
    r"|^\s*(?P<n2>\d{1,2}|[ivx]{1,4})\s*(?:st|nd|rd|th)?\s*(?:semester|sem)\b"
    r"|^\s*(?P<n3>first|second|third|fourth|fifth|sixth|seventh|eighth)\s+semester"
)
# In-line references: "Semester 3", "sem-4", "3rd semester", "SEM V".
_SEM_REF = re.compile(
    r"(?i)\b(?:sem(?:ester)?)\s*[-_:)\.]?\s*(?P<n1>\d{1,2}|[ivx]{1,4})\b"
    r"|(?P<n2>\d{1,2})(?:st|nd|rd|th)\s*(?:sem|semester)\b",
)


def _to_int(value) -> int | None:
    v = str(value or "").strip().lower()
    if not v:
        return None
    if v in _ROMAN:
        return _ROMAN[v]
    if v in _WORD_NUM:
        return _WORD_NUM[v]
    try:
        n = int(float(v))
    except (ValueError, TypeError):
        return None
    return n if 1 <= n <= 12 else None


def _inline_sem_number(line: str) -> int | None:
    m = _SEM_REF.search(line)
    if not m:
        return None
    num = _to_int(m.group("n1") or m.group("n2"))
    if num is None:
        # roman like "IV" already handled by _to_int
        return None
    return num if 1 <= num <= 12 else None


def _semester_headers(lines: list[str]) -> dict[int, int]:
    """line_index -> semester number for heading lines matching semester headers."""
    out: dict[int, int] = {}
    for idx, line in enumerate(lines):
        m = _SEM_HEADER.match(line.strip())
        if not m:
            continue
        num = _to_int(m.group("n1") or m.group("n2") or m.group("n3"))
        if num:
            out[idx] = num
    return out


# ---------------------------------------------------------------------------
# Subject / category detection
# ---------------------------------------------------------------------------

# Course codes: "CA101", "BCC-3.1", "SEC101", "MCA-201", "AECC-II".
_COURSE_CODE = re.compile(r"(?<!\w)([A-Za-z]{1,5})-?(\d{1,4})(?!\w)")
# Table-first cell that is a course code ("CA101", "BCC-3.1").
_COURSE_CODE_CELL = re.compile(r"(?i)^[a-z]{1,5}-?\d{1,4}$")

_CATEGORY_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(major|core)\b", re.IGNORECASE), "major"),
    (re.compile(r"\b(minor)\b", re.IGNORECASE), "minor"),
    (re.compile(r"\b(vac|value[ -]?added)\b", re.IGNORECASE), "vac"),
    (re.compile(r"\b(sec|skill[ -]?enhancement)\b", re.IGNORECASE), "sec"),
    (re.compile(r"\b(aec|ability[ -]?enhancement)\b", re.IGNORECASE), "aec"),
    (re.compile(r"\b(dse|discipline[ -]?specific elective|open[ -]?elective|elective)\b", re.IGNORECASE), "generic"),
    (re.compile(r"\b(generic|inter-?disciplinary|open)\b", re.IGNORECASE), "generic"),
    (re.compile(r"\b(practical|laboratory|lab|training|industrial)\b", re.IGNORECASE), "generic"),
]
_HEADER_LINE = re.compile(
    r"(?i)\b(course\s+code|subject\s+code|code\s+of|course\s+title|subject\s+title"
    r"|name\s+of|credit\s+(?:points|distribution)?|total\s+credits|paper\s+code|th|pr|tu|practical)\b"
)

_CREDITS = re.compile(r"\b(\d{1,2})\s*(?:credits?|cr\.?)\b", re.IGNORECASE)
_HOURS = re.compile(r"\b(\d{1,3})\s*(?:hours?|hrs?)\b", re.IGNORECASE)


def _category_of(text: str) -> str:
    for pattern, cat in _CATEGORY_KEYWORDS:
        if pattern.search(text):
            return cat
    return "major"


def _category_of_code(code: str) -> str:
    """Category hint from a course-code prefix (SEC101 -> sec, AEC111 -> aec, VAC => vac)."""
    prefix = re.sub(r"[^a-zA-Z]", "", (code or ""))[:3].lower()
    return {
        "sec": "sec", "aec": "aec", "vac": "vac", "maj": "major",
        "min": "minor", "gen": "generic", "dse": "generic", "ele": "generic",
    }.get(prefix, "major")


def _line_is_noise(line: str) -> bool:
    lowered = line.strip().lower()
    if not lowered:
        return True
    if _HEADER_LINE.search(lowered) and len(lowered) < 60:
        return True
    if lowered == "total" or lowered.startswith(("total ", "grand total", "credits:")):
        return True
    if re.fullmatch(r"[\s.\-–—_|\\/]+", lowered):
        return True
    if len(lowered) < 3:
        return True
    return False


# ---------------------------------------------------------------------------
# Section blocks (outcomes, eligibility, fee, minors, majors)
# ---------------------------------------------------------------------------

_OUTCOME_SECTION = re.compile(
    r"^.{0,20}\b(?:programme|program)\s?(?:learning\s+)?outcomes?\b"
    r"|\blearning\s+outcomes?\b|\bcourse\s+outcomes?\b|^outcomes?\b"
    r"|\b(?:programme\s+)?specific\s+outcomes?\b|\b(g?:po|co|pso|plo|slo)s?\s*[:.]?\s*\b",
    re.IGNORECASE | re.MULTILINE,
)
_ELIG_SECTION = re.compile(
    r"(?i)^\s*(?:eligibility|eligibility\s+criteria|minimum\s+qualification|"
    r"admission\s+requirements?|requirements\s+for\s+admission)\b"
)
_FEE_SECTION = re.compile(r"(?i)^\s*(?:fee|fees|fee\s+structure|tuition(?:\s+fees)?)\s*[:]?\b")
_MINOR_SECTION = re.compile(r"(?i)^\s*(?:minor\s+(?:disciplines?|subjects?)|minors?)\b")
_MAJOR_SECTION = re.compile(r"(?i)^\s*(?:major\s+(?:disciplines?|subjects?)|majors?)\b")
_SEM_SECTION = re.compile(r"(?i)^\s*sem(?:ester)?\b")


def _section_block(lines: list[str], start: int, max_len: int = 60,
                   stop_on: tuple[re.Pattern, ...] | None = None,
                   head_tail: str = "") -> list[str]:
    """Collect body lines after a section header.

    Optional `head_tail` prepends the remainder of the header line itself
    (e.g. "Eligibility: 10+2 with Mathematics" — the "10+2 ..." part lives on
    the header line) so content on the header line is not lost.
    """
    block: list[str] = []
    if head_tail:
        block.append(head_tail)
    stop = stop_on if stop_on is not None else (
        _OUTCOME_SECTION, _ELIG_SECTION, _FEE_SECTION, _MINOR_SECTION,
        _MAJOR_SECTION, _SEM_SECTION,
    )
    for idx in range(start + 1, min(len(lines), start + max_len)):
        cand = lines[idx].strip()
        if not cand:
            continue
        if _SEM_HEADER.match(cand) or any(p.search(cand) for p in stop):
            break
        block.append(cand)
    return block


def _header_tail(line: str, section_re: re.Pattern) -> str:
    """'Eligibility: 10+2 Maths' -> '10+2 Maths'. '' if nothing after colon."""
    m = section_re.search(line)
    if not m:
        return ""
    tail = line[m.end():]
    if tail.startswith(":"):
        tail = tail[1:]
    return tail.strip()


# ---------------------------------------------------------------------------
# Field matchers
# ---------------------------------------------------------------------------

_SESSION_RE = re.compile(r"(?i)\b(\d{4})\s*[-––]\s*(\d{4})\b")
_REVISION_RE = re.compile(r"(?i)\b(?:revision(?:s)?\s*[:\-]?\s*|rev[.\s]?)(\d{1,4})\b")
_DURATION_RE = re.compile(r"\b(\d{1,2})\s*years?(?:\s*programme?)?\b|\bduration\s*[:\-]?\s*(\d{1,2})\b", re.IGNORECASE)
_TOTAL_CREDITS = re.compile(r"(?i)\btotal\s+credits?\s*[:=]?\s*(\d{1,3})\b")
_MAJOR_NAME = re.compile(r"(?i)^\s*(?:[-•*o]+\s*|\d+[.)]\s*)?([a-z][a-z0-9 ,&/\\'-]{2,40})$")


def _first_region(pattern: re.Pattern, text: str, group: int | None = None) -> str | None:
    m = pattern.search(text)
    if not m:
        return None
    if group:
        val = m.group(group)
    else:
        val = next((g for g in m.groups() if g), None)
    return val.strip() if val else None


# ---------------------------------------------------------------------------
# Table / spreadsheet parsing
# ---------------------------------------------------------------------------


_HEADER_ALIASES = (
    (re.compile(r"(?i)\bcode\b|code\s*\(?code\)?"), "code"),
    (re.compile(r"(?i)\b(name|title|subject|paper)\b"), "name"),
    (re.compile(r"(?i)\b(category|type|nature|choice)\b"), "cat"),
    (re.compile(r"(?i)\bcredit"), "cred"),
    (re.compile(r"(?i)\bsem(?:ester)?\b"), "sem"),
    (re.compile(r"(?i)\b(hours?|hrs?|lec)\b"), "hours"),
)


def _find_header_row(grid: list[list[str]]) -> tuple[int, dict[str, int]]:
    """Locate the row that actually contains column headers.

    Skips leading title rows (e.g. a single cell "Bachelor of Computer
    Applications (BCA) - NEP 2020" on the first CSV line) and returns the
    header index plus the column map. Falls back to row 0 with an empty map.
    """
    best_idx, best_score, best_map = 0, 0, {}
    for idx, row in enumerate(grid[:12]):
        cells = [str(c).strip().lower() for c in row if str(c).strip()]
        if not cells:
            continue
        cmap: dict[str, int] = {}
        score = 0
        for pos, cell in enumerate(cells):
            for pattern, key in _HEADER_ALIASES:
                if key not in cmap and pattern.search(cell):
                    cmap[key] = pos
                    score += 1
                    break
        if score > best_score:
            best_idx, best_score, best_map = idx, score, cmap
    return best_idx, best_map


def _tables_to_subjects(tables: list[list[list[str]]]) -> list[dict[str, Any]]:
    """Extract subject records from grid cells (spreadsheet / CSV)."""
    subjects: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for grid in tables:
        if not grid:
            continue
        ncols = max(len(g) for g in _nonempty_rows(grid))
        hdr_idx, cmap = _find_header_row(grid)
        c_code = cmap.get("code", -1)
        c_name = cmap.get("name", -1)
        c_cat = cmap.get("cat", -1)
        c_cred = cmap.get("cred", -1)
        c_sem = cmap.get("sem", -1)

        for row in grid[hdr_idx + 1:]:
            if not row:
                continue
            cells = [str(c).strip() for c in row[: ncols]]
            if not any(cells):
                continue
            if all(not c for c in cells[:2]):
                continue
            clean_cells = [c for c in cells if c]
            if not clean_cells:
                continue
            # naive fallback when no header aliases found:
            if c_code < 0:
                lead = clean_cells[0]
                if not re.match(r"^[a-z]{1,5}-?\d{1,4}$", lead, re.IGNORECASE):
                    # maybe row has [name, credits] or [sem, name, credits]
                    if len(clean_cells) >= 2 and _to_int(clean_cells[0]) is not None:
                        sem_hint, rest = clean_cells[0], clean_cells[1:]
                        if rest:
                            code_full, name_full = "", " ".join(rest)
                            sem_val = _to_int(sem_hint)
                        else:
                            continue
                    else:
                        continue
                else:
                    code_full = clean_cells[0]
                    tail = [c for c in clean_cells[1:] if not re.fullmatch(r"[\d,.]+", c)]
                    name_full = " ".join(tail)
                    sem_val = None
            else:
                code_full = cells[c_code] if c_code < len(cells) else ""
                name_full = cells[c_name] if c_name >= 0 and c_name < len(cells) else " ".join(
                    c for i, c in enumerate(cells) if i not in (c_code, c_cred, c_cat, c_sem)
                )
                sem_val = _to_int(cells[c_sem]) if c_sem >= 0 and c_sem < len(cells) else None
            credits = None
            if c_cred >= 0 and c_cred < len(cells):
                credits = _to_int(cells[c_cred])
            cat = "major"
            if c_cat >= 0 and c_cat < len(cells) and cells[c_cat]:
                cat = _category_of(cells[c_cat])
            name_full = re.sub(r"\s+", " ", name_full).strip()
            code_full = re.sub(r"\s+", "", (code_full or "")).upper()
            if not name_full:
                continue
            key = (sem_val, code_full, name_full)
            if key in seen:
                continue
            seen.add(key)
            subjects.append({
                "category": cat, "code": code_full[:24] or "", "name": name_full[:180],
                "credits": credits, "hours": None, "semester": sem_val,
            })
    return subjects


def _nonempty_rows(grid: list[list[str]]) -> list[list[str]]:
    return [list(r) for r in grid if any(c for c in r)]


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------


def extract_curriculum(
    pages: list[dict],
    tables: list[list[list[str]]] | None = None,
    hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract a structured curriculum record from raw pages + optional tables.

    `hints` carries anything already known: {"title", "programme_name",
    "programme_code", "level", "scheme"} — used to fill the programme block.
    """
    hints = hints or {}
    warnings: list[str] = []
    text = "\n".join((p.get("text") or "") for p in pages)
    capped = text[:400000]
    lines = capped.splitlines()

    # ---- unit tests that only tables are given ---------------------------------

    # ---- semester headings ------------------------------------------------------
    header_map = _semester_headers(lines)
    if not header_map:
        warnings.append("No semester headings detected — subjects may need manual semester assignment.")

    # ---- primitives -------------------------------------------------------------
    academic_session = _extract_session(capped)
    revision = _first_region(_REVISION_RE, capped)
    duration = _first_region(_DURATION_RE, capped)
    duration = _to_int(duration) if duration else None
    total_credits = _first_region(_TOTAL_CREDITS, capped)
    total_credits = _to_int(total_credits) if total_credits else None

    # ---- subject scan (text) ---------------------------------------------------
    text_subjects: list[dict[str, Any]] = []
    current_sem: int | None = None
    subjects_seen: set[tuple] = set()
    for idx, line in enumerate(lines[:500]):
        stripped = line.strip()
        if not stripped:
            continue
        if idx in header_map:
            current_sem = header_map[idx]
            continue
        if _OUTCOME_SECTION.search(stripped) or _FEE_SECTION.search(stripped) \
                or _ELIG_SECTION.search(stripped) or _MINOR_SECTION.search(stripped) \
                or _MAJOR_SECTION.search(stripped):
            continue

        if _line_is_noise(stripped):
            continue
        m_code = _first_code_match(stripped)
        cred, hrs = _split_credit_hours(stripped)
        if m_code and len(stripped) < 160:
            name = _strip_code_from(stripped, m_code)
            cat = _category_of(stripped)
            if cat == "major":
                cat = _category_of_code(m_code)
            key = (current_sem, m_code.lower(), name)
            if key not in subjects_seen:
                subjects_seen.add(key)
                text_subjects.append({
                    "category": cat, "code": m_code.upper(), "name": name,
                    "credits": cred, "hours": hrs, "semester": current_sem,
                })

    # ---- table subjects merge ----------------------------------------------------
    table_subjects = _tables_to_subjects(tables or []) if tables else []

    merged: dict[tuple, dict] = {}
    for s in text_subjects + table_subjects:
        key = (s["semester"], s.get("code", "").lower(), s.get("name", "").lower())
        if key not in merged:
            merged[key] = s
    subjects = list(merged.values())

    sem_groups: dict[int, list[dict]] = {}
    loose: list[dict] = []
    for s in subjects:
        if s.get("semester"):
            sem_groups.setdefault(int(s["semester"]), []).append(s)
        else:
            loose.append(s)
    if loose:
        warnings.append("Some subjects have no semester — check them in review before publishing.")
    for g in sem_groups.values():
        g.sort(key=lambda s: (s.get("code") or "").lower())

    # ---- outcomes --------------------------------------------------------------
    outcomes: list[str] = []
    for idx, line in enumerate(lines[:4000]):
        stripped = line.strip()
        if _OUTCOME_SECTION.search(stripped):
            tail = _header_tail(stripped, _OUTCOME_SECTION)
            collected = _section_block(lines, idx, head_tail=tail)
            for cand in collected:
                bullet = re.sub(r"^[\s\-•*o_\d().]+", "", cand).strip()
                if len(bullet) >= 12 and bullet not in outcomes:
                    outcomes.append(bullet[:400])
            break  # single outcome block per doc, keep it simple

    # ---- eligibility -------------------------------------------------------------
    eligibility = None
    for idx, line in enumerate(lines[:4000]):
        if _ELIG_SECTION.search(line.strip()):
            tail = _header_tail(line.strip(), _ELIG_SECTION)
            block = _section_block(lines, idx, head_tail=tail)
            if block:
                eligibility = " ".join(block).strip()
            break
    if eligibility and len(eligibility) > 600:
        eligibility = eligibility[:600]

    # ---- fee structure -----------------------------------------------------------
    fee_lines: list[str] = []
    for idx, line in enumerate(lines[:4000]):
        if _FEE_SECTION.search(line.strip()):
            tail = _header_tail(line.strip(), _FEE_SECTION)
            # stop on other sections, but NOT on fee lines themselves
            stop = (_OUTCOME_SECTION, _ELIG_SECTION, _MINOR_SECTION,
                    _MAJOR_SECTION, _SEM_SECTION)
            collected = _section_block(lines, idx, max_len=40, stop_on=stop,
                                       head_tail=tail)
            fee_lines.extend(collected)
            break
    fee_structure = _parse_fee(fee_lines)

    # ---- minor groups ------------------------------------------------------------
    minor_names: list[str] = []
    minors: list[dict[str, Any]] = []
    for idx, line in enumerate(lines[:4000]):
        if _MINOR_SECTION.search(line.strip()):
            block = _section_block(lines, idx)
            for cand in block:
                n = re.sub(r"^[\s\-•*o\d().]+", "", cand).strip()
                if n and 2 <= len(n) <= 60 and not re.search(r"\d{3,}", n):
                    if n not in minor_names:
                        minor_names.append(n)
            break
    minor_subjects: dict[str, list[dict]] = {}
    generic_minor = "Generic Minor"
    for s in subjects:
        if s.get("category") == "minor":
            target = minor_names[0] if minor_names else generic_minor
            minor_subjects.setdefault(target, []).append(s)
    if minor_names:
        for name in minor_names:
            minors.append({"name": name, "subjects": minor_subjects.get(name, [])})
    else:
        for name, items in minor_subjects.items():
            minors.append({"name": name, "subjects": items})
        if minor_subjects:
            warnings.append("Minor subjects detected but no minor discipline names — review group names.")

    # ---- major disciplines ------------------------------------------------------
    major_disciplines = None
    for idx, line in enumerate(lines[:2000]):
        if _MAJOR_SECTION.search(line.strip()):
            block = _section_block(lines, idx)
            names = []
            for cand in block:
                c = re.sub(r"^[\s\-•*o·\d().]+", "", cand).strip()
                if c and 2 <= len(c) <= 60 and not re.search(r"\d{3,}", c) and not _HEADER_LINE.search(c):
                    names.append(c)
            if names:
                major_disciplines = names[:20]
            break

    # ---- programme block ---------------------------------------------------------
    programme = {
        "name": hints.get("programme_name"),
        "code": hints.get("programme_code"),
        "level": hints.get("level"),
        "duration_years": duration,
        "total_credits": total_credits,
        "eligibility": eligibility,
        "fee_structure": fee_structure or None,
        "major_disciplines": major_disciplines,
        "description": hints.get("description"),
    }

    grouped = [{"number": sem, "subjects": items} for sem, items in sorted(sem_groups.items())]

    summary = {
        "semesters": len(grouped),
        "subjects": len(subjects),
        "outcomes": len(outcomes),
        "minors": len(minors),
        "fee_entries": len(fee_structure or []),
    }

    warnings.append(
        "Auto-detected fields are shown in the review screen — please verify the "
        "programme, scheme, level and per-semester subjects before publishing."
    )

    return {
        "title": (hints.get("programme_name") and f"{hints.get('programme_name')} Curriculum") or "Curriculum Document",
        "scheme": hints.get("scheme"),
        "academic_session": academic_session,
        "revision": revision,
        "programme": programme,
        "semesters": grouped,
        "minors": minors,
        "outcomes": outcomes,
        "fee_structure": fee_structure,
        "summary": summary,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Small helpers used above
# ---------------------------------------------------------------------------


def _first_code_match(line: str) -> str | None:
    m = _COURSE_CODE.search(line)
    return m.group(0) if m else None


def _strip_code_from(line: str, code: str) -> str:
    name = re.sub(rf"\b{re.escape(code)}\b", " ", line)
    name = re.sub(r"(?i)\b(?:[-–—(]?\s*)?\d{1,3}\s*(?:credits?|cr\.?|hours?|hrs?)\s*\)?", "", name)
    name = re.sub(r"\s{2,}", " ", name).strip()
    # trim trailing "(x credits)" leftover, trailing digits, stray parens/dashes
    name = re.sub(r"\s*\(.*\)\s*$", "", name)
    name = re.sub(r"\s+\d{1,2}$", "", name)
    name = name.strip(" -–—:.,;()")
    return name[:180]


def _split_credit_hours(line: str) -> tuple[int | None, int | None]:
    m = _CREDITS.search(line)
    credits = _to_int(m.group(1)) if m else None
    if credits is None or credits > 20:
        credits = None
    h = _HOURS.search(line)
    hours = _to_int(h.group(1)) if h else None
    if hours is not None and hours > 60:
        hours = None
    return credits, hours


def _extract_session(text: str) -> str | None:
    m = _SESSION_RE.search(text)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def _parse_fee(lines: list[str]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in lines:
        if _FEE_SECTION.search(line) and len(line) < 40 and ":" not in line:
            continue
        # "Tuition Fee: Rs. 25000" | "Tuition Fee : INR 25,000" | "Tuition fee - 25000"
        m = re.match(
            r"^(?P<label>[^0-9]{2,60}?)\s*(?::|\t|-+|\u2013|\u2014)?\s*"
            r"(?:(?:rs|inr)\s*\.?\s*|\u20b9\s*)?([\d,]+(?:\.[\d]{2})?)\s*\.?$",
            line.strip(), re.IGNORECASE,
        )
        if m:
            entries.append({"label": m.group(1).strip().strip(" :-\t"),
                            "value": f"Rs. {m.group(2)}"})
            continue
        # "Rs. 25000 — Tuition Fee" (amount first)
        m2 = re.match(
            r"^(?:(?:rs|inr)\s*\.?\s*|\u20b9\s*)(?P<amt>[\d,]+)\s*(?::|-)?\s*"
            r"(?P<label>.{3,60})$",
            line.strip(), re.IGNORECASE,
        )
        if m2:
            entries.append({"label": m2.group("label").strip().strip(" :-"),
                            "value": f"Rs. {m2.group('amt')}"})
    # dedupe + cap
    out = []
    for e in entries:
        label = e["label"].strip(" .,:")
        if label and not re.search(r"^total|^grand", label, re.I) and e not in out:
            out.append({"label": label, "value": e["value"]})
        if len(out) >= 12:
            break
    return out