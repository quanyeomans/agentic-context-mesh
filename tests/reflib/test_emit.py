"""Unit tests for kairix.reflib.emit — entity stub JSON emission."""

from __future__ import annotations

import json

import pytest

from kairix.reflib.emit import emit_entity_stubs
from kairix.reflib.extract import RawRelationship
from kairix.reflib.resolve import ResolvedEntity


def _make_entities() -> list[ResolvedEntity]:
    return [
        ResolvedEntity(
            id="marcus-aurelius",
            canonical_name="Marcus Aurelius",
            entity_type="Person",
            description="Roman emperor and Stoic philosopher",
            domains=["philosophy"],
            source_docs=["meditations.md"],
            aliases=["Aurelius"],
        ),
        ResolvedEntity(
            id="stoicism",
            canonical_name="Stoicism",
            entity_type="Concept",
            description="Hellenistic philosophy",
            domains=["philosophy"],
            source_docs=["stoicism-overview.md"],
            aliases=["Stoic philosophy"],
        ),
        ResolvedEntity(
            id="kubernetes",
            canonical_name="Kubernetes",
            entity_type="Technology",
            description="Container orchestration platform",
            domains=["engineering"],
            source_docs=["k8s-guide.md"],
            aliases=["k8s"],
        ),
    ]


def _make_relationships() -> list[RawRelationship]:
    return [
        RawRelationship(
            from_name="Marcus Aurelius",
            from_type="Person",
            to_name="Stoicism",
            to_type="Concept",
            kind="PRACTISED",
            source_doc="meditations.md",
        ),
        RawRelationship(
            from_name="Kubernetes",
            from_type="Technology",
            to_name="Stoicism",
            to_type="Concept",
            kind="UNRELATED",
            source_doc="random.md",
        ),
    ]


class TestEmitEntityStubs:
    @pytest.mark.unit
    def test_creates_entities_directory(self, tmp_path):
        emit_entity_stubs(_make_entities(), _make_relationships(), tmp_path)
        assert (tmp_path / "entities").is_dir()

    @pytest.mark.unit
    def test_returns_paths(self, tmp_path):
        nodes_path, edges_path = emit_entity_stubs(_make_entities(), _make_relationships(), tmp_path)
        assert nodes_path == tmp_path / "entities" / "nodes.json"
        assert edges_path == tmp_path / "entities" / "edges.json"

    @pytest.mark.unit
    def test_nodes_json_count(self, tmp_path):
        nodes_path, _ = emit_entity_stubs(_make_entities(), _make_relationships(), tmp_path)
        nodes = json.loads(nodes_path.read_text())
        assert len(nodes) == 3

    @pytest.mark.unit
    def test_nodes_json_fields(self, tmp_path):
        nodes_path, _ = emit_entity_stubs(_make_entities(), _make_relationships(), tmp_path)
        nodes = json.loads(nodes_path.read_text())
        marcus = next(n for n in nodes if n["id"] == "marcus-aurelius")
        assert marcus["label"] == "Person"
        assert marcus["name"] == "Marcus Aurelius"
        assert marcus["description"] == "Roman emperor and Stoic philosopher"
        assert "Aurelius" in marcus["aliases"]

    @pytest.mark.unit
    def test_edges_json_count(self, tmp_path):
        _, edges_path = emit_entity_stubs(_make_entities(), _make_relationships(), tmp_path)
        edges = json.loads(edges_path.read_text())
        assert len(edges) == 2

    @pytest.mark.unit
    def test_edges_json_fields(self, tmp_path):
        _, edges_path = emit_entity_stubs(_make_entities(), _make_relationships(), tmp_path)
        edges = json.loads(edges_path.read_text())
        practised = next(e for e in edges if e["kind"] == "PRACTISED")
        assert practised["from_id"] == "marcus-aurelius"
        assert practised["to_id"] == "stoicism"

    @pytest.mark.unit
    def test_edge_deduplication(self, tmp_path):
        rels = [*_make_relationships(), _make_relationships()[0]]  # duplicate
        _, edges_path = emit_entity_stubs(_make_entities(), rels, tmp_path)
        edges = json.loads(edges_path.read_text())
        assert len(edges) == 2

    @pytest.mark.unit
    def test_valid_json_format(self, tmp_path):
        nodes_path, edges_path = emit_entity_stubs(_make_entities(), _make_relationships(), tmp_path)
        nodes = json.loads(nodes_path.read_text())
        edges = json.loads(edges_path.read_text())
        assert isinstance(nodes, list), "nodes.json should contain a JSON array"
        assert isinstance(edges, list), "edges.json should contain a JSON array"

    @pytest.mark.unit
    def test_nested_directory_creation(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        emit_entity_stubs(_make_entities(), _make_relationships(), deep)
        assert (deep / "entities" / "nodes.json").exists()

    @pytest.mark.unit
    def test_empty_entities(self, tmp_path):
        nodes_path, edges_path = emit_entity_stubs([], [], tmp_path)
        assert json.loads(nodes_path.read_text()) == []
        assert json.loads(edges_path.read_text()) == []
