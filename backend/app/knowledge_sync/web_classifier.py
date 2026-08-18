"""
backend/app/knowledge_sync/web_classifier.py

Page classification for the Website Knowledge Sync engine.

Maps a crawled page (its title, URL path and extracted text) to one of the
supported CUS knowledge categories:

  admissions, examinations, departments, programmes, news, notices, faculty,
  scholarships, hostels, transport, administration, research, academic-calendar,
  events, student-services, policies, downloads, unknown

Classification is deterministic: keyword/regex scoring with higher weights for
title and URL matches; ties resolve to the highest scoring category.
"""

from __future__ import annotations

import re
from typing import Any

CATEGORIES = [
    "admissions", "examinations", "departments", "programmes", "news", "notices",
    "faculty", "scholarships", "hostels", "transport", "administration",
    "research", "academic-calendar", "events", "student-services", "policies",
    "downloads", "unknown",
]

# (category, list of keyword regex fragments)
CATEGORY_PATTERNS: list[tuple[str, list[str]]] = [
    ("admissions", [
        r"admission", r"prospectus", r"apply\s?online", r"enrol", r"enroll",
        r"cut\s?off", r"counseling", r"counselling", r"entrance\s*(test|exam)?",
        r"merit\s*list", r"registration", r"intake",
    ]),
    ("examinations", [
        r"examination", r"exam\s*schedule", r"date\s?sheet", r"admit\s?card",
        r"hall\s?ticket", r"revaluation", r"supplementar", r"backlog",
        r"examination\s*cell", r"practical\s*exam",
    ]),
    ("departments", [
        r"department", r"schools?\b", r"centres?\b", r"labs?\b", r"library\b",
        r"hod\b", r"head\s+of\s+department", r"ic\b", r"departments",
    ]),
    ("programmes", [
        r"programme", r"program\b", r"course\s*offered", r"syllabus", r"syllabi",
        r"curriculum", r"\bb\.?\s?sc\b", r"\bb\.?\s?[acd]\b", r"\bb\.?ca",
        r"\bm\.?\s?sc\b", r"\bm\.?ca", r"\bm\.?\s?[acd]\b", r"\bph\.?\s?d",
        r"\bdiploma\b", r"certificate\s*course", r"\bsubject\b", r"\belective\b",
        r"\bunder[ -]?graduate\b", r"\bpost[ -]?graduate\b", r"\bug\b", r"\bpg\b",
    ]),
    ("news", [
        r"\bnews\b", r"press\s?release", r"media\b", r"newspaper", r"bulletin",
        r"what'?s?\s?new", r"latest\s*news", r"newsletter",
    ]),
    ("notices", [
        r"notice", r"notification", r"circular", r"order\b", r"announcement",
        r"tender", r"advertisement", r"public\s?notice",
    ]),
    ("faculty", [
        r"faculty", r"staff\b", r"teaching\s?staff", r"non[-_ ]?teaching",
        r"professors?\b", r"lecturer", r"tutor", r"instructors?\b", r"lab\s?technician",
    ]),
    ("scholarships", [
        r"scholarship", r"fellowship", r"financial\s?aids?", r"stipend",
        r"merit\s*reward", r"free\s?studentship",
    ]),
    ("hostels", [
        r"hostel", r"accommodation", r"boarding", r"residence", r"student\s?home",
    ]),
    ("transport", [
        r"transport", r"bus\s*service", r"shuttle", r"car\s?parking", r"conveyance",
    ]),
    ("administration", [
        r"administration", r"registrar", r"controller", r"vc\b", r"vice\s?chancellor",
        r"pro\s?vice", r"dean\b", r"adminstration", r"administration\s?block",
        r"officer", r"committee", r"governing\s*body",
    ]),
    ("research", [
        r"research", r"journal", r"publication", r"ph\.?\s?d", r"scholars?",
        r"patent", r"innovation", r"seed\s*money", r"research\s*cell",
    ]),
    ("academic-calendar", [
        r"academic\s?calendar", r"calendar\b", r"semester\s?schedule",
        r"academic\s?session", r"datesheet", r"important\s?dates",
        r"academic\s?term",
    ]),
    ("events", [
        r"event", r"workshop", r"seminar", r"conference", r"convocation",
        r"cultural\s*fest", r"tech\s*fest", r"fresher", r"guest\s?lecture",
    ]),
    ("student-services", [
        r"student\s?services", r"student\s?support", r"grievance", r"complaint",
        r"helpdesk", r"student\s?corner", r"forms?\s*download", r"counselling\s+cell",
        r"alumni", r"placement\s*cell", r"career", r"internship", r"e\s?-?\s?governance",
    ]),
    ("policies", [
        r"policy", r"regulations", r"ordinance", r"statutes?", r"acts?\b",
        r"code\s*of\s*conduct", r"anti[-_ ]?ragging", r"grievance\s*redressal",
        r"rti\b", r"right\s+to\s+info", r"disclaimer", r"terms\s+of\s+use",
    ]),
    ("downloads", [
        r"download", r"downloads", r"forms?", r"application\s*form", r"brochure",
        r"e[-_]?forms?", r"documents", r"files?\b",
    ]),
]

_SCORED: list[tuple[str, list[re.Pattern]]] = []


def _precompile() -> None:
    global _SCORED
    if _SCORED:
        return
    for category, fragments in CATEGORY_PATTERNS:
        compiled = [re.compile(f, re.IGNORECASE) for f in fragments]
        _SCORED.append((category, compiled))


def normalize_title(title: str) -> str:
    """Lowercase alphanumeric-only version used for title-similarity dedup."""
    if not title:
        return ""
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def _count_hits(text: str, patterns: list[re.Pattern]) -> int:
    """Number of distinct keyword fragments matching the text (max 1 each)."""
    if not text:
        return 0
    hits = 0
    for pat in patterns:
        if pat.search(text):
            hits += 1
    return hits


def classify_page(*, title: str = "", url: str = "", text: str = "") -> str:
    """
    Classify a page into one of the CATEGORIES.
    Title and URL tokens are weighted more heavily than body text.

    Fallbacks:
      - the page slug (last URL segment) boosts matching keywords,
      - pages with no signal at all return "unknown".
    """
    _precompile()
    scores: dict[str, float] = {}
    title_l = title or ""
    url_l = url or ""
    text_l = (text or "")[:4000]
    # Slug tokens also act as strong signal (e.g. /admission-2026).
    slug = (url_l.rstrip("/").rsplit("/", 1)[-1] or "")

    for category, patterns in _SCORED:
        score = 0
        score += _count_hits(title_l, patterns) * 4
        score += _count_hits(slug, patterns) * 3
        score += _count_hits(url_l, patterns) * 2
        score += _count_hits(text_l, patterns) * 1
        if score > 0:
            scores[category] = scores.get(category, 0) + score

    if not scores:
        return "unknown"
    # Ties: prefer the first-listed category (admissions etc.) deterministically.
    best = max(scores.items(), key=lambda kv: (kv[1], -CATEGORIES.index(kv[0])))
    return best[0]


def document_category_for(content_type: str) -> str:
    """Best-effort default category for binary documents based on extension."""
    return "downloads"