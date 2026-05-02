"""Tests for kairix.knowledge.reflib.loader — entity stub loading into Neo4j."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kairix.knowledge.reflib.loader import load_entity_stubs

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_NODES = [
    {
        "label": "Organisation",
        "id": "acme-corp",
        "name": "Acme Corp",
        "tier": "client",
        "engagement_status": "active",
        "industry": ["technology"],
    },
    {
        "label": "Person",
        "id": "jane-doe",
        "name": "Jane Doe",
        "org": "acme-corp",
        "role": "CTO",
    },
    {
        "label": "Concept",
        "id": "zero-trust",
        "name": "Zero Trust Architecture",
        "domain": "security",
    },
    {
        "label": "Technology",
        "id": "neo4j",
        "name": "Neo4j",
        "category": "database",
    },
    {
        "label": "Framework",
        "id": "togaf",
        "name": "TOGAF",
        "domain": "architecture",
    },
    {
        "label": "Publication",
        "id": "designing-data-intensive",
        "name": "Designing Data-Intensive Applications",
        "authors": ["Martin Kleppmann"],
        "year": "2017",
    },
    {
        "label": "Outcome",
        "id": "digital-health",
        "name": "Digital Health",
        "domain": "healthcare",
    },
]

SAMPLE_EDGES = [
    {
        "from_id": "jane-doe",
        "from_label": "Person",
        "to_id": "acme-corp",
        "to_label": "Organisation",
        "kind": "WORKS_AT",
    },
    {
        "from_id": "acme-corp",
        "from_label": "Organisation",
        "to_id": "zero-trust",
        "to_label": "Concept",
        "kind": "RELATED_TO",
        "props": {"context": "security initiative"},
    },
]


def _write_json(path: Path, data: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _make_mock_client(available: bool = True) -> MagicMock:
    """Build a mock Neo4jClient with upsert methods that return True."""
    client = MagicMock()
    client.available = available
    client._driver = MagicMock() if available else None
    client.upsert_organisation.return_value = True
    client.upsert_person.return_value = True
    client.upsert_outcome.return_value = True
    client.upsert_edge.return_value = True
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadFromFixtures:
    """Load from fixture JSON files with a mocked Neo4j client."""

    @pytest.mark.unit
    def test_loads_all_nodes_and_edges(self, tmp_path: Path):
        nodes_path = tmp_path / "entities" / "nodes.json"
        edges_path = tmp_path / "entities" / "edges.json"
        _write_json(nodes_path, SAMPLE_NODES)
        _write_json(edges_path, SAMPLE_EDGES)

        client = _make_mock_client()
        report = load_entity_stubs(nodes_path, edges_path, client)

        assert report.nodes_loaded == 7
        assert report.nodes_skipped == 0
        assert report.edges_loaded == 2
        assert report.edges_skipped == 0
        assert report.errors == []

        # Dedicated upsert methods called for Organisation, Person, Outcome
        client.upsert_organisation.assert_called_once()
        client.upsert_person.assert_called_once()
        client.upsert_outcome.assert_called_once()

        # Edges via upsert_edge
        assert client.upsert_edge.call_count == 2

    @pytest.mark.unit
    def test_generic_node_upsert_uses_upsert_node(self, tmp_path: Path):
        """Concept, Framework, Technology, Publication go through upsert_node."""
        nodes_path = tmp_path / "nodes.json"
        edges_path = tmp_path / "edges.json"
        # Only generic-label nodes
        generic_nodes = [n for n in SAMPLE_NODES if n["label"] in ("Concept", "Technology", "Framework", "Publication")]
        _write_json(nodes_path, generic_nodes)
        _write_json(edges_path, [])

        client = _make_mock_client()
        client.upsert_node.return_value = True

        report = load_entity_stubs(nodes_path, edges_path, client)

        assert report.nodes_loaded == 4
        assert report.nodes_skipped == 0
        # upsert_node should have been called for each generic node
        assert client.upsert_node.call_count == 4

    @pytest.mark.unit
    def test_skips_unknown_label(self, tmp_path: Path):
        nodes_path = tmp_path / "nodes.json"
        edges_path = tmp_path / "edges.json"
        _write_json(nodes_path, [{"label": "UnknownThing", "id": "x", "name": "X"}])
        _write_json(edges_path, [])

        client = _make_mock_client()
        report = load_entity_stubs(nodes_path, edges_path, client)

        assert report.nodes_loaded == 0
        assert report.nodes_skipped == 1
        assert len(report.errors) == 1
        assert "Unknown node label" in report.errors[0]

    @pytest.mark.unit
    def test_skips_unknown_edge_kind(self, tmp_path: Path):
        nodes_path = tmp_path / "nodes.json"
        edges_path = tmp_path / "edges.json"
        _write_json(nodes_path, [])
        _write_json(
            edges_path,
            [
                {
                    "from_id": "a",
                    "from_label": "Person",
                    "to_id": "b",
                    "to_label": "Organisation",
                    "kind": "INVENTED_REL",
                }
            ],
        )

        client = _make_mock_client()
        report = load_entity_stubs(nodes_path, edges_path, client)

        assert report.edges_loaded == 0
        assert report.edges_skipped == 1
        assert "Unknown edge kind" in report.errors[0]


@pytest.mark.unit
class TestDryRun:
    """Dry run produces a report without calling any Neo4j mutations."""

    @pytest.mark.unit
    def test_dry_run_no_mutations(self, tmp_path: Path):
        nodes_path = tmp_path / "nodes.json"
        edges_path = tmp_path / "edges.json"
        _write_json(nodes_path, SAMPLE_NODES)
        _write_json(edges_path, SAMPLE_EDGES)

        client = _make_mock_client()
        report = load_entity_stubs(nodes_path, edges_path, client, dry_run=True)

        assert report.nodes_loaded == 7
        assert report.edges_loaded == 2
        assert report.errors == []

        # No upsert methods should have been called
        client.upsert_organisation.assert_not_called()
        client.upsert_person.assert_not_called()
        client.upsert_outcome.assert_not_called()
        client.upsert_edge.assert_not_called()
        # Driver session should not have been used for generic nodes either
        client._driver.session.assert_not_called()


@pytest.mark.unit
class TestMissingFiles:
    """Graceful handling when entity files are missing."""

    @pytest.mark.unit
    def test_missing_nodes_file(self, tmp_path: Path):
        nodes_path = tmp_path / "missing_nodes.json"
        edges_path = tmp_path / "edges.json"
        _write_json(edges_path, SAMPLE_EDGES)

        client = _make_mock_client()
        report = load_entity_stubs(nodes_path, edges_path, client)

        assert report.nodes_loaded == 0
        assert report.edges_loaded == 2
        assert any("not found" in e for e in report.errors)

    @pytest.mark.unit
    def test_missing_edges_file(self, tmp_path: Path):
        nodes_path = tmp_path / "nodes.json"
        edges_path = tmp_path / "missing_edges.json"
        _write_json(nodes_path, SAMPLE_NODES)

        client = _make_mock_client()
        report = load_entity_stubs(nodes_path, edges_path, client)

        assert report.nodes_loaded == 7
        assert report.edges_loaded == 0
        assert any("not found" in e for e in report.errors)

    @pytest.mark.unit
    def test_both_files_missing(self, tmp_path: Path):
        nodes_path = tmp_path / "nope_nodes.json"
        edges_path = tmp_path / "nope_edges.json"

        client = _make_mock_client()
        report = load_entity_stubs(nodes_path, edges_path, client)

        assert report.nodes_loaded == 0
        assert report.edges_loaded == 0
        assert len(report.errors) == 2

    @pytest.mark.unit
    def test_neo4j_unavailable_returns_empty_report(self, tmp_path: Path):
        nodes_path = tmp_path / "nodes.json"
        edges_path = tmp_path / "edges.json"
        _write_json(nodes_path, SAMPLE_NODES)
        _write_json(edges_path, SAMPLE_EDGES)

        client = _make_mock_client(available=False)
        report = load_entity_stubs(nodes_path, edges_path, client)

        assert report.nodes_loaded == 0
        assert report.edges_loaded == 0
        assert "Neo4j unavailable" in report.errors

    @pytest.mark.integration
    def test_malformed_json(self, tmp_path: Path):
        nodes_path = tmp_path / "nodes.json"
        edges_path = tmp_path / "edges.json"
        nodes_path.write_text("not valid json {{{", encoding="utf-8")
        _write_json(edges_path, [])

        client = _make_mock_client()
        report = load_entity_stubs(nodes_path, edges_path, client)

        assert report.nodes_loaded == 0
        assert any("Failed to read" in e for e in report.errors)
