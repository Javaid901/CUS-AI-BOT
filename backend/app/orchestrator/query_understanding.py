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
    "admision": "admission",
    "attendence": "attendance",
    "attandance": "attendance",
    "attendace": "attendance",
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
    "curriculam": "curriculum",
    "curiculum": "curriculum",
    "curriclum": "curriculum",
    "curriculumm": "curriculum",
    "scolarship": "scholarship",
    "scholorship": "scholarship",
    "scholarshipp": "scholarship",
    "schlr": "scholarship",
    "scholerhip": "scholarship",
    "scholarshipss": "scholarships",
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
    "semster": "semester",
    "semestr": "semester",
    "semesterss": "semesters",
    "nepp": "nep",
    "nep 2000": "nep 2020",
    "nep 20200": "nep 2020",
    "progamme": "programme",
    "progam": "program",
    "programms": "programs",
    "programes": "programs",
    "progrmas": "programs",
    "cources": "courses",
    "cource": "course",
    "couse": "course",
    "corses": "courses",
    "coursess": "courses",
    "carrear": "career",
    "carear": "career",
    "carrier": "career",
    "carrer": "career",
    "backlog": "backlog",
    "backlogs": "backlog",
    "baklog": "backlog",
    "transcrip": "transcript",
    "transcripe": "transcript",
    "subjcts": "subjects",
    "subjct": "subject",
    "subjects": "subjects",
    "subjcets": "subjects",
    "subjecs": "subjects",
    "notifcation": "notification",
    "notications": "notifications",
    "notificatiom": "notification",
    "circualr": "circular",
    "anouncement": "announcement",
    "announcment": "announcement",
    "anoncements": "announcements",
    "holidayys": "holidays",
    "calnder": "calendar",
    "calenders": "calendars",
    "regstrar": "registrar",
    "registrar": "registrar",
    # Granular information-retrieval vocabulary (spec: field-level queries)
    "feee": "fee",
    "ffe": "fee",
    "fee strcture": "fee structure",
    "fees strcture": "fee structure",
    "feees": "fee",
    "durration": "duration",
    "durraton": "duration",
    "crdits": "credits",
    "crdit": "credits",
    "creddits": "credits",
    "creditt": "credits",
    "credit": "credits",
    "elegibility": "eligibility",
    "eligbility": "eligibility",
    "eligibilliity": "eligibility",
    "doccuments": "documents",
    "documenst": "documents",
    "doc": "documents",
    "semsters": "semesters",
    "semestrs": "semesters",
    "semestrer": "semesters",
    "smster": "semester",
    "smtr": "semester",
    "semister": "semester",
    "semiters": "semesters",
    "minorss": "minors",
    "major_disciplines": "major disciplines",
    "major courses": "majors",
    "major course": "majors",
    "minor courses": "minors",
    "minor course": "minors",
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
    "fyugp": "FYUGP",
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
    # Navigation controls — must NEVER be fuzzy-corrected into programme or
    # topic lookalikes ("back" -> "ba" is a 1-edit-distance false match that
    # would erase the back signal and re-route to a bare-programme plan).
    "back", "cancel", "skip",
    # Intelligence-upgrade additions: abbreviations, schemes and synonyms
    "nep", "fyugp", "fygup", "curriculum", "curriculam", "semester",
    "semesters", "attendance", "sgpa", "cgpa", "grades", "marks",
    "papers", "modules", "programme", "programmes", "program",
    "programs", "enrollment", "enrolment",
    # Fee/eligibility phrasing words (never corrected into lookalike topics)
    "cost", "price", "charges", "charge", "payment", "expenses", "expense",
    "amount", "tuition", "worth", "concessions", "concession",
    "register", "registration", "enroll", "enrol", "enrollments",
    "score", "scored", "scoring", "percent", "percentage", "marksheet",
    "structure", "structures", "criteria", "requirement", "requirements",
    "policy", "policies", "education", "educational", "apply", "joining",
    "form", "forms", "last", "final", "open", "opened", "starts", "held",
    "date", "semester-wise", "semesterwise", "learn", "learned", "taught",
    # Service-keyword words (never fuzzy-corrected into lookalike topics —
    # "hall ticket status" must stay a service request)
    "hall", "ticket", "card", "status", "receipt", "certificate", "degree",
    "profile", "helpdesk", "support", "migration", "xerox", "photocopy",
    "copy", "backlogs", "semester", "examination",
    # Authority / office vocabulary — never fuzzy-corrected into service
    # keywords ("registrar" -> "register" would hijack student registration).
    "registrar", "registrars", "chancellor", "chancellors", "vice chancellor",
    "vc", "controller", "coe", "dean", "deans", "warden", "librarian",
    "officer", "officers", "authority", "authorities", "incharge",
    "in-charge", "secretary", "director", "handles", "handling", "deals",
    "dealing", "manages", "managing", "oversees", "supervises",
    # News / website-knowledge vocabulary — preserved verbatim so news
    # intent detection can match current-notice queries ("latest circular",
    # "examination notification", "academic calendar", "holiday notice").
    "notice", "notices", "notification", "notifications", "circular",
    "circulars", "calendar", "calendars", "announcement", "announcements",
    "news", "newsletter", "newsletters", "bulletin", "bulletins",
    "holiday", "holidays", "update", "updates", "published", "publish",
    # Granular information-retrieval vocabulary (never fuzzy-corrected away)
    "major", "majors", "minor", "minors", "credit", "credits", "scheme",
    "schemes", "vac", "sec", "aec", "vacancy", "vacancies", "semester",
    "semesters",
}

# Programme-level keywords that should not be corrected
PROGRAMME_KEYWORDS: set[str] = {
    "ba", "bsc", "bcom", "bba", "bca", "btech", "bed",
    "ma", "msc", "mcom", "mba", "mca", "med", "phd",
    "ba+b.ed", "ba+ma", "bsc+msc", "bca+mca", "bba+mba",
    "integrated", "ug", "pg", "dyd",
}


# Requirement words that are clearly not programme/department names (lowercase).
UNIVERSITY_STOP_WORDS: set[str] = {
    # Pronouns / determiners
    "a", "an", "the", "this", "that", "these", "those", "my", "your",
    "our", "their", "his", "her", "its", "it's", "i", "me", "we", "us",
    # Prepositions / conjunctions / adverbs
    "of", "for", "to", "in", "on", "at", "by", "with", "from", "about",
    "into", "onto", "over", "under", "through", "across", "between", "among",
    "after", "before", "since", "during", "and", "or", "but", "nor", "not",
    "so", "if", "then", "than", "also", "too", "very", "more", "most", "less",
    # Verbs / auxiliaries
    "is", "are", "was", "were", "am", "be", "been", "being", "do", "does",
    "did", "done", "have", "has", "had", "having", "can", "could", "would",
    "will", "shall", "should", "may", "might", "must", "need", "needs",
    "want", "wants", "know", "knows", "tell", "show", "give", "list",
    "explain", "describe", "find", "search", "look", "take", "make", "get",
    "got", "put", "use", "used", "see", "let", "like", "called", "regarding",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    # Greetings / fillers / other
    "hi", "hello", "hey", "please", "thanks", "thank", "ok", "okay",
    "yes", "no", "maybe", "sure", "again", "still", "even", "only", "just",
    "all", "any", "some", "many", "much", "few", "both", "each", "every",
    "kindly", "available", "information", "details", "detail", "info",
    "help", "helpful", "question",
    # Adjectives/quantifiers that must never be fuzzy-corrected into programme
    # names or topics ("main" -> "ma", "basic" -> "ba", "come" -> "bcom", ...)
    "main", "core", "basic", "full", "per", "such", "total", "various",
    "specific", "certain", "different", "important", "recent", "latest",
    "current", "first", "open", "last", "final",
    # Verbs that must never be corrected ("come" -> "bcom", "went" -> ...)
    "come", "came", "going", "go", "went", "does", "doing",
    # Course-discovery verbs: "offers/officers" is a 2-edit false match that
    # would break "which college offers BCA" course-college routing.
    "offer", "offers", "offered", "offering", "provides", "providing",
    "teaches", "teaching", "runs", "running",
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
    """Find the closest match in a dictionary using edit distance.

    Iterates the dictionary in sorted order so results are deterministic
    regardless of set iteration order / hash seed.
    """
    if word in dictionary:
        return word
    if len(word) <= 2:
        return None
    best = None
    best_dist = max_dist + 1
    for candidate in sorted(dictionary):
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
        if w in UNIVERSITY_STOP_WORDS:
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
