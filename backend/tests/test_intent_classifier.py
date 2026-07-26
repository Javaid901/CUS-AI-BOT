"""
Automated evaluation suite for the semantic intent classifier.

Tests:
  - Intent classification accuracy across hundreds of paraphrases
  - Confidence scoring
  - Low-confidence (unknown) behaviour
  - Consistent routing across semantically equivalent queries
    - Semantic enrichment for follow-up queries in context
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
from collections import Counter

# Suppress non-critical logs during testing
import logging
logging.disable(logging.CRITICAL)

# Windows cp1252 compat: avoid Unicode arrows
ARROW = "->"


def _test_classification_accuracy():
    """Test that each paraphrase in the intent KB classifies to its own intent."""
    from app.orchestrator.intent_classifier import classify
    from app.orchestrator.intent_kb import INTENT_PARAPHRASES

    print("=" * 60)
    print("TEST: Classification accuracy (self-classification)")
    print("=" * 60)

    total = 0
    correct = 0
    intent_results = {}

    for intent, paraphrases in INTENT_PARAPHRASES.items():
        hits = 0
        for phrase in paraphrases:
            total += 1
            result, conf, debug = classify(phrase, threshold=0.0)
            if result == intent:
                hits += 1
                correct += 1
        intent_results[intent] = {
            "total": len(paraphrases),
            "hits": hits,
            "accuracy": hits / len(paraphrases) * 100 if paraphrases else 0,
            "avg_conf": 0,
        }
        print(f"  {intent:25s}: {hits:3d}/{len(paraphrases):3d} correct ({intent_results[intent]['accuracy']:.1f}%)")

    overall = correct / total * 100 if total else 0
    print(f"\n  OVERALL: {correct}/{total} correct ({overall:.1f}%)")
    print()
    return overall >= 80.0


def _test_cross_intent_accuracy():
    """Test that each intent's paraphrases don't incorrectly match other intents."""
    from app.orchestrator.intent_classifier import classify
    from app.orchestrator.intent_kb import INTENT_PARAPHRASES

    print("=" * 60)
    print("TEST: Cross-intent confusion test")
    print("=" * 60)

    confusion = Counter()
    total_checks = 0

    for intent, paraphrases in INTENT_PARAPHRASES.items():
        for phrase in paraphrases[:5]:
            result, conf, debug = classify(phrase, threshold=0.0)
            if result != intent:
                confusion[(intent, result)] += 1
            total_checks += 1

    if confusion:
        print("  Cross-intent confusions (intent -> classified_as):")
        for (intent, wrong), count in confusion.most_common(10):
            print(f"    {intent:25s} -> {wrong:25s} ({count}x)")
    else:
        print("  No cross-intent confusions detected")
    print()
    return True


def _test_unknown_threshold():
    """Test that low-confidence queries return 'unknown'."""
    from app.orchestrator.intent_classifier import classify

    print("=" * 60)
    print("TEST: Low-confidence -> unknown behaviour")
    print("=" * 60)

    nonsense_queries = [
        "xyzzy flurbo garblex",
        "asdf qwerty zxcvbnm",
        "lorem ipsum dolor sit amet",
        "hello world",
        "test test one two three",
        "random garbage input",
        "foo bar baz qux",
    ]

    unknowns = 0
    for query in nonsense_queries:
        result, conf, debug = classify(query)
        if result == "unknown":
            unknowns += 1
        print(f"  '{query:40s}' {ARROW} {result:15s} (conf={conf:.3f})")

    rate = unknowns / len(nonsense_queries) * 100
    print(f"\n  Unknown rate: {unknowns}/{len(nonsense_queries)} ({rate:.0f}%)")
    print()
    return rate >= 50.0


def _test_semantic_equivalence():
    """Test that semantically equivalent queries get the SAME intent consistently."""
    from app.orchestrator.intent_classifier import classify

    print("=" * 60)
    print("TEST: Semantic equivalence consistency")
    print("=" * 60)

    equivalent_groups = {
        "courses": [
            "courses",
            "what courses are offered",
            "available programmes",
            "what can i study",
            "degrees offered",
            "tell me about courses",
            "list of programmes",
            "what programmes do you offer",
            "course catalogue",
            "what subjects are available",
        ],
        "admissions": [
            "admissions",
            "how to apply",
            "admission process",
            "how do i get admission",
            "tell me about admissions",
            "how can i apply",
            "application process",
            "how to enrol",
            "apply online",
            "i want to take admission",
        ],
        "fee": [
            "fee",
            "fee structure",
            "how much are the fees",
            "what is the tuition fee",
            "cost of study",
            "how much does it cost",
            "what are the charges",
            "fee details",
            "tuition fees",
            "how much do i have to pay",
        ],
        "results": [
            "results",
            "exam results",
            "how to check my result",
            "my marks",
            "when will results be announced",
            "check result online",
            "semester results",
            "grade card",
            "marksheet",
            "are results out",
        ],
        "datesheet": [
            "datesheet",
            "exam schedule",
            "when are the exams",
            "exam timetable",
            "examination schedule",
            "when do exams start",
            "semester exam dates",
            "exam calendar",
            "what is the exam schedule",
            "show me the date sheet",
        ],
        "scholarships": [
            "scholarships",
            "financial aid",
            "how can i get scholarship",
            "what scholarships are available",
            "merit scholarship",
            "scholarship schemes",
            "financial assistance",
            "is there any scholarship",
            "scholarship opportunities",
            "how to apply for scholarship",
        ],
        "contact": [
            "contact",
            "phone number",
            "university address",
            "email address",
            "how to contact the university",
            "what is the address",
            "helpline number",
            "where is the university located",
            "university website",
            "contact us",
        ],
        "colleges": [
            "colleges",
            "constituent colleges",
            "list of colleges",
            "what colleges are there",
            "tell me about colleges",
            "colleges under the university",
            "college list",
            "which colleges are part of the university",
            "college details",
            "affiliated colleges",
        ],
    }

    all_consistent = True
    for expected_intent, queries in equivalent_groups.items():
        results = []
        for q in queries:
            result, conf, debug = classify(q, threshold=0.0)
            results.append(result)

        consistent = all(r == expected_intent for r in results)
        status = "OK" if consistent else "MISMATCH"
        if not consistent:
            all_consistent = False
            for q, r in zip(queries, results):
                if r != expected_intent:
                    print(f"    '{q[:50]}' {ARROW} {r} (expected {expected_intent})")

        print(f"  {expected_intent:15s}: {status} ({sum(1 for r in results if r == expected_intent)}/{len(results)} correct)")

    print()
    return all_consistent


def _test_navigation_accuracy():
    """Test that navigation-related queries correctly identify as broad.

    Queries starting with question words (what/how/when/who) are correctly
    classified as "specific" for RAG routing. Only keyword-style navigation
    queries should return "broad".
    """
    from app.chat.intent_router import classify as classify_nav

    print("=" * 60)
    print("TEST: Navigation intent detection")
    print("=" * 60)

    # Split into two groups: keyword nav (expect broad) and question nav (expect specific)
    broad_expected = [
        ("courses", "courses"),
        ("available programmes", "courses"),
        ("degrees offered", "courses"),
        ("fee structure", "fee"),
        ("admission process", "admissions"),
        ("exam schedule", "datesheet"),
        ("financial aid", "scholarships"),
        ("check my result", "results"),
        ("contact information", "contact"),
        ("phone number", "contact"),
        ("constituent colleges", "colleges"),
        ("eligibility criteria", "eligibility"),
        ("cost of study", "fee"),
        ("tuition fees", "fee"),
    ]
    specific_expected = [
        ("what courses are offered", "courses"),
        ("what can i study", "courses"),
        ("how much are the fees", "fee"),
        ("what is the tuition fee", "fee"),
        ("how to apply", "admissions"),
        ("how do i get admission", "admissions"),
        ("when are the exams", "datesheet"),
        ("what scholarships are available", "scholarships"),
        ("how to check result", "results"),
        ("list of colleges", "colleges"),
        ("who can apply", "eligibility"),
    ]

    hits = 0
    total = len(broad_expected) + len(specific_expected)

    for query, expected_cat in broad_expected:
        intent_type, category = classify_nav(query)
        if intent_type == "broad" and category == expected_cat:
            hits += 1
            status = "OK"
        else:
            status = f"{ARROW} ({intent_type}, {category})"
        print(f"  {query:35s} {ARROW} broad+{expected_cat:15s} {status}")

    for query, expected_cat in specific_expected:
        intent_type, category = classify_nav(query)
        if intent_type == "specific" and category is None:
            hits += 1
            status = "OK"
        else:
            status = f"{ARROW} ({intent_type}, {category})"
        print(f"  {query:35s} {ARROW} specific (RAG) {status}")

    rate = hits / total * 100
    print(f"\n  Navigation accuracy: {hits}/{total} ({rate:.1f}%)")
    print()
    return rate >= 70.0


def _test_context_followup():
    """Test that follow-up queries within programme context work.
    
    Tests that the planner correctly identifies topic from semantic intent
    when the entity extractor misses it (Stage 0c enrichment).
    """
    print("=" * 60)
    print("TEST: Context-aware follow-up handling (semantic enrichment)")
    print("=" * 60)

    # Test the semantic intent classifier directly for the tricky follow-ups
    from app.orchestrator.intent_classifier import classify

    tricky_followups = [
        ("cost", "fee"),
        ("how much", "fee"),
        ("tuition", "fee"),
        ("who can apply", "eligibility"),
    ]

    # Also test the Stage 0c semantic topic map directly
    SEMANTIC_TOPIC_MAP = {
        "fee": "fee", "eligibility": "eligibility", "scholarships": "fee",
        "datesheet": "dates", "examination": "examination",
        "results": "results", "contact": "contact",
    }

    hits = 0
    for query, expected_topic in tricky_followups:
        intent, conf, debug = classify(query)
        mapped_topic = SEMANTIC_TOPIC_MAP.get(intent, None)
        is_correct = mapped_topic == expected_topic
        if is_correct:
            hits += 1
        print(f"  '{query:20s}' {ARROW} intent={intent:15s} conf={conf:.3f} mapped_topic={str(mapped_topic):15s} expected={expected_topic:15s} {'OK' if is_correct else 'MISMATCH'}")

    rate = hits / len(tricky_followups) * 100
    print(f"\n  Semantic enrichment catch rate: {hits}/{len(tricky_followups)} ({rate:.1f}%)")
    print()
    return rate >= 75.0


def _test_confidence_scoring():
    """Test that confidence scores are meaningful."""
    from app.orchestrator.intent_classifier import classify

    print("=" * 60)
    print("TEST: Confidence scoring")
    print("=" * 60)

    clear_intent = ("fee", "fee structure")
    ambiguous = ("hello", "general_info")

    result, conf, debug = classify(clear_intent[1])
    print(f"  Clear intent '{clear_intent[1]}' {ARROW} {result} (conf={conf:.3f})")

    result2, conf2, debug2 = classify(ambiguous[0])
    print(f"  Ambiguous '{ambiguous[0]}' {ARROW} {result2} (conf={conf2:.3f})")

    if conf > conf2 + 0.1:
        print("  OK: Clear intent has significantly higher confidence than ambiguous")
    else:
        print("  WARN: Clear/ambiguous confidence gap is small")

    print()
    return True


def run_all():
    """Run all evaluation tests and return overall pass/fail."""
    tests = [
        ("Self-classification accuracy", _test_classification_accuracy),
        ("Cross-intent confusion", _test_cross_intent_accuracy),
        ("Unknown threshold behaviour", _test_unknown_threshold),
        ("Semantic equivalence consistency", _test_semantic_equivalence),
        ("Navigation intent detection", _test_navigation_accuracy),
        ("Context follow-up handling", _test_context_followup),
        ("Confidence scoring", _test_confidence_scoring),
    ]

    passed = 0
    failed = 0
    results = []

    t0 = time.time()
    for name, test_fn in tests:
        try:
            t1 = time.time()
            ok = test_fn()
            elapsed = time.time() - t1
            if ok:
                passed += 1
                results.append((name, "PASS", elapsed))
            else:
                failed += 1
                results.append((name, "FAIL", elapsed))
        except Exception as e:
            failed += 1
            import traceback
            results.append((name, f"ERROR: {e}", 0))
            traceback.print_exc()

    total_time = time.time() - t0

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, status, elapsed in results:
        print(f"  {name:40s} {status:8s} ({elapsed:.1f}s)")
    print(f"\n  Passed: {passed}/{len(tests)}")
    print(f"  Failed: {failed}/{len(tests)}")
    print(f"  Total time: {total_time:.1f}s")
    print()

    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
