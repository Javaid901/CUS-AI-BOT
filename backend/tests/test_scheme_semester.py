"""
backend/tests/test_scheme_semester.py

Regression tests for the academic-scheme (NEP/CBCS) and semester-awareness
feature set shipped in the "scheme & semester" upgrade:

  * scheme detection + labels          (orchestrator.context)
  * semester entity extraction         (orchestrator.extractor)
  * semester parse helper              (orchestrator.engine._parse_semester)
  * metadata filter building           (ingest.retriever.build_metadata_filter)
  * loose vs strict where matching     (ingest.retriever._matches_where)

Run:  python tests/test_scheme_semester.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import engine helpers — importing app.models first avoids the known
# standalone circular-import quirk (AggregatedMetric from analytics.models).
import app.models  # noqa: F401

from app.orchestrator.context import detect_academic_scheme, scheme_label
from app.orchestrator.extractor import extract_entities
from app.orchestrator.engine import _parse_semester
from app.ingest.retriever import build_metadata_filter, _matches_where

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}{'  ' + detail if detail else ''}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def test_scheme_detection():
    print("-- Academic scheme detection --")
    cases = {
        "what is the NEP 2020 exam scheme": "nep2020",
        "nep 2020 syllabus pdf": "nep2020",
        "NEP-2020 examination regulation": "nep2020",
        "CBCS results": "cbcs",
        "under choice based credit system": "cbcs",
        "cbcs fee structure": "cbcs",
        "show semester results please": None,
        "hello": None,
    }
    for text, expected in cases.items():
        got = detect_academic_scheme(text)
        check(f"scheme '{text[:30]}' -> {got}", got == expected, f"expected {expected}")

    check("label nep2020", scheme_label("nep2020") == "NEP 2020", scheme_label("nep2020") or "")
    check("label cbcs", scheme_label("cbcs") == "CBCS", scheme_label("cbcs") or "")
    check("label unknown", scheme_label("xyz") is None or scheme_label("xyz") == "", str(scheme_label("xyz")))


def test_semester_extraction():
    print("-- Semester entity extraction --")
    cases = {
        "show my 4th semester results": 4,
        "sem 4 attendance": 4,
        "semeter 6 result": None,  # not a valid relative/digit form handled here
        "semester 6 fee receipt": 6,
        "fourth semester transcript": 4,
        "3rd semester exam form": 3,
        "results": None,
    }
    for text, expected in cases.items():
        ent = extract_entities(text)
        got = ent.semester
        check(f"extract sem '{text[:30]}' -> {got}", got == expected, f"expected {expected}")

    ent = extract_entities("show my 4th semester NEP results")
    check("extract scheme 'nep'", ent.scheme == "nep", str(ent.scheme))
    check("extract service 'results'", ent.service == "results", str(ent.service))


def test_semester_parse():
    print("-- _parse_semester helper --")
    cases = [
        ("sem 5", 4, True, 5),
        ("5th semester", 4, True, 5),
        ("fourth sem", 4, True, 4),
        ("next semester", 4, True, 5),
        ("current semester", 4, True, 4),
        ("previous semester", 4, True, 3),
        ("previous semester", 1, True, 1),  # clamp at 1
        ("semester 6", 4, True, 6),
        ("results", 4, True, None),
        ("1st sem", 1, True, 1),
    ]
    for text, default, word_ok, expected in cases:
        got = _parse_semester(text, default, word_ok=word_ok)
        check(f"parse '{text}' (d={default}) -> {got}", got == expected, f"expected {expected}")


def test_metadata_filter():
    print("-- build_metadata_filter --")
    f1 = build_metadata_filter({"academic_scheme": "nep2020", "programme": "bca", "semester": 4})
    check("multi-clause $and", f1 == {"$and": [
        {"academic_scheme": "nep2020"}, {"programme": "bca"}, {"semester": "4"},
    ]}, str(f1))

    f2 = build_metadata_filter({"programme": "bca"})
    check("single clause", f2 == {"programme": "bca"}, str(f2))

    f3 = build_metadata_filter({"level": "ug", "domain": "admissions"})
    check("unfilterable keys -> None", f3 is None, str(f3))

    f4 = build_metadata_filter({"semester": "not-a-number"})
    check("non-numeric semester ignored", f4 is None, str(f4))

    f5 = build_metadata_filter(None)
    check("empty context -> None", f5 is None, str(f5))


def test_where_matching():
    print("-- _matches_where (strict vs loose) --")
    where = {"$and": [{"academic_scheme": "cbcs"}, {"programme": "bca"}]}
    match_tagged = {"academic_scheme": "cbcs", "programme": "bca"}
    conflict_tagged = {"academic_scheme": "nep2020", "programme": "bca"}
    legacy = {}  # untagged legacy chunk

    check("strict: matching tagged", _matches_where(match_tagged, where), "")
    check("strict: conflicting tagged", not _matches_where(conflict_tagged, where), "")
    check("strict: legacy excluded", not _matches_where(legacy, where), "")
    check("loose: legacy rescued", _matches_where(legacy, where, loose=True), "")
    check("loose: conflict still excluded", not _matches_where(conflict_tagged, where, loose=True), "")
    check("where=None always true", _matches_where({}, None) and _matches_where(match_tagged, None), "")


def main():
    test_scheme_detection()
    test_semester_extraction()
    test_semester_parse()
    test_metadata_filter()
    test_where_matching()
    print()
    print("SUMMARY")
    print(f"  Passed: {len(PASS)}/{len(PASS) + len(FAIL)}")
    print(f"  Failed: {len(FAIL)}/{len(PASS) + len(FAIL)}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()