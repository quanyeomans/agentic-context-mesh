"""Step definitions for curator_health.feature."""

from pytest_bdd import given, then, when

from kairix.agents.curator.health import run_health_check
from tests.fixtures.neo4j_mock import FakeNeo4jClient

_state: dict = {}


@given("Neo4j has 5 entities with vault_path and summary set")
def neo4j_healthy():
    entities = [
        {
            "id": f"entity-{i}",
            "name": f"Entity {i}",
            "label": "Organisation",
            "vault_path": f"entities/entity-{i}.md",
            "summary": f"Summary {i}",
        }
        for i in range(5)
    ]
    _state["neo4j"] = FakeNeo4jClient(entities=entities)


@given("Neo4j is unavailable")
def neo4j_unavailable():
    class UnavailableClient:
        available = False

    _state["neo4j"] = UnavailableClient()


@given("Neo4j has an entity with no vault_path")
def neo4j_missing_vault_path():
    # FakeNeo4jClient returns missing_vault_path when "vault_path IS NULL" is in query
    # Override cypher to return one missing vault_path entity
    entities = [
        {
            "id": "broken-entity",
            "name": "Broken Entity",
            "label": "Organisation",
            "vault_path": None,
            "summary": "Has no vault path",
        }
    ]

    class ClientWithMissingVaultPath(FakeNeo4jClient):
        def cypher(self, query, params=None):
            if "vault_path IS NULL" in query or "vault_path IS NULL OR" in query:
                return [{"id": "broken-entity", "name": "Broken Entity", "label": "Organisation"}]
            return super().cypher(query, params)

    _state["neo4j"] = ClientWithMissingVaultPath(entities=entities)


@when("I run the curator health check")
def run_health():
    _state["report"] = run_health_check(neo4j_client=_state["neo4j"])


@then("missing_vault_path is empty")
def check_missing_vault_empty():
    assert len(_state["report"].missing_vault_path) == 0


@then("missing_vault_path is not empty")
def check_missing_vault_not_empty():
    assert len(_state["report"].missing_vault_path) > 0


@then("stale_entities is empty")
def check_stale_empty():
    assert len(_state["report"].stale_entities) == 0


@then("total_entities equals 5")
def check_total_5():
    assert _state["report"].total_entities == 5


@then("total_entities equals 0")
def check_total_0():
    assert _state["report"].total_entities == 0


@then("neo4j_available is true")
def check_neo4j_available():
    assert _state["report"].neo4j_available is True


@then("neo4j_available is false")
def check_neo4j_unavailable():
    assert _state["report"].neo4j_available is False
