"""Contract test: emit.py output is loadable by loader.py.

Verifies that the JSON format written by emit_entity_stubs() can be
parsed and validated by the loader's build_node() and _build_edge().
This guards the integration boundary between reflib/emit.py and
reflib/loader.py.
"""

import json
from pathlib import Path

import pytest

from kairix.knowledge.reflib.emit import emit_entity_stubs
from kairix.knowledge.reflib.extract import RawRelationship, scan_reference_library
from kairix.knowledge.reflib.loader import build_node, load_entity_stubs
from kairix.knowledge.reflib.resolve import ResolvedEntity, resolve_entities

pytestmark = pytest.mark.contract

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "integration" / "reflib_fixture"


class TestEmitNodesParsedByLoader:
    """Nodes emitted by emit.py must be parseable by loader's build_node()."""

    @pytest.mark.contract
    def test_emitted_nodes_have_required_fields(self, tmp_path: Path) -> None:
        """Every emitted node has 'id', 'label', and 'name' fields."""
        entities = [
            ResolvedEntity(
                id="marcus-aurelius",
                canonical_name="Marcus Aurelius",
                entity_type="Person",
                description="Roman Emperor and Stoic philosopher",
                domains=["philosophy"],
                source_docs=["philosophy/meditations.md"],
                aliases=["Marcus Aurelius Antoninus"],
            ),
            ResolvedEntity(
                id="meditations",
                canonical_name="Meditations",
                entity_type="Publication",
                description="Stoic philosophical text",
                domains=["philosophy"],
                source_docs=["philosophy/meditations.md"],
            ),
        ]
        relationships: list[RawRelationship] = []

        nodes_path, edges_path = emit_entity_stubs(entities, relationships, tmp_path)  # noqa: RUF059

        nodes = json.loads(nodes_path.read_text())
        for node in nodes:
            assert "id" in node, f"Node missing 'id': {node}"
            assert "label" in node, f"Node missing 'label': {node}"
            assert "name" in node, f"Node missing 'name': {node}"

    @pytest.mark.contract
    def test_emitted_person_node_loadable(self, tmp_path: Path) -> None:
        """A Person node from emit can be built by loader's _build_node."""
        entities = [
            ResolvedEntity(
                id="marcus-aurelius",
                canonical_name="Marcus Aurelius",
                entity_type="Person",
                description="Roman Emperor",
                domains=["philosophy"],
                source_docs=["philosophy/meditations.md"],
            ),
        ]
        nodes_path, _ = emit_entity_stubs(entities, [], tmp_path)
        nodes = json.loads(nodes_path.read_text())

        assert len(nodes) == 1
        node_data = nodes[0]
        # _build_node should not raise for a valid Person node
        node = build_node(node_data["label"], node_data)
        assert node.id == "marcus-aurelius"

    @pytest.mark.contract
    def test_emitted_publication_node_loadable(self, tmp_path: Path) -> None:
        """A Publication node from emit can be built by loader's _build_node."""
        entities = [
            ResolvedEntity(
                id="meditations",
                canonical_name="Meditations",
                entity_type="Publication",
                description="Stoic text",
                domains=["philosophy"],
                source_docs=["philosophy/meditations.md"],
            ),
        ]
        nodes_path, _ = emit_entity_stubs(entities, [], tmp_path)
        nodes = json.loads(nodes_path.read_text())

        node_data = nodes[0]
        node = build_node(node_data["label"], node_data)
        assert node.id == "meditations"

    @pytest.mark.contract
    def test_emitted_concept_node_loadable(self, tmp_path: Path) -> None:
        """A Concept node from emit can be built by loader's _build_node."""
        entities = [
            ResolvedEntity(
                id="twelve-factor-app",
                canonical_name="Twelve-Factor App",
                entity_type="Concept",
                description="Methodology for building SaaS apps",
                domains=["software-engineering"],
                source_docs=["engineering/codebase.md"],
            ),
        ]
        nodes_path, _ = emit_entity_stubs(entities, [], tmp_path)
        nodes = json.loads(nodes_path.read_text())

        node_data = nodes[0]
        node = build_node(node_data["label"], node_data)
        assert node.id == "twelve-factor-app"


class TestEmitEdgesFormat:
    """Edges emitted by emit.py are tested for loader compatibility."""

    @pytest.mark.contract
    def test_emitted_edges_have_required_fields(self, tmp_path: Path) -> None:
        """Every emitted edge has from_id, to_id, and kind."""
        entities = [
            ResolvedEntity(
                id="marcus-aurelius",
                canonical_name="Marcus Aurelius",
                entity_type="Person",
                domains=["philosophy"],
                source_docs=["philosophy/meditations.md"],
            ),
            ResolvedEntity(
                id="meditations",
                canonical_name="Meditations",
                entity_type="Publication",
                domains=["philosophy"],
                source_docs=["philosophy/meditations.md"],
            ),
        ]
        relationships = [
            RawRelationship(
                from_name="Meditations",
                from_type="Publication",
                to_name="Marcus Aurelius",
                to_type="Person",
                kind="AUTHORED_BY",
                source_doc="philosophy/meditations.md",
            ),
        ]

        _, edges_path = emit_entity_stubs(entities, relationships, tmp_path)
        edges = json.loads(edges_path.read_text())

        assert len(edges) >= 1
        for edge in edges:
            assert "from_id" in edge, f"Edge missing 'from_id': {edge}"
            assert "to_id" in edge, f"Edge missing 'to_id': {edge}"
            assert "kind" in edge, f"Edge missing 'kind': {edge}"

    @pytest.mark.contract
    def test_emitted_edge_kind_is_valid(self, tmp_path: Path) -> None:
        """Edge kinds emitted are valid EdgeKind enum values."""
        from kairix.knowledge.graph.models import EdgeKind

        entities = [
            ResolvedEntity(
                id="marcus-aurelius",
                canonical_name="Marcus Aurelius",
                entity_type="Person",
                domains=["philosophy"],
                source_docs=["philosophy/meditations.md"],
            ),
            ResolvedEntity(
                id="meditations",
                canonical_name="Meditations",
                entity_type="Publication",
                domains=["philosophy"],
                source_docs=["philosophy/meditations.md"],
            ),
        ]
        relationships = [
            RawRelationship(
                from_name="Meditations",
                from_type="Publication",
                to_name="Marcus Aurelius",
                to_type="Person",
                kind="AUTHORED_BY",
                source_doc="philosophy/meditations.md",
            ),
        ]

        _, edges_path = emit_entity_stubs(entities, relationships, tmp_path)
        edges = json.loads(edges_path.read_text())

        valid_kinds = {e.value for e in EdgeKind}
        for edge in edges:
            assert edge["kind"] in valid_kinds, (
                f"Edge kind {edge['kind']!r} not in valid EdgeKind values: {valid_kinds}"
            )


class TestFullPipelineRoundtrip:
    """End-to-end: extract -> resolve -> emit -> dry_run load."""

    @pytest.mark.contract
    def test_fixture_entities_survive_roundtrip(self, tmp_path: Path) -> None:
        """Entities extracted from the fixture can be emitted and dry-run loaded."""
        if not FIXTURE_ROOT.exists():
            pytest.skip("Fixture not available")

        raw_entities, raw_relationships = scan_reference_library(FIXTURE_ROOT)
        resolved = resolve_entities(raw_entities)

        # Must have some entities from the fixture
        assert len(resolved) > 0, "No entities extracted from fixture"

        nodes_path, edges_path = emit_entity_stubs(resolved, raw_relationships, tmp_path)

        assert nodes_path.exists()
        assert edges_path.exists()

        # Dry-run load should parse without errors
        report = load_entity_stubs(nodes_path, edges_path, neo4j_client=None, dry_run=True)
        assert report.nodes_loaded > 0, f"No nodes loaded in dry run. Errors: {report.errors}"
