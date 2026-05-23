"""
Tests for kairix.core.search.intent — query intent classifier.

Labelled examples covering all six intent classes (TEMPORAL, MULTI_HOP, ENTITY,
PROCEDURAL, KEYWORD, SEMANTIC).
Priority order tested: TEMPORAL > MULTI_HOP > ENTITY > PROCEDURAL > KEYWORD > SEMANTIC.
"""

import pytest

from kairix.core.search.intent import QueryIntent, classify

# ---------------------------------------------------------------------------
# Labelled test cases
# ---------------------------------------------------------------------------

CASES: list[tuple[str, QueryIntent]] = [
    # --- TEMPORAL (6 cases) ---
    ("what was completed last week", QueryIntent.TEMPORAL),
    ("what changed in March", QueryIntent.TEMPORAL),
    ("when did we fix the embed lock crash", QueryIntent.TEMPORAL),
    ("what has been done recently", QueryIntent.TEMPORAL),
    ("show me items completed on 2026-03-22", QueryIntent.TEMPORAL),
    ("what happened over the last 30 days", QueryIntent.TEMPORAL),
    # --- MULTI_HOP (3 cases) ---
    ("how does the embed loop relate to the budget trim", QueryIntent.MULTI_HOP),
    ("explain the tradeoffs between BM25 and vector retrieval", QueryIntent.MULTI_HOP),
    ("what is the connection between intent classification and rerank cost", QueryIntent.MULTI_HOP),
    # --- ENTITY (4 cases) ---
    ("tell me about Jordan Blake", QueryIntent.ENTITY),
    ("what has Builder done", QueryIntent.ENTITY),
    ("what do we know about BuilderCo", QueryIntent.ENTITY),
    ("who is Jordan Blake", QueryIntent.ENTITY),
    # --- PROCEDURAL (4 cases) ---
    ("how to fetch a Key Vault secret", QueryIntent.PROCEDURAL),
    ("how do I handle a merge conflict", QueryIntent.PROCEDURAL),
    ("what's the rule for writing credentials", QueryIntent.PROCEDURAL),
    ("should I use trash instead of rm", QueryIntent.PROCEDURAL),
    # --- KEYWORD (3 cases) ---
    ("SQLiteVec Extension", QueryIntent.KEYWORD),
    ("SchemaVersionError", QueryIntent.KEYWORD),
    ("/data/workspaces/builder/MEMORY.md", QueryIntent.KEYWORD),
    # --- SEMANTIC (3 cases) ---
    ("why does hybrid search outperform pure vector", QueryIntent.SEMANTIC),
    ("explain the architecture of the kairix memory system", QueryIntent.SEMANTIC),
    ("what are the trade-offs between BM25 and vector search", QueryIntent.SEMANTIC),
    # --- ATTRIBUTE_FACT (Plan B-parity Cap #5) — short entity-attribute lookups ---
    ("what is acme's address?", QueryIntent.ATTRIBUTE_FACT),
    ("what's jordan's role?", QueryIntent.ATTRIBUTE_FACT),
    ("address of acme?", QueryIntent.ATTRIBUTE_FACT),
    ("role of Jordan?", QueryIntent.ATTRIBUTE_FACT),
    ("acme's address", QueryIntent.ATTRIBUTE_FACT),
]


@pytest.mark.unit
@pytest.mark.parametrize("query,expected", CASES)
def test_intent_labelled_examples(query: str, expected: QueryIntent) -> None:
    """Each labelled query must be classified to the expected intent."""
    result = classify(query)
    assert result == expected, f"Query: {query!r}\n  Expected: {expected.value}\n  Got:      {result.value}"


# ---------------------------------------------------------------------------
# Contract tests — boundary conditions and never-raise guarantee
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_classify_empty_string() -> None:
    """Empty string returns SEMANTIC (default)."""
    assert classify("") == QueryIntent.SEMANTIC


@pytest.mark.contract
def test_classify_whitespace_only() -> None:
    """Whitespace-only returns SEMANTIC."""
    assert classify("   ") == QueryIntent.SEMANTIC


@pytest.mark.contract
def test_classify_never_raises() -> None:
    """Classifier must never raise even for garbage input."""
    garbage_inputs = [
        None,  # type: ignore[arg-type]  # deliberately wrong type to exercise robustness contract
        12345,  # type: ignore[arg-type]  # deliberately wrong type to exercise robustness contract
        "!@#$%^&*()",
        "\x00\x01\x02",
        "a" * 10_000,
    ]
    for inp in garbage_inputs:
        result = classify(inp)  # type: ignore[arg-type]  # inp union includes non-str on purpose
        assert isinstance(result, QueryIntent)


@pytest.mark.unit
def test_classify_returns_query_intent_enum() -> None:
    """Return value is always a QueryIntent member."""
    for query, _ in CASES:
        result = classify(query)
        assert result in list(QueryIntent)


@pytest.mark.unit
def test_temporal_beats_entity() -> None:
    """TEMPORAL takes priority over ENTITY signals in the same query."""
    # "tell me about" is ENTITY but "recently" is TEMPORAL → TEMPORAL wins
    result = classify("tell me about what BuilderCo did recently")
    assert result == QueryIntent.TEMPORAL


@pytest.mark.unit
def test_temporal_beats_multi_hop() -> None:
    """TEMPORAL takes priority over MULTI_HOP signals in the same query.
    Per docstring priority: TEMPORAL > MULTI_HOP.
    """
    # "tradeoffs" is MULTI_HOP, "last week" is TEMPORAL → TEMPORAL wins.
    result = classify("what tradeoffs did we discuss last week")
    assert result == QueryIntent.TEMPORAL


@pytest.mark.unit
def test_multi_hop_beats_entity() -> None:
    """MULTI_HOP takes priority over ENTITY in the same query.
    Per docstring priority: MULTI_HOP > ENTITY.
    """
    # "tell me about" is ENTITY, "tradeoffs" is MULTI_HOP → MULTI_HOP wins.
    result = classify("tell me about the tradeoffs in our retrieval pipeline")
    assert result == QueryIntent.MULTI_HOP


@pytest.mark.unit
def test_entity_beats_procedural() -> None:
    """ENTITY takes priority over PROCEDURAL."""
    result = classify("what do we know about how to write rules")
    # "what do we know about" → ENTITY; "how to" → PROCEDURAL → ENTITY wins
    assert result == QueryIntent.ENTITY


@pytest.mark.unit
def test_procedural_beats_keyword() -> None:
    """PROCEDURAL takes priority over KEYWORD signals."""
    result = classify("how do I use SQLiteVec Extension")
    assert result == QueryIntent.PROCEDURAL


@pytest.mark.unit
def test_version_string_is_keyword() -> None:
    """Queries containing version strings are KEYWORD."""
    assert classify("kairix v1.1.2 changelog") == QueryIntent.KEYWORD


@pytest.mark.unit
def test_http_error_code_is_keyword() -> None:
    """HTTP 4xx/5xx codes are KEYWORD."""
    assert classify("why am I getting 429 errors") == QueryIntent.KEYWORD


@pytest.mark.unit
def test_allcaps_error_code_is_keyword() -> None:
    """ALLCAPS error codes are KEYWORD."""
    assert classify("AZURE-OPENAI-001 error diagnosis") == QueryIntent.KEYWORD


# ---------------------------------------------------------------------------
# ATTRIBUTE_FACT — Plan B-parity Capability #5 (federation in SearchPipeline)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_attribute_fact_what_is_x_y_pattern() -> None:
    """``what is X's Y?`` lookups classify as ATTRIBUTE_FACT.

    Sabotage-proof: remove the ``_ATTRIBUTE_FACT_PATTERNS`` block from
    intent.py (or delete the ``ATTRIBUTE_FACT`` branch from ``classify``)
    and this assertion flips to ENTITY (the next candidate in priority
    order matches "what is .... role" via the ENTITY pattern).
    """
    assert classify("what is acme's address?") == QueryIntent.ATTRIBUTE_FACT


@pytest.mark.unit
def test_attribute_fact_y_of_x_pattern() -> None:
    """``Y of X?`` short attribute lookups classify as ATTRIBUTE_FACT.

    Sabotage-proof: drop the second pattern (the ``\\bof\\b``-anchored
    one) and this query falls all the way to SEMANTIC. The check below
    pins both that the new intent fires AND that the shorter "of"
    variant isn't being misrouted to KEYWORD via the Title-Case heuristic.
    """
    assert classify("address of Acme?") == QueryIntent.ATTRIBUTE_FACT


@pytest.mark.unit
def test_attribute_fact_possessive_only_pattern() -> None:
    """Bare ``X's Y`` possessive without a leading question word fires."""
    assert classify("acme's address") == QueryIntent.ATTRIBUTE_FACT


@pytest.mark.unit
def test_attribute_fact_does_not_override_temporal() -> None:
    """TEMPORAL takes priority over ATTRIBUTE_FACT in the same query.

    Per the documented order: TEMPORAL > MULTI_HOP > ATTRIBUTE_FACT.
    """
    # "last week" is TEMPORAL, "X's Y" looks ATTRIBUTE_FACT → TEMPORAL wins.
    assert classify("what was acme's address last week") == QueryIntent.TEMPORAL


@pytest.mark.unit
def test_attribute_fact_does_not_override_multi_hop() -> None:
    """MULTI_HOP takes priority over ATTRIBUTE_FACT in the same query."""
    # "relates to" is MULTI_HOP → MULTI_HOP wins despite the possessive.
    assert classify("how does acme's address relate to billing") == QueryIntent.MULTI_HOP


@pytest.mark.unit
def test_attribute_fact_long_narrative_query_falls_to_other_intents() -> None:
    """Long narrative questions don't slip into ATTRIBUTE_FACT.

    A multi-clause narrative containing a possessive should not be
    captured by the tight "what is X's Y?" head pattern.
    Sabotage-proof: relax the head pattern to match anywhere in the
    string (drop the ``^`` / "what is" anchor) and long narratives like
    the one below start misclassifying.
    """
    # No tight possessive head, "tell me about" is ENTITY → ENTITY.
    result = classify("tell me about Acme Corp and their history")
    assert result == QueryIntent.ENTITY


# ---------------------------------------------------------------------------
# ATTRIBUTE_FACT — Plan B-parity D2 remediation (broader patterns)
# ---------------------------------------------------------------------------
#
# Empirical motivation: the 2026-05-21 LoCoMo benchmark showed 5% pass
# rate (below the 11% pre-Plan-B baseline). Categorising the 60
# responses by intent: questions of the shape "What did X verb?" and
# "Who is/was X's Y?" were routing to SEMANTIC (low fact weight in
# fusion), so even when the right fact was retrieved it ranked below
# unrelated chunks. These patterns broaden the ATTRIBUTE_FACT surface
# without bleeding into TEMPORAL (the "do last/this/in" suffix still
# protects the temporal queries) or MULTI_HOP.


@pytest.mark.unit
@pytest.mark.parametrize(
    "query",
    [
        "what did agent-alpha research?",
        "what did Jordan publish?",
        "what did Acme acquire?",
    ],
)
def test_attribute_fact_what_did_x_verb_pattern(query: str) -> None:
    """``What did X verb?`` single-verb factoid lookups classify as ATTRIBUTE_FACT.

    Sabotage-proof: deleted the 4th pattern in ``_ATTRIBUTE_FACT_PATTERNS``
    (the ``what\\s+did\\s+\\S+\\s+\\w+`` one) → all three queries fall to
    SEMANTIC. Restored.
    """
    assert classify(query) == QueryIntent.ATTRIBUTE_FACT


@pytest.mark.unit
@pytest.mark.parametrize(
    "query",
    [
        "who is agent-alpha's friend?",
        "who was Jordan's mentor?",
        "who is Acme's CEO?",
    ],
)
def test_attribute_fact_who_is_x_possessive_pattern(query: str) -> None:
    """``Who is/was X's Y?`` possessive identity lookups classify as ATTRIBUTE_FACT.

    Sabotage-proof: deleted the 5th pattern (the ``who(?:'s|\\s+is|\\s+was|
    \\s+are|\\s+were)\\s+\\S+'s\\s+\\w+`` one) → all three queries fall to
    ENTITY via the ``\\bwho\\s+is\\b`` pattern (which would lose the
    fact-dominant fusion weighting they need). Restored.
    """
    assert classify(query) == QueryIntent.ATTRIBUTE_FACT


@pytest.mark.unit
def test_attribute_fact_what_did_x_do_temporal_still_wins() -> None:
    """The existing TEMPORAL ``what did X do last/this/in`` pattern keeps priority.

    Pins that the broader ``what did X verb`` pattern doesn't bleed into
    the temporal surface — questions with explicit time suffixes still
    route to TEMPORAL (so date-aware retrieval + timeline rewriting
    fire correctly). Sabotage-proof: dropped the explicit anchor on
    the new pattern (so it matches any "what did X <one-word>" prefix)
    → this temporal query would route ATTRIBUTE_FACT first, but the
    ``what did X do ... last/this/in`` TEMPORAL pattern fires earlier
    in priority order so the test continues to pass — confirming the
    priority guard works as documented.
    """
    assert classify("what did agent-alpha do last week?") == QueryIntent.TEMPORAL


@pytest.mark.unit
def test_attribute_fact_what_did_x_multi_clause_falls_through() -> None:
    """Multi-clause "what did X verb [and ...]" doesn't slip into ATTRIBUTE_FACT.

    The 4th pattern is anchored at start AND end (``$``), so a multi-clause
    narrative continues past the question word and falls through to
    SEMANTIC (or another intent that matches the trailing clause).
    """
    # No tight ATTRIBUTE_FACT shape (extra trailing clause); no
    # TEMPORAL/MULTI_HOP/ENTITY/PROCEDURAL signal → SEMANTIC.
    result = classify("what did agent-alpha research about ancient civilisations")
    assert result == QueryIntent.SEMANTIC
