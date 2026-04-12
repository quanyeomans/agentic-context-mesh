"""
Tests for kairix.curator.health — entity graph health check (CA-1).

All tests use an in-memory entities.db (schema v2) via KAIRIX_TEST_DB env var.
No external services required.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from kairix.curator.health import (
    format_report_json,
    format_report_text,
    run_health_check,
)
from kairix.entities.schema import open_entities_db


@pytest.fixture()
def health_db(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    """Fresh entities DB (schema v2) isolated per test via KAIRIX_TEST_DB."""
    db_path = str(tmp_path / "health_test.db")
    monkeypatch.setenv("KAIRIX_TEST_DB", db_path)
    db = open_entities_db()
    yield db  # type: ignore[misc]
    db.close()


def _insert(
    db: sqlite3.Connection,
    entity_id: str,
    entity_type: str = "person",
    name: str = "Test Entity",
    summary: str | None = "A summary.",
    vault_path: str | None = "/vault/path",
    status: str = "active",
    last_seen: str | None = "2026-04-10T00:00:00Z",
    updated_at: str = "2026-04-10T00:00:00Z",
    created_at: str = "2026-01-01T00:00:00Z",
) -> None:
    """Insert a minimal entity row for testing."""
    db.execute(
        "INSERT INTO entities"
        " (id, type, name, markdown_path, summary, vault_path, status,"
        "  last_seen, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            entity_type,
            name,
            f"{entity_id}.md",
            summary,
            vault_path,
            status,
            last_seen,
            created_at,
            updated_at,
        ),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Entity counts
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_empty_db_zero_counts(health_db: sqlite3.Connection) -> None:
    report = run_health_check(health_db)
    assert report.total_entities == 0
    assert report.entities_by_type == {}
    assert report.ok is True


@pytest.mark.unit
def test_counts_entities_by_type(health_db: sqlite3.Connection) -> None:
    _insert(health_db, "p1", entity_type="person", name="Alice")
    _insert(health_db, "p2", entity_type="person", name="Bob")
    _insert(health_db, "o1", entity_type="organisation", name="Acme")
    report = run_health_check(health_db)
    assert report.total_entities == 3
    assert report.entities_by_type == {"person": 2, "organisation": 1}


# ---------------------------------------------------------------------------
# Synthesis failures
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_synthesis_failure_null_summary(health_db: sqlite3.Connection) -> None:
    _insert(health_db, "no-summary", summary=None)
    report = run_health_check(health_db)
    assert any(i.entity_id == "no-summary" for i in report.synthesis_failures)


@pytest.mark.unit
def test_synthesis_failure_whitespace_only_summary(health_db: sqlite3.Connection) -> None:
    # SQLite trim("   ") = "" — should be treated as missing
    _insert(health_db, "ws-summary", summary="   ")
    report = run_health_check(health_db)
    assert any(i.entity_id == "ws-summary" for i in report.synthesis_failures)


@pytest.mark.unit
def test_healthy_entity_not_in_synthesis_failures(health_db: sqlite3.Connection) -> None:
    _insert(health_db, "good", summary="Has a real summary.")
    report = run_health_check(health_db)
    assert not any(i.entity_id == "good" for i in report.synthesis_failures)


# ---------------------------------------------------------------------------
# Missing vault path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_vault_path_detected(health_db: sqlite3.Connection) -> None:
    _insert(health_db, "no-path", vault_path=None)
    report = run_health_check(health_db)
    assert any(i.entity_id == "no-path" for i in report.missing_vault_path)


@pytest.mark.unit
def test_vault_path_set_not_flagged(health_db: sqlite3.Connection) -> None:
    _insert(health_db, "has-path", vault_path="Network/People-Notes/test.md")
    report = run_health_check(health_db)
    assert not any(i.entity_id == "has-path" for i in report.missing_vault_path)


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stale_entity_detected(health_db: sqlite3.Connection) -> None:
    # Last seen and updated >200 days before test date — stale under 90-day threshold
    _insert(
        health_db,
        "stale",
        last_seen="2025-09-01T00:00:00Z",
        updated_at="2025-09-01T00:00:00Z",
    )
    report = run_health_check(health_db, staleness_days=90)
    assert any(i.entity_id == "stale" for i in report.stale_entities)


@pytest.mark.unit
def test_recent_entity_not_stale(health_db: sqlite3.Connection) -> None:
    # Last seen 2 days ago — well within any reasonable staleness window
    _insert(
        health_db,
        "fresh",
        last_seen="2026-04-10T00:00:00Z",
        updated_at="2026-04-10T00:00:00Z",
    )
    report = run_health_check(health_db, staleness_days=90)
    assert not any(i.entity_id == "fresh" for i in report.stale_entities)


@pytest.mark.unit
def test_staleness_days_param_respected(health_db: sqlite3.Connection) -> None:
    # Entity updated ~30 days before 2026-04-12: stale under 10-day threshold, not under 60-day
    _insert(
        health_db,
        "mid",
        last_seen="2026-03-13T00:00:00Z",
        updated_at="2026-03-13T00:00:00Z",
    )
    stale_10 = [i.entity_id for i in run_health_check(health_db, staleness_days=10).stale_entities]
    stale_60 = [i.entity_id for i in run_health_check(health_db, staleness_days=60).stale_entities]
    assert "mid" in stale_10
    assert "mid" not in stale_60


@pytest.mark.unit
def test_null_last_seen_counts_as_stale(health_db: sqlite3.Connection) -> None:
    # An entity never seen in search (last_seen=NULL) and not updated recently is stale
    _insert(
        health_db,
        "never-seen",
        last_seen=None,
        updated_at="2025-09-01T00:00:00Z",
    )
    report = run_health_check(health_db, staleness_days=90)
    assert any(i.entity_id == "never-seen" for i in report.stale_entities)


# ---------------------------------------------------------------------------
# Archived entities excluded from all checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_archived_entity_excluded_from_all_checks(health_db: sqlite3.Connection) -> None:
    _insert(
        health_db,
        "archived",
        summary=None,
        vault_path=None,
        status="archived",
        last_seen="2020-01-01T00:00:00Z",
        updated_at="2020-01-01T00:00:00Z",
    )
    report = run_health_check(health_db)
    all_issue_ids = (
        [i.entity_id for i in report.synthesis_failures]
        + [i.entity_id for i in report.missing_vault_path]
        + [i.entity_id for i in report.stale_entities]
    )
    assert "archived" not in all_issue_ids
    assert report.total_entities == 0


# ---------------------------------------------------------------------------
# Neo4j graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_neo4j_none_returns_unavailable(health_db: sqlite3.Connection) -> None:
    report = run_health_check(health_db, neo4j_client=None)
    assert report.neo4j_available is False
    assert report.neo4j_node_counts == {}


@pytest.mark.unit
def test_neo4j_unavailable_client_graceful(health_db: sqlite3.Connection) -> None:
    class _UnavailableClient:
        available = False

    report = run_health_check(health_db, neo4j_client=_UnavailableClient())
    assert report.neo4j_available is False
    assert report.neo4j_node_counts == {}


# ---------------------------------------------------------------------------
# ok property
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ok_false_when_synthesis_failures(health_db: sqlite3.Connection) -> None:
    _insert(health_db, "bad", summary=None)
    assert run_health_check(health_db).ok is False


@pytest.mark.unit
def test_ok_true_when_all_healthy(health_db: sqlite3.Connection) -> None:
    # Entity with summary + vault_path + recent activity — no issues expected
    _insert(
        health_db,
        "healthy",
        summary="Complete summary.",
        vault_path="Network/People-Notes/healthy.md",
        last_seen="2026-04-10T00:00:00Z",
        updated_at="2026-04-10T00:00:00Z",
    )
    report = run_health_check(health_db, staleness_days=90)
    assert report.ok is True


# ---------------------------------------------------------------------------
# JSON serialisability (contract test)
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_report_json_serialisable(health_db: sqlite3.Connection) -> None:
    """HealthReport must serialise cleanly via format_report_json."""
    _insert(health_db, "e1", summary=None, vault_path=None)
    report = run_health_check(health_db)
    raw = format_report_json(report)
    decoded = json.loads(raw)
    assert decoded["total_entities"] == 1
    assert len(decoded["synthesis_failures"]) == 1
    assert decoded["synthesis_failures"][0]["entity_id"] == "e1"
    assert decoded["neo4j_available"] is False


@pytest.mark.unit
def test_format_report_text_contains_key_sections(health_db: sqlite3.Connection) -> None:
    """Text report must include all section headings."""
    report = run_health_check(health_db)
    text = format_report_text(report)
    assert "# Kairix — Entity Health Report" in text
    assert "## Synthesis Failures" in text
    assert "## Stale Entities" in text
    assert "## Missing Vault Path" in text
    assert "## Graph (Neo4j)" in text
    assert "**Status:" in text


@pytest.mark.unit
def test_format_report_text_renders_issues(health_db: sqlite3.Connection) -> None:
    """Text report renders issue lines and ISSUES FOUND status when problems exist."""
    # Entity with all three issue types
    _insert(
        health_db,
        "bad-entity",
        summary=None,
        vault_path=None,
        last_seen="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
    )
    report = run_health_check(health_db, staleness_days=90)
    text = format_report_text(report)
    # Issue lines rendered for synthesis failures, missing vault path, and stale
    assert "bad-entity" in text
    assert "ISSUES FOUND" in text


@pytest.mark.unit
def test_format_report_text_neo4j_connected_with_nodes(health_db: sqlite3.Connection) -> None:
    """Text report renders 'Connected' with node counts when Neo4j is available."""
    from kairix.curator.health import HealthReport

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
def test_format_report_text_neo4j_connected_no_nodes(health_db: sqlite3.Connection) -> None:
    """Text report renders 'Connected — no nodes found' when Neo4j is available but empty."""
    from kairix.curator.health import HealthReport

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
