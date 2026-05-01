"""Step definitions for search_dedup.feature — verifies no duplicate paths in results."""

from pytest_bdd import given, then, when

from kairix.core.search.rrf import rrf

_state: dict = {}


@given("the mock retrieval backend is available")
def mock_backend_available():
    """No external services needed — we test the fusion layer directly."""


@when("I search with duplicated paths in the index")
def search_with_duplicated_paths():
    """Simulate a search where BM25 returns the same doc under two paths."""
    from kairix.core.search.bm25 import BM25Result

    bm25_results = [
        BM25Result(
            file="04-Agent-Knowledge/entities/person/alistair-black.md",
            title="Alistair Black",
            snippet="A person entity.",
            score=10.0,
            collection="default",
        ),
        BM25Result(
            file="obsidian-vault/04-Agent-Knowledge/entities/person/alistair-black.md",
            title="Alistair Black",
            snippet="A person entity.",
            score=9.5,
            collection="vault-agent-knowledge",
        ),
        BM25Result(
            file="01-Projects/some-project/README.md",
            title="Some Project",
            snippet="A different document.",
            score=8.0,
            collection="default",
        ),
    ]

    fused = rrf(bm25_results, [])
    _state["results"] = fused


@then("no two results share the same document path")
def no_duplicate_paths_check():
    results = _state["results"]
    paths = [r.path for r in results]
    assert len(paths) == len(set(paths)), f"Duplicate paths found: {[p for p in paths if paths.count(p) > 1]}"
