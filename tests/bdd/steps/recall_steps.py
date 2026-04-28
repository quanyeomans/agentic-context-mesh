"""Step definitions for recall_check.feature.

Tests the adaptive recall quality gate. Uses in-memory SQLite databases
to simulate indexed document state. No external API calls.
"""

import sqlite3

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.embed.recall_check import (
    DEFAULT_RECALL_QUERIES,
    DEGRADATION_THRESHOLD,
    _build_adaptive_queries,
    _get_recall_queries,
)

pytestmark = pytest.mark.bdd

_state: dict = {}


# ---------------------------------------------------------------------------
# Scenario: Adaptive queries are generated from indexed documents
# ---------------------------------------------------------------------------


@given("an index with titled documents")
def index_with_titled_documents():
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (
            path TEXT, title TEXT, active INTEGER
        );
        INSERT INTO documents VALUES ('docs/architecture.md', 'architecture', 1);
        INSERT INTO documents VALUES ('docs/deploy-guide.md', 'deploy-guide', 1);
        INSERT INTO documents VALUES ('docs/testing.md', 'testing', 1);
        INSERT INTO documents VALUES ('docs/onboarding.md', 'onboarding', 1);
    """)
    db.commit()
    _state["db"] = db


@when("the recall check builds adaptive queries")
def build_adaptive_queries():
    db = _state["db"]
    _state["queries"] = _build_adaptive_queries(db)


@then("at least 3 recall queries are generated")
def at_least_3_queries():
    assert len(_state["queries"]) >= 3


@then("each query has an id, query text, and expected fragment")
def each_query_has_fields():
    for qid, query, fragment in _state["queries"]:
        assert isinstance(qid, str) and len(qid) > 0
        assert isinstance(query, str) and len(query) > 0
        assert isinstance(fragment, str) and len(fragment) > 0


# ---------------------------------------------------------------------------
# Scenario: Default recall queries are used when no documents exist
# ---------------------------------------------------------------------------


@given("an empty search index")
def empty_search_index():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE documents (path TEXT, title TEXT, active INTEGER)")
    db.commit()
    _state["db"] = db


@when("the recall check builds queries")
def build_queries(monkeypatch):
    # Ensure RECALL_QUERIES env var is not set so we fall through to adaptive/default
    monkeypatch.delenv("RECALL_QUERIES", raising=False)
    db = _state["db"]
    _state["queries"] = _get_recall_queries(db)


@then("the default recall queries are used")
def default_queries_used():
    assert _state["queries"] == DEFAULT_RECALL_QUERIES


@then("at least 5 queries are returned")
def at_least_5_queries():
    assert len(_state["queries"]) >= 5


# ---------------------------------------------------------------------------
# Scenario: Degradation threshold triggers alert
# ---------------------------------------------------------------------------


@given(parsers.parse("a previous recall score of {score:f}"))
def previous_recall_score(score):
    _state["previous_score"] = score


@given(parsers.parse("a current recall score of {score:f}"))
def current_recall_score(score):
    _state["current_score"] = score


@when("the recall gate compares scores")
def recall_gate_compares():
    delta = _state["current_score"] - _state["previous_score"]
    _state["degradation_detected"] = delta < -DEGRADATION_THRESHOLD


@then("degradation is detected")
def degradation_detected():
    assert _state["degradation_detected"], (
        f"Expected degradation but delta was within threshold "
        f"(previous={_state['previous_score']}, current={_state['current_score']}, "
        f"threshold={DEGRADATION_THRESHOLD})"
    )
