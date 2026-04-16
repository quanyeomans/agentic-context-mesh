"""Fake Neo4j client for tests — no real Neo4j connection required."""
from __future__ import annotations

_DEFAULT_ENTITIES: list[dict] = [
    {"id": "openclaw", "name": "OpenClaw", "label": "Organisation", "vault_path": "entities/openclaw.md", "summary": "AI agent platform"},
    {"id": "avanade", "name": "Avanade", "label": "Organisation", "vault_path": "entities/avanade.md", "summary": "Microsoft services partner"},
    {"id": "alice-smith", "name": "Alice Smith", "label": "Person", "vault_path": "entities/alice-smith.md", "summary": "Founder"},
    {"id": "kairix-project", "name": "Kairix", "label": "Project", "vault_path": "entities/kairix.md", "summary": "Hybrid search memory system"},
    {"id": "tc-ventures", "name": "Three Cubes Ventures", "label": "Organisation", "vault_path": "entities/tc-ventures.md", "summary": "Venture studio"},
]


class FakeNeo4jClient:
    """Fake Neo4jClient satisfying the Neo4jClient interface. No real Neo4j required."""

    available: bool = True

    def __init__(self, entities: list[dict] | None = None) -> None:
        self._entities: list[dict] = entities if entities is not None else list(_DEFAULT_ENTITIES)

    def cypher(self, query: str, params: dict | None = None) -> list[dict]:
        """Pattern-match query string and return appropriate fake results."""
        if "vault_path IS NULL" in query:
            return []
        if "summary IS NULL" in query:
            return []
        if "COUNT(*)" in query:
            return [{"label": "Organisation", "cnt": len(self._entities)}]
        if "last_seen IS NOT NULL" in query:
            return []
        return self._entities

    def find_by_name(self, name: str) -> list[dict]:
        """Case-insensitive match against stored entities by 'name' field."""
        name_lower = name.lower()
        return [e for e in self._entities if e.get("name", "").lower() == name_lower]

    def related_entities(self, entity_id: str, max_hops: int = 2) -> list[dict]:
        """Return related entities — always empty in the fake."""
        return []

    def upsert_organisation(self, **kwargs) -> dict:
        """Stub — no-op in fake."""
        return kwargs

    def upsert_edge(self, **kwargs) -> None:
        """Stub — no-op in fake."""
        return None
