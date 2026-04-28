"""Step definitions for eval_auto_gold.feature."""

import sqlite3

from pytest_bdd import given, parsers, then, when

from kairix.eval.auto_gold import analyse_corpus, generate_template_queries

# Module-level state (simple, test-scoped)
_state: dict = {}

# Schema matching what analyse_corpus expects
_CREATE_TABLE = """
CREATE TABLE documents (
    path TEXT NOT NULL,
    title TEXT,
    collection TEXT DEFAULT 'default',
    active INTEGER DEFAULT 1
)
"""


def _make_db(rows: list[tuple[str, str, str]]) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with a documents table and insert rows.

    Each row is (path, title, collection).
    """
    db = sqlite3.connect(":memory:")
    db.execute(_CREATE_TABLE)
    db.executemany(
        "INSERT INTO documents (path, title, collection) VALUES (?, ?, ?)",
        rows,
    )
    db.commit()
    return db


@given("an indexed corpus with procedural and date-named documents")
def indexed_corpus_with_types() -> None:
    rows = [
        ("how-to-deploy-app", "how-to-deploy-app", "guides"),
        ("runbook-incident-response", "runbook-incident-response", "guides"),
        ("how-to-configure-logging", "how-to-configure-logging", "guides"),
        ("2026-04-28-release-notes", "2026-04-28-release-notes", "notes"),
        ("2026-04-20-standup", "2026-04-20-standup", "notes"),
        ("architecture-overview", "architecture-overview", "design"),
        ("api-reference", "api-reference", "design"),
        ("onboarding-checklist", "onboarding-checklist", "hr"),
        ("team-directory", "team-directory", "hr"),
        ("project-roadmap", "project-roadmap", "planning"),
    ]
    _state["db"] = _make_db(rows)
    _state["expected_count"] = len(rows)


@given("an empty indexed corpus")
def empty_corpus() -> None:
    _state["db"] = _make_db([])
    _state["expected_count"] = 0


@when("the operator analyses the corpus")
def analyse() -> None:
    _state["profile"] = analyse_corpus(_state["db"])


@when(parsers.parse("the operator generates template queries with count {n:d}"))
def generate_queries(n: int) -> None:
    profile = analyse_corpus(_state["db"])
    _state["profile"] = profile
    _state["queries"] = generate_template_queries(profile, n)


@then("the profile total_docs matches the indexed count")
def check_total_docs_match() -> None:
    assert _state["profile"].total_docs == _state["expected_count"]


@then("the profile procedural_count is greater than zero")
def check_procedural_positive() -> None:
    assert _state["profile"].procedural_count > 0


@then("the profile date_filename_count is greater than zero")
def check_date_filename_positive() -> None:
    assert _state["profile"].date_filename_count > 0


@then(parsers.parse("the profile total_docs is {n:d}"))
def check_total_docs_exact(n: int) -> None:
    assert _state["profile"].total_docs == n


@then(parsers.parse('at least one query has category "{category}"'))
def check_category_present(category: str) -> None:
    categories = [q["category"] for q in _state["queries"]]
    assert category in categories, f"Expected category {category!r} in {set(categories)}"


@then(parsers.parse("the total query count is {n:d}"))
def check_query_count(n: int) -> None:
    assert len(_state["queries"]) == n, f"Expected {n} queries, got {len(_state['queries'])}"
