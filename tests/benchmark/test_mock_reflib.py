"""
Tests for kairix.quality.benchmark.mock_reflib_retrieval — verify the mock-reflib backend
returns results for known queries and matches expected fixture documents.
"""

from __future__ import annotations

import pytest

from kairix.quality.benchmark.mock_reflib_retrieval import (
    FIXTURE_DOCUMENTS,
    mock_reflib_retrieve,
)


@pytest.mark.unit
class TestMockReflibFixtures:
    """Verify the fixture corpus is well-formed."""

    def test_fixture_count(self) -> None:
        assert len(FIXTURE_DOCUMENTS) == 30

    def test_all_fixtures_have_required_fields(self) -> None:
        for doc in FIXTURE_DOCUMENTS:
            assert "path" in doc, f"Missing path in {doc.get('title', '?')}"
            assert "title" in doc, f"Missing title in {doc.get('path', '?')}"
            assert "body" in doc, f"Missing body in {doc.get('path', '?')}"
            assert "keywords" in doc, f"Missing keywords in {doc.get('path', '?')}"
            assert isinstance(doc["keywords"], set), f"keywords must be a set in {doc['title']}"
            assert len(doc["keywords"]) >= 5, f"Too few keywords in {doc['title']}"

    def test_body_length_in_range(self) -> None:
        for doc in FIXTURE_DOCUMENTS:
            word_count = len(doc["body"].split())
            assert 50 <= word_count <= 250, f"{doc['title']} body has {word_count} words, expected 50-250"

    def test_unique_paths(self) -> None:
        paths = [doc["path"] for doc in FIXTURE_DOCUMENTS]
        assert len(paths) == len(set(paths)), "Duplicate paths in fixture corpus"

    def test_unique_titles(self) -> None:
        titles = [doc["title"] for doc in FIXTURE_DOCUMENTS]
        assert len(titles) == len(set(titles)), "Duplicate titles in fixture corpus"


@pytest.mark.unit
class TestMockReflibRetrieval:
    """Verify retrieval returns expected documents for known queries."""

    def test_returns_results_for_agent_query(self) -> None:
        paths, _snippets, meta = mock_reflib_retrieve("agent loop pattern observe act")
        assert len(paths) > 0
        assert "reflib/agentic-ai/agent-loop-patterns.md" in paths
        assert meta["system"] == "mock-reflib"

    def test_returns_results_for_distributed_systems(self) -> None:
        paths, _snippets, _meta = mock_reflib_retrieve("distributed systems cap theorem consistency")
        assert "reflib/engineering/distributed-systems-fundamentals.md" in paths[:3]

    def test_returns_results_for_epistemology(self) -> None:
        paths, _snippets, _meta = mock_reflib_retrieve("epistemology knowledge belief justification")
        assert "reflib/philosophy/epistemology-and-knowledge.md" in paths[:3]

    def test_returns_results_for_security(self) -> None:
        paths, _snippets, _meta = mock_reflib_retrieve("zero trust architecture security authentication")
        assert "reflib/security/zero-trust-architecture.md" in paths[:3]

    def test_returns_results_for_team_topologies(self) -> None:
        paths, _snippets, _meta = mock_reflib_retrieve("team topologies stream-aligned platform enabling conway")
        assert "reflib/operating-models/team-topologies.md" in paths[:3]

    def test_returns_empty_for_unrelated_query(self) -> None:
        paths, _snippets, meta = mock_reflib_retrieve("xyzzy plugh nothing")
        assert len(paths) == 0
        assert meta["n_matched"] == 0

    def test_respects_limit(self) -> None:
        paths, _, _ = mock_reflib_retrieve("agent design system engineering", limit=3)
        assert len(paths) <= 3

    def test_snippets_match_paths(self) -> None:
        paths, snippets, _ = mock_reflib_retrieve("testing strategy unit integration")
        assert len(paths) == len(snippets)
        for snippet in snippets:
            assert len(snippet) > 0

    def test_top_result_is_best_match(self) -> None:
        """The document with the most keyword overlap should rank first."""
        paths, _, _ = mock_reflib_retrieve("secrets management vault key credential rotation api security")
        assert paths[0] == "reflib/security/secrets-management.md"
