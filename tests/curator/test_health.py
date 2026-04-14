"""
Tests for kairix.curator.health — entity graph health check (CA-1).

Neo4j is the canonical entity store. Tests use a mock Neo4j client that
returns pre-configured Cypher results. No SQLite dependency, no external
services required.
"""

from __future__ import annotations

import json

import pytest

from kairix.curator.health import (
    HealthReport,
    format_report_json,
    format_report_text,
    run_health_check,
)

# ---------------------------------------------------------------------------
# Mock Neo4j client
# ---------------------------------------------------------------------------


class _MockNeo4jClient:
    """
    Minimal mock of kairix.graph.client.Neo4jClient for health check tests.

    Routes Cypher calls by recognising distinguishable substrings in the query:
      - "COUNT(*)"      → entity count rows
      - "summary IS"    → synthesis failure rows
      - "vault_path IS" → missing vault_path rows
      - "last_seen"     → stale entity rows
    """

    def __init__(
        self,
        available: bool = True,
        counts: list[dict] | None = None,
        synthesis_failures: list[dict] | None = None,
        missing_vault_path: list[dict] | None = None,
        stale_entities: list[dict] | None = None,
    ) -> None:
        self.available = available
        self._counts = counts or []
        self._synthesis_failures = synthesis_failures or []
        self._missing_vault_path = missing_vault_path or []
        self._stale_entities = stale_entities or []

    def cypher(self, query: str, params: dict | None = None) -> list[dict]:
        if "COUNT(*)" in query:
            return self._counts
        if "summary IS" in query:
            return self._synthesis_failures
        if "vault_path IS" in query:
            return self._missing_vault_path
        if "last_seen" in query:
            return self._stale_entities
        return []


def _entity_row(
    entity_id: str = "e1",
    name: str = "Test Entity",
    label: str = "Person",
    last_seen: str | None = None,
) -> dict:
    row: dict = {"id": entity_id, "name": name, "label": label}
    if last_seen is not None:
        row["last_seen"] = last_seen
    return row


# ---------------------------------------------------------------------------
# neo4j_client=None / unavailable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_neo4j_none_returns_unavailable() -> None:
    report = run_health_check(None)
    assert report.neo4j_available is False
    assert report.total_entities == 0
    assert report.entities_by_type == {}
    assert report.ok is True  # no issues when unavailable


@pytest.mark.unit
def test_neo4j_unavailable_client_graceful() -> None:
    client = _MockNeo4jClient(available=False)
    report = run_health_check(client)
    assert report.neo4j_available is False
    assert report.neo4j_node_counts == {}


# ---------------------------------------------------------------------------
# Entity counts
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_empty_graph_zero_counts() -> None:
    client = _MockNeo4jClient(counts=[])
    report = run_health_check(client)
    assert report.total_entities == 0
    assert report.entities_by_type == {}
    assert report.ok is True


@pytest.mark.unit
def test_counts_entities_by_type() -> None:
    client = _MockNeo4jClient(
        counts=[
            {"label": "Person", "cnt": 2},
            {"label": "Organisation", "cnt": 1},
        ]
    )
    report = run_health_check(client)
    assert report.total_entities == 3
    assert report.entities_by_type == {"person": 2, "organisation": 1}
    assert report.neo4j_available is True


# ---------------------------------------------------------------------------
# Synthesis failures
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_synthesis_failure_detected() -> None:
    client = _MockNeo4jClient(synthesis_failures=[_entity_row("no-summary", label="Person")])
    report = run_health_check(client)
    assert any(i.entity_id == "no-summary" for i in report.synthesis_failures)
    assert report.synthesis_failures[0].detail == "no summary"


@pytest.mark.unit
def test_no_synthesis_failures_when_empty() -> None:
    client = _MockNeo4jClient(synthesis_failures=[])
    report = run_health_check(client)
    assert report.synthesis_failures == []


@pytest.mark.unit
def test_multiple_synthesis_failures() -> None:
    client = _MockNeo4jClient(
        synthesis_failures=[
            _entity_row("e1", label="Person"),
            _entity_row("e2", label="Organisation"),
        ]
    )
    report = run_health_check(client)
    ids = [i.entity_id for i in report.synthesis_failures]
    assert "e1" in ids
    assert "e2" in ids


# ---------------------------------------------------------------------------
# Missing vault_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_vault_path_detected() -> None:
    client = _MockNeo4jClient(missing_vault_path=[_entity_row("no-path", label="Organisation")])
    report = run_health_check(client)
    assert any(i.entity_id == "no-path" for i in report.missing_vault_path)
    assert report.missing_vault_path[0].detail == "vault_path not set"


@pytest.mark.unit
def test_no_missing_vault_path_when_empty() -> None:
    client = _MockNeo4jClient(missing_vault_path=[])
    report = run_health_check(client)
    assert report.missing_vault_path == []


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stale_entity_detected() -> None:
    client = _MockNeo4jClient(stale_entities=[_entity_row("stale", last_seen="2025-09-01T00:00:00Z")])
    report = run_health_check(client, staleness_days=90)
    assert any(i.entity_id == "stale" for i in report.stale_entities)
    assert "2025-09-01" in report.stale_entities[0].detail


@pytest.mark.unit
def test_no_stale_entities_when_empty() -> None:
    client = _MockNeo4jClient(stale_entities=[])
    report = run_health_check(client, staleness_days=90)
    assert report.stale_entities == []


@pytest.mark.unit
def test_null_last_seen_not_flagged_as_stale() -> None:
    """
    Entities without a last_seen property are NOT flagged as stale.
    The Neo4j query only matches nodes where last_seen IS NOT NULL.
    New entities with no activity tracking are not penalised.
    """
    # Mock returns empty stale list — client.cypher with last_seen query returns []
    client = _MockNeo4jClient(stale_entities=[])
    report = run_health_check(client, staleness_days=90)
    assert report.stale_entities == []


@pytest.mark.unit
def test_staleness_days_reflected_in_report() -> None:
    """staleness_days parameter is stored in the report for display."""
    client = _MockNeo4jClient()
    report_10 = run_health_check(client, staleness_days=10)
    report_60 = run_health_check(client, staleness_days=60)
    assert report_10.staleness_threshold_days == 10
    assert report_60.staleness_threshold_days == 60


# ---------------------------------------------------------------------------
# ok property
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ok_false_when_synthesis_failures() -> None:
    client = _MockNeo4jClient(synthesis_failures=[_entity_row("bad")])
    report = run_health_check(client)
    assert report.ok is False


@pytest.mark.unit
def test_ok_false_when_missing_vault_path() -> None:
    client = _MockNeo4jClient(missing_vault_path=[_entity_row("no-path")])
    report = run_health_check(client)
    assert report.ok is False


@pytest.mark.unit
def test_ok_false_when_stale() -> None:
    client = _MockNeo4jClient(stale_entities=[_entity_row("old")])
    report = run_health_check(client)
    assert report.ok is False


@pytest.mark.unit
def test_ok_true_when_all_healthy() -> None:
    client = _MockNeo4jClient(
        counts=[{"label": "Person", "cnt": 1}],
        synthesis_failures=[],
        missing_vault_path=[],
        stale_entities=[],
    )
    report = run_health_check(client, staleness_days=90)
    assert report.ok is True


# ---------------------------------------------------------------------------
# JSON serialisability (contract test)
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_report_json_serialisable() -> None:
    """HealthReport must serialise cleanly via format_report_json."""
    client = _MockNeo4jClient(
        synthesis_failures=[_entity_row("e1")],
        missing_vault_path=[_entity_row("e1")],
    )
    report = run_health_check(client)
    raw = format_report_json(report)
    decoded = json.loads(raw)
    assert len(decoded["synthesis_failures"]) == 1
    assert decoded["synthesis_failures"][0]["entity_id"] == "e1"
    assert decoded["neo4j_available"] is True


@pytest.mark.unit
def test_format_report_text_contains_key_sections() -> None:
    """Text report must include all section headings."""
    client = _MockNeo4jClient()
    report = run_health_check(client)
    text = format_report_text(report)
    assert "# Kairix — Entity Health Report" in text
    assert "## Synthesis Failures" in text
    assert "## Stale Entities" in text
    assert "## Missing Vault Path" in text
    assert "## Graph (Neo4j)" in text
    assert "**Status:" in text


@pytest.mark.unit
def test_format_report_text_renders_issues() -> None:
    """Text report renders issue lines and ISSUES FOUND status when problems exist."""
    client = _MockNeo4jClient(
        synthesis_failures=[_entity_row("bad-entity")],
        missing_vault_path=[_entity_row("bad-entity")],
    )
    report = run_health_check(client, staleness_days=90)
    text = format_report_text(report)
    assert "bad-entity" in text
    assert "ISSUES FOUND" in text


@pytest.mark.unit
def test_format_report_text_neo4j_connected_with_nodes() -> None:
    """Text report renders 'Connected' with node counts when Neo4j is available."""
    report = HealthReport(
        generated_at="2026-04-12T00:00:00Z",
        total_entities=0,
        entities_by_type={},
        neo4j_available=True,
        neo4j_node_counts={"Person": 5, "Organisation": 2},
    )
    text = format_report_text(report)
    assert "Connected" in text
    assert "Person" in text


@pytest.mark.unit
def test_format_report_text_neo4j_connected_no_nodes() -> None:
    """Text report renders 'Connected — no nodes found' when Neo4j is available but empty."""
    report = HealthReport(
        generated_at="2026-04-12T00:00:00Z",
        total_entities=0,
        entities_by_type={},
        neo4j_available=True,
        neo4j_node_counts={},
    )
    text = format_report_text(report)
    assert "Connected" in text
    assert "no nodes found" in text


@pytest.mark.unit
def test_format_report_text_neo4j_unavailable() -> None:
    """Text report renders 'Unavailable' when Neo4j client is not connected."""
    client = _MockNeo4jClient(available=False)
    report = run_health_check(client)
    text = format_report_text(report)
    assert "Unavailable" in text
