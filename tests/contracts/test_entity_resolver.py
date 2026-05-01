"""Contract: EntityResolverProtocol conformance.

Verifies that FakeNeo4jClient and the contracts.EntityResolverProtocol
are structurally compatible. Also validates the resolver contract behaviour.
"""

import pytest

from kairix.quality.contracts.entities import EntityResolverProtocol
from tests.fixtures.neo4j_mock import FakeNeo4jClient


class _Neo4jResolverAdapter:
    """Thin adapter to make Neo4jClient satisfy EntityResolverProtocol."""

    def __init__(self, client: FakeNeo4jClient) -> None:
        self._client = client

    def resolve(self, name: str) -> str | None:
        rows = self._client.find_by_name(name)
        return str(rows[0]["id"]) if rows else None

    def related(self, entity_id: str, rel_type: str | None = None) -> list[str]:
        rows = self._client.related_entities(entity_id)
        return [str(r.get("id", "")) for r in rows]


@pytest.mark.contract
def test_adapter_satisfies_entity_resolver_protocol(neo4j_client):
    adapter = _Neo4jResolverAdapter(neo4j_client)
    assert isinstance(adapter, EntityResolverProtocol)


@pytest.mark.contract
def test_resolve_known_entity(neo4j_client):
    adapter = _Neo4jResolverAdapter(neo4j_client)
    result = adapter.resolve("OpenClaw")
    assert result == "openclaw"


@pytest.mark.contract
def test_resolve_unknown_entity_returns_none(neo4j_client):
    adapter = _Neo4jResolverAdapter(neo4j_client)
    result = adapter.resolve("NonExistentEntity12345")
    assert result is None


@pytest.mark.contract
def test_related_returns_list(neo4j_client):
    adapter = _Neo4jResolverAdapter(neo4j_client)
    result = adapter.related("openclaw")
    assert isinstance(result, list)
