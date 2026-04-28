"""Tests for kairix.entities.seed — entity discovery from indexed documents."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory DB with sample documents for entity seeding."""
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY, collection TEXT, path TEXT,
            title TEXT, hash TEXT, active INTEGER DEFAULT 1
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT);
        INSERT INTO documents (collection, path, title, hash, active) VALUES
            ('default', 'clients/acme-corp.md', 'acme-corp', 'h1', 1),
            ('default', 'people/alice-chen.md', 'alice-chen', 'h2', 1),
            ('default', 'projects/kubernetes-migration.md', 'kubernetes-migration', 'h3', 1),
            ('default', 'notes/daily-2026-04-28.md', 'daily-2026-04-28', 'h4', 1);
        INSERT INTO content (hash, doc) VALUES
            ('h1', 'Acme Corp is a technology consulting firm based in Sydney.'),
            ('h2', 'Alice Chen is the CTO of Acme Corp. She leads the platform team.'),
            ('h3', 'The Kubernetes migration project involves moving from VMs to containers.'),
            ('h4', 'Met with Alice about the Kubernetes migration timeline.');
    """)
    return db


class TestScanForEntities:
    def test_discovers_entities_from_titles(self) -> None:
        from kairix.entities.seed import scan_for_entities

        db = _make_test_db()
        candidates = scan_for_entities(db, limit=100)
        # Should find entities from document titles (people/, clients/ folders)
        assert len(candidates) > 0

    def test_respects_limit(self) -> None:
        from kairix.entities.seed import scan_for_entities

        db = _make_test_db()
        candidates = scan_for_entities(db, limit=2)
        assert len(candidates) <= 2

    def test_returns_entity_candidates_with_required_fields(self) -> None:
        from kairix.entities.seed import scan_for_entities

        db = _make_test_db()
        candidates = scan_for_entities(db, limit=100)
        for c in candidates:
            assert hasattr(c, "name")
            assert hasattr(c, "entity_type")
            assert hasattr(c, "confidence")
            assert hasattr(c, "source_docs")
            assert 0.0 <= c.confidence <= 1.0

    def test_deduplicates_by_name(self) -> None:
        from kairix.entities.seed import scan_for_entities

        db = _make_test_db()
        candidates = scan_for_entities(db, limit=100)
        names = [c.name.lower() for c in candidates]
        assert len(names) == len(set(names))


class TestSeedGraph:
    def test_upserts_confirmed_candidates(self) -> None:
        from kairix.entities.seed import EntityCandidate, seed_graph

        mock_client = MagicMock()
        mock_client.available = True
        mock_client.upsert_node.return_value = True

        candidates = [
            EntityCandidate(name="Acme Corp", entity_type="Organisation", confidence=0.9, source_docs=["acme-corp.md"]),
            EntityCandidate(name="Alice Chen", entity_type="Person", confidence=0.85, source_docs=["alice-chen.md"]),
        ]
        count = seed_graph(mock_client, candidates)
        assert count == 2
        assert mock_client.upsert_node.call_count == 2

    def test_returns_zero_when_neo4j_unavailable(self) -> None:
        from kairix.entities.seed import EntityCandidate, seed_graph

        mock_client = MagicMock()
        mock_client.available = False

        candidates = [
            EntityCandidate(name="Test", entity_type="Organisation", confidence=0.9, source_docs=["t.md"]),
        ]
        count = seed_graph(mock_client, candidates)
        assert count == 0

    def test_handles_upsert_failure_gracefully(self) -> None:
        from kairix.entities.seed import EntityCandidate, seed_graph

        mock_client = MagicMock()
        mock_client.available = True
        mock_client.upsert_node.return_value = False  # upsert fails

        candidates = [
            EntityCandidate(name="Failing Entity", entity_type="Organisation", confidence=0.9, source_docs=["f.md"]),
        ]
        count = seed_graph(mock_client, candidates)
        assert count == 0  # failed upserts don't count
