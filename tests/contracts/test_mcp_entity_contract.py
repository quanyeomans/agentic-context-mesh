"""Contract test: MCP entity tool returns populated fields for known entities.

Verifies that tool_entity returns non-null name, type, and a synthesised
summary when the Neo4j graph contains an entity with type-specific properties.
Guards the boundary between MCP tool output and Neo4j node properties.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kairix.agents.mcp.server import tool_entity

pytestmark = pytest.mark.contract


class _FakeNeo4jClient:
    """Minimal stand-in for the Neo4j graph client."""

    available = True

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def cypher(self, query: str, params: dict | None = None) -> list[dict]:
        return self._rows


class TestEntityCardPersonNode:
    """PersonNode entities return role/org in the summary."""

    def test_person_returns_populated_fields(self) -> None:
        fake = _FakeNeo4jClient(
            [
                {
                    "type": "Person",
                    "id": "alistair-black",
                    "name": "Alistair Black",
                    "vault_path": "Network/People-Notes/alistair-black.md",
                    "role": "Principal Engineer",
                    "org": "Acme Corp",
                    "tier": None,
                    "engagement_status": None,
                    "domain": None,
                    "industry": None,
                    "category": None,
                }
            ]
        )
        with patch("kairix.knowledge.graph.client.get_client", return_value=fake):
            result = tool_entity(name="Alistair Black")

        assert result["name"] == "Alistair Black"
        assert result["type"] == "Person"
        assert result["id"] == "alistair-black"
        assert result["summary"] != ""
        assert "Principal Engineer" in result["summary"]
        assert "Acme Corp" in result["summary"]
        assert result["vault_path"] != ""
        assert result["error"] == ""

    def test_person_without_role_returns_empty_summary(self) -> None:
        fake = _FakeNeo4jClient(
            [
                {
                    "type": "Person",
                    "id": "jane-doe",
                    "name": "Jane Doe",
                    "vault_path": "",
                    "role": None,
                    "org": None,
                    "tier": None,
                    "engagement_status": None,
                    "domain": None,
                    "industry": None,
                    "category": None,
                }
            ]
        )
        with patch("kairix.knowledge.graph.client.get_client", return_value=fake):
            result = tool_entity(name="Jane Doe")

        assert result["name"] == "Jane Doe"
        assert result["summary"] == ""


class TestEntityCardOrganisationNode:
    """OrganisationNode entities return tier/engagement in the summary."""

    def test_org_returns_populated_fields(self) -> None:
        fake = _FakeNeo4jClient(
            [
                {
                    "type": "Organisation",
                    "id": "acme-corp",
                    "name": "Acme Corp",
                    "vault_path": "02-Areas/00-Clients/Acme-Corp/index.md",
                    "role": None,
                    "org": None,
                    "tier": "client",
                    "engagement_status": "active",
                    "domain": None,
                    "industry": ["consulting", "technology"],
                    "category": None,
                }
            ]
        )
        with patch("kairix.knowledge.graph.client.get_client", return_value=fake):
            result = tool_entity(name="Acme Corp")

        assert result["name"] == "Acme Corp"
        assert result["type"] == "Organisation"
        assert "client" in result["summary"]
        assert "active" in result["summary"]
        assert "consulting" in result["summary"]
        assert result["error"] == ""


class TestEntityCardConceptNode:
    """Concept/Outcome nodes return domain in the summary."""

    def test_concept_returns_domain(self) -> None:
        fake = _FakeNeo4jClient(
            [
                {
                    "type": "Concept",
                    "id": "zero-trust",
                    "name": "Zero Trust",
                    "vault_path": "",
                    "role": None,
                    "org": None,
                    "tier": None,
                    "engagement_status": None,
                    "domain": "cybersecurity",
                    "industry": None,
                    "category": None,
                }
            ]
        )
        with patch("kairix.knowledge.graph.client.get_client", return_value=fake):
            result = tool_entity(name="Zero Trust")

        assert result["type"] == "Concept"
        assert "cybersecurity" in result["summary"]


class TestEntityNotFound:
    """Missing entities return an error, not a crash."""

    def test_missing_entity_returns_error(self) -> None:
        fake = _FakeNeo4jClient([])
        with patch("kairix.knowledge.graph.client.get_client", return_value=fake):
            result = tool_entity(name="Nobody Real")

        assert result["error"] != ""
        assert result["name"] == "Nobody Real"
