"""
backend/app/orchestrator/query_understanding.py

Query Understanding Engine — lightweight, deterministic preprocessing layer.

Corrects spelling, expands abbreviations, normalizes aliases, and rewrites
ambiguous/incomplete queries into canonical form before the planner sees them.

NO LLM calls — pure dictionary + edit distance for speed.

Flow:
  raw_message
    → normalize_punctuation()
    → expand_abbreviations()
    → correct_spelling()
    → normalize_aliases()
    → score_confidence()
    → clean_message + metadata
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Domain-specific dictionary of known correct terms (lowercase)
# ---------------------------------------------------------------------------

# College names mapped to their canonical forms
COLLEGE_CANONICAL: dict[str, str] = {
    "government college for women": "Government College for Women M.A. Road",
    "government college for women ma road": "Government College for Women M.A. Road",
    "government college for women m.a. road": "Government College for Women M.A. Road",
    "govt college for women": "Government College for Women M.A. Road",
    "gcw ma road": "Government College for Women M.A. Road",
    "gcw m.a. road": "Government College for Women M.A. Road",
    "women's college srinagar": "Government College for Women M.A. Road",
    "amar singh college": "Amar Singh College",
    "amar singh college srinagar": "Amar Singh College",
    "sri pratap college": "Sri Pratap College",
    "s.p. college": "Sri Pratap College",
    "sp college": "Sri Pratap College",
    "sp college srinagar": "Sri Pratap College",
    "abdul ahad azad memorial degree college bemina": "Abdul Ahad Azad Memorial Degree College Bemina",
    "gdc bemina": "Abdul Ahad Azad Memorial Degree College Bemina",
    "degree college bemina": "Abdul Ahad Azad Memorial Degree College Bemina",
    "bemina college": "Abdul Ahad Azad Memorial Degree College Bemina",
    "iase srinagar": "Institute of Advanced Studies in Education",
    "institute of advanced studies in education": "Institute of Advanced Studies in Education",
    "government degree college anantnag": "Government Degree College Anantnag",
    "gdc anantnag": "Government Degree College Anantnag",
    "degree college anantnag": "Government Degree College Anantnag",
    "government degree college pulwama": "Government Degree College Pulwama",
    "gdc pulwama": "Government Degree College Pulwama",
    "degree college pulwama": "Government Degree College Pulwama",
    "government degree college kulgam": "Government Degree College Kulgam",
    "gdc kulgam": "Government Degree College Kulgam",
    "degree college kulgam": "Government Degree College Kulgam",
}

# Common misspellings → correct term (university domain)
COMMON_MISSPELLINGS: dict[str, str] = {
    "admisson": "admission",
    "admisssion": "admission",
    "admisssions": "admissions",
    "adimission": "admission",
    "addmission": "admission",
    "adimisions": "admissions",
    "admissionss": "admissions",
    "eligiblity": "eligibility",
    "eligibilty": "eligibility",
    "eligibilityy": "eligibility",
    "eligable": "eligibility",
    "ilegible": "eligibility",
    "elgibility": "eligibility",
    "reslt": "result",
    "reslts": "results",
    "rsult": "result",
    "reult": "result",
    "resullt": "result",
    "resuts": "results",
    "resut": "result",
    "contct": "contact",
    "contcat": "contact",
    "conatct": "contact",
    "contract": "contact",
    "fees": "fee",
    "feestructure": "fee structure",
    "fees structure": "fee structure",
    "documnts": "documents",
    "documets": "documents",
    "documnets": "documents",
    "docs": "documents",
    "ducoments": "documents",
    "syllbus": "syllabus",
    "sylabus": "syllabus",
    "syllabuss": "syllabus",
    "syllubs": "syllabus",
    "scolarship": "scholarship",
    "scholorship": "scholarship",
    "scholarshipp": "scholarship",
    "schlr": "scholarship",
    "durations": "duration",
    "duratn": "duration",
    "duraton": "duration",
    "prospectus": "prospectus",
    "prospectuss": "prospectus",
    "examination": "examination",
    "exmination": "examination",
    "exam": "examination",
    "exams": "examination",
    "datesheet": "datesheet",
    "date sheet": "datesheet",
    "dateshett": "datesheet",
    "dateshit": "datesheet",
    "departmnts": "departments",
    "departements": "departments",
    "dept": "departments",
    "depts": "departments",
    "facilites": "facilities",
    "facilties": "facilities",
    "facilty": "facilities",
    "notices": "notices",
    "notic": "notices",
    "notise": "notices",
    "placment": "placement",
    "palcement": "placement",
    "placementss": "placement",
    "intake": "seats",
    "intaks": "seats",
    "specialisation": "specializations",
    "specialization": "specializations",
    "subjects": "specializations",
    "carear": "career",
    "carrier": "career",
    "carrer": "career",
    "backlog": "backlog",
    "backlogs": "backlog",
    "baklog": "backlog",
    "transcrip": "transcript",
    "transcripe": "transcript",
}

# Abbreviation expansions
ABBREVIATIONS: dict[str, str] = {
    "info": "information",
    "dept": "department",
    "depts": "departments",
    "uni": "university",
    "cus": "Cluster University Srinagar",
    "clustr": "Cluster University",
    "govt": "government",
    "gdc": "Government Degree College",
    "gcw": "Government College for Women",
    "btech": "B.Tech",
    "bsc": "B.Sc",
    "bcom": "B.Com",
    "bba": "BBA",
    "bca": "BCA",
    "bed": "B.Ed",
    "ma": "MA",
    "msc": "M.Sc",
    "mcom": "M.Com",
    "mba": "MBA",
    "mca": "MCA",
    "med": "M.Ed",
    "phd": "PhD",
    "ug": "UG",
    "pg": "PG",
    "dyd": "DYD",
    "cuet": "CUET",
    "naac": "NAAC",
    "jkbose": "JKBOSE",
    "jk": "Jammu and Kashmir",
}

# Known topics (domain keywords that shouldn't be corrected away)
KNOWN_TOPICS: set[str] = {
    "admission", "admissions", "courses", "course", "fee", "eligibility",
    "duration", "dates", "documents", "results", "result", "datesheet",
    "syllabus", "scholarship", "scholarships", "notices", "notice",
    "downloads", "download", "hostel", "examination", "exam",
    "departments", "department", "colleges", "college", "contact",
    "about", "overview", "principal", "facilities", "facility",
    "library", "sports", "placement", "placements", "career",
    "prospectus", "seats", "intake", "specializations",
    "admission_mode", "admission process",
}

# Programme-level keywords that should not be corrected
PROGRAMME_KEYWORDS: set[str] = {
    "ba", "bsc", "bcom", "bba", "bca", "btech", "bed",
    "ma", "msc", "mcom", "mba", "mca", "med", "phd",
    "ba+b.ed", "ba+ma", "bsc+msc", "bca+mca", "bba+mba",
    "integrated", "ug", "pg", "dyd",
}


# ---------------------------------------------------------------------------
# Levenshtein distance for fuzzy matching
# ---------------------------------------------------------------------------

def _levenshtein(a: str, b: str) -> int:
    """Compute edit distance between two strings."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


def _fuzzy_correct(word: str, dictionary: set[str], max_dist: int = 2) -> str | None:
    """Find the closest match in a dictionary using edit distance."""
    if word in dictionary:
        return word
    if len(word) <= 2:
        return None
    best = None
    best_dist = max_dist + 1
    for candidate in dictionary:
        dist = _levenshtein(word, candidate)
        if dist < best_dist:
            best_dist = dist
            best = candidate
            if dist == 1:
                break
    return best


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_punctuation(text: str) -> str:
    """Normalize punctuation: collapse repeats, strip leading/trailing noise."""
    text = re.sub(r"[?!]+", "?", text)
    text = re.sub(r"[.]+", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

QueryResult = dict[str, Any]
"""Structure:
    original: str          — original user message
    clean: str             — normalized, corrected message
    corrected: bool        — whether spelling corrections were applied
    corrections: list      — list of (original_word, corrected_word)
    college_resolved: str | None  — college name if detected
    programme_resolved: str | None — programme ID if detected
    topic_resolved: str | None    — topic key if detected
    confidence: float      — 0.0–1.0 confidence in the interpretation
    expanded: bool         — whether abbreviations were expanded
"""


def process_query(text: str) -> QueryResult:
    """Process a raw user message through the query understanding pipeline.

    Returns a QueryResult dict with the cleaned message and metadata.
    The planner can use this to decide routing.
    """
    original = text.strip()
    if not original:
        return {
            "original": original,
            "clean": original,
            "corrected": False,
            "corrections": [],
            "college_resolved": None,
            "programme_resolved": None,
            "topic_resolved": None,
            "confidence": 1.0,
            "expanded": False,
        }

    # Step 1: Normalize punctuation and case
    clean = normalize_punctuation(original.lower())
    corrections: list[tuple[str, str]] = []

    # Step 2: Expand common abbreviations
    expanded = False
    words = clean.split()
    for i, w in enumerate(words):
        if w in ABBREVIATIONS:
            words[i] = ABBREVIATIONS[w]
            expanded = True
    clean = " ".join(words)

    # Step 3: College alias detection (word-boundary match)
    # Use lowercased version because abbreviation expansion (Step 2) may
    # have inserted capitalized words (e.g., "Government College for Women").
    # Regex word boundary avoids false positives (e.g., "gdc" matching inside "bgdc").
    college_resolved = None
    clean_lower = clean.lower()
    for alias, canonical in COLLEGE_CANONICAL.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", clean_lower):
            college_resolved = canonical
            break

    # Step 4: Correct common misspellings (word-level)
    corrected_words = []
    for w in clean.split():
        if w in COMMON_MISSPELLINGS:
            corrected = COMMON_MISSPELLINGS[w]
            corrections.append((w, corrected))
            corrected_words.append(corrected)
        else:
            corrected_words.append(w)
    clean = " ".join(corrected_words)

    # Step 5: Fuzzy correction for unknown words (only for longer words)
    known_words: set[str] = set()
    known_words.update(KNOWN_TOPICS)
    known_words.update(PROGRAMME_KEYWORDS)
    known_words.update(COMMON_MISSPELLINGS.values())
    known_words.update(ABBREVIATIONS.keys())
    known_words.update(ABBREVIATIONS.values())
    known_words.update(COLLEGE_CANONICAL.keys())
    known_words.update({w.lower() for w in COLLEGE_CANONICAL.values()})

    fuzzy_corrected = []
    for w in clean.split():
        if w in KNOWN_TOPICS or w in PROGRAMME_KEYWORDS:
            fuzzy_corrected.append(w)
            continue
        if w not in known_words and len(w) > 3:
            match = _fuzzy_correct(w, known_words)
            if match and match != w:
                corrections.append((w, match))
                fuzzy_corrected.append(match)
                continue
        fuzzy_corrected.append(w)
    clean = " ".join(fuzzy_corrected)

    # Step 6: Extract resolved entities from cleaned text
    programme_resolved = None
    topic_resolved = None
    for prog in PROGRAMME_KEYWORDS:
        pattern = r"\b" + re.escape(prog) + r"\b"
        if re.search(pattern, clean):
            programme_resolved = prog
            break
    for topic in KNOWN_TOPICS:
        pattern = r"\b" + re.escape(topic) + r"\b"
        if re.search(pattern, clean):
            topic_resolved = topic
            break

    # Step 7: Confidence scoring
    was_corrected = len(corrections) > 0
    confidence = 1.0
    if was_corrected:
        confidence -= min(0.15 * len(corrections), 0.4)
    if not programme_resolved and not topic_resolved and not college_resolved:
        confidence -= 0.1
    if expanded:
        confidence -= 0.05
    confidence = max(confidence, 0.3)

    return {
        "original": original,
        "clean": clean,
        "corrected": was_corrected,
        "corrections": corrections,
        "college_resolved": college_resolved,
        "programme_resolved": programme_resolved,
        "topic_resolved": topic_resolved,
        "confidence": round(confidence, 2),
        "expanded": expanded,
    }
