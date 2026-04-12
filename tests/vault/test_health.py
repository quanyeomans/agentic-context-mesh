"""
Tests for kairix.vault.health — Neo4j-primary vault health check.

All Neo4j calls are mocked. Never raises.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kairix.vault.health import VaultHealthReport, format_health_text, run_vault_health


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_neo4j(available: bool = True) -> MagicMock:
    client = MagicMock()
    client.available = available
    client.cypher.return_value = []
    return client


def _neo4j_with_counts(
    orgs: int = 5,
    persons: int = 10,
    outcomes: int = 2,
    orgs_missing_path: int = 0,
    persons_missing_path: int = 0,
    orgs_missing_summary: int = 0,
    persons_missing_summary: int = 0,
    works_at: int = 8,
    knows: int = 3,
    mentions: int = 20,
) -> MagicMock:
    """Build a mock neo4j client that returns realistic query results."""
    client = MagicMock()
    client.available = True

    def _cypher(query, params=None):
        q = query.strip()
        if "labels(n)[0] IN" in q:
            return [
                {"label": "Organisation", "cnt": orgs},
                {"label": "Person", "cnt": persons},
                {"label": "Outcome", "cnt": outcomes},
            ]
        if "Organisation" in q and "vault_path" in q:
            return [{"cnt": orgs_missing_path}]
        if "Person" in q and "vault_path" in q:
            return [{"cnt": persons_missing_path}]
        if "Organisation" in q and "summary" in q:
            return [{"cnt": orgs_missing_summary}]
        if "Person" in q and "summary" in q:
            return [{"cnt": persons_missing_summary}]
        if "WORKS_AT" in q:
            return [{"cnt": works_at}]
        if "KNOWS" in q:
            return [{"cnt": knows}]
        if "MENTIONS" in q:
            return [{"cnt": mentions}]
        return []

    client.cypher.side_effect = _cypher
    return client


# ---------------------------------------------------------------------------
# VaultHealthReport
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_vault_health_report_total_entities() -> None:
    r = VaultHealthReport(
        generated_at="2026-01-01T00:00:00Z",
        neo4j_available=True,
        organisation_count=3,
        person_count=7,
        outcome_count=2,
    )
    assert r.total_entities == 12


@pytest.mark.unit
def test_vault_health_report_ok_when_healthy() -> None:
    r = VaultHealthReport(
        generated_at="2026-01-01T00:00:00Z",
        neo4j_available=True,
        organisation_count=3,
        person_count=7,
        outcome_count=2,
        orgs_missing_vault_path=0,
        persons_missing_vault_path=0,
    )
    assert r.ok is True


@pytest.mark.unit
def test_vault_health_report_not_ok_when_neo4j_unavailable() -> None:
    r = VaultHealthReport(
        generated_at="2026-01-01T00:00:00Z",
        neo4j_available=False,
        organisation_count=3,
        person_count=7,
    )
    assert r.ok is False


@pytest.mark.unit
def test_vault_health_report_not_ok_when_no_entities() -> None:
    r = VaultHealthReport(
        generated_at="2026-01-01T00:00:00Z",
        neo4j_available=True,
        organisation_count=0,
        person_count=0,
        outcome_count=0,
    )
    assert r.ok is False


@pytest.mark.unit
def test_vault_health_report_not_ok_when_orgs_missing_path() -> None:
    r = VaultHealthReport(
        generated_at="2026-01-01T00:00:00Z",
        neo4j_available=True,
        organisation_count=5,
        person_count=10,
        orgs_missing_vault_path=2,
    )
    assert r.ok is False


@pytest.mark.unit
def test_vault_health_report_not_ok_when_persons_missing_path() -> None:
    r = VaultHealthReport(
        generated_at="2026-01-01T00:00:00Z",
        neo4j_available=True,
        organisation_count=5,
        person_count=10,
        persons_missing_vault_path=1,
    )
    assert r.ok is False


@pytest.mark.unit
def test_vault_health_report_not_ok_with_issues() -> None:
    r = VaultHealthReport(
        generated_at="2026-01-01T00:00:00Z",
        neo4j_available=True,
        organisation_count=5,
        person_count=10,
        issues=["some issue"],
    )
    assert r.ok is False


# ---------------------------------------------------------------------------
# run_vault_health — Neo4j unavailable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_vault_health_neo4j_unavailable() -> None:
    client = _make_neo4j(available=False)
    report = run_vault_health(neo4j_client=client)

    assert report.neo4j_available is False
    assert len(report.issues) > 0
    assert any("unavailable" in i.lower() for i in report.issues)
    client.cypher.assert_not_called()


@pytest.mark.unit
def test_run_vault_health_neo4j_unavailable_counts_zero() -> None:
    client = _make_neo4j(available=False)
    report = run_vault_health(neo4j_client=client)

    assert report.organisation_count == 0
    assert report.person_count == 0
    assert report.outcome_count == 0


# ---------------------------------------------------------------------------
# run_vault_health — healthy Neo4j
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_vault_health_populates_node_counts() -> None:
    client = _neo4j_with_counts(orgs=5, persons=10, outcomes=2)
    report = run_vault_health(neo4j_client=client)

    assert report.neo4j_available is True
    assert report.organisation_count == 5
    assert report.person_count == 10
    assert report.outcome_count == 2


@pytest.mark.unit
def test_run_vault_health_populates_edge_counts() -> None:
    client = _neo4j_with_counts(works_at=8, knows=3, mentions=20)
    report = run_vault_health(neo4j_client=client)

    assert report.works_at_edge_count == 8
    assert report.knows_edge_count == 3
    assert report.mentions_edge_count == 20


@pytest.mark.unit
def test_run_vault_health_populates_completeness_fields() -> None:
    client = _neo4j_with_counts(orgs_missing_path=2, persons_missing_path=1)
    report = run_vault_health(neo4j_client=client)

    assert report.orgs_missing_vault_path == 2
    assert report.persons_missing_vault_path == 1


@pytest.mark.unit
def test_run_vault_health_populates_summary_fields() -> None:
    client = _neo4j_with_counts(orgs_missing_summary=3, persons_missing_summary=4)
    report = run_vault_health(neo4j_client=client)

    assert report.orgs_missing_summary == 3
    assert report.persons_missing_summary == 4


@pytest.mark.unit
def test_run_vault_health_ok_when_all_clear() -> None:
    client = _neo4j_with_counts(orgs=3, persons=5, outcomes=1)
    report = run_vault_health(neo4j_client=client)

    assert report.ok is True
    assert len(report.issues) == 0


@pytest.mark.unit
def test_run_vault_health_issue_when_no_entities() -> None:
    client = _neo4j_with_counts(orgs=0, persons=0, outcomes=0)
    report = run_vault_health(neo4j_client=client)

    assert not report.ok
    assert any("crawl" in i.lower() for i in report.issues)


@pytest.mark.unit
def test_run_vault_health_issue_when_missing_org_vault_path() -> None:
    client = _neo4j_with_counts(orgs=5, persons=5, orgs_missing_path=2)
    report = run_vault_health(neo4j_client=client)

    assert not report.ok
    assert any("organisation" in i.lower() and "vault_path" in i.lower() for i in report.issues)


@pytest.mark.unit
def test_run_vault_health_issue_when_missing_person_vault_path() -> None:
    client = _neo4j_with_counts(orgs=5, persons=5, persons_missing_path=3)
    report = run_vault_health(neo4j_client=client)

    assert not report.ok
    assert any("person" in i.lower() and "vault_path" in i.lower() for i in report.issues)


@pytest.mark.unit
def test_run_vault_health_cypher_exception_does_not_raise() -> None:
    client = MagicMock()
    client.available = True
    client.cypher.side_effect = RuntimeError("connection refused")

    # Should not raise — returns a report with issues
    report = run_vault_health(neo4j_client=client)
    assert isinstance(report, VaultHealthReport)


# ---------------------------------------------------------------------------
# format_health_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_format_health_text_unavailable() -> None:
    report = VaultHealthReport(
        generated_at="2026-01-01T00:00:00Z",
        neo4j_available=False,
        issues=["Neo4j unavailable — graph health check skipped"],
    )
    text = format_health_text(report)
    assert "Neo4j unavailable" in text
    assert "HEALTHY" not in text


@pytest.mark.unit
def test_format_health_text_healthy() -> None:
    report = VaultHealthReport(
        generated_at="2026-01-01T00:00:00Z",
        neo4j_available=True,
        organisation_count=3,
        person_count=5,
        outcome_count=1,
    )
    text = format_health_text(report)
    assert "HEALTHY" in text
    assert "3" in text
    assert "5" in text


@pytest.mark.unit
def test_format_health_text_shows_issues() -> None:
    report = VaultHealthReport(
        generated_at="2026-01-01T00:00:00Z",
        neo4j_available=True,
        organisation_count=5,
        person_count=5,
        issues=["2 organisation(s) missing vault_path — re-run vault crawl"],
    )
    text = format_health_text(report)
    assert "ISSUES FOUND" in text
    assert "vault_path" in text


@pytest.mark.unit
def test_format_health_text_contains_generated_at() -> None:
    report = VaultHealthReport(generated_at="2026-04-13T09:00:00Z")
    text = format_health_text(report)
    assert "2026-04-13" in text
