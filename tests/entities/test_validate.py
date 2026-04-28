"""Tests for kairix.knowledge.entities.validate — Wikidata validator."""

from unittest.mock import MagicMock, patch

import pytest

from kairix.knowledge.entities.validate import WikidataMatch, search_wikidata, validate_entity
from tests.fixtures.neo4j_mock import FakeNeo4jClient


def _mock_wikidata_response(items: list[dict]):
    """Build a mock requests.get response returning Wikidata items."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"search": items}
    return mock_resp


@pytest.mark.unit
def test_search_wikidata_returns_empty_on_network_error():
    with patch("kairix.knowledge.entities.validate.requests.get", side_effect=ConnectionError("timeout")):
        result = search_wikidata("OpenClaw")
    assert result == []


@pytest.mark.unit
def test_search_wikidata_parses_results():
    fake_items = [
        {"id": "Q123", "label": "OpenClaw", "description": "AI agent platform"},
        {"id": "Q456", "label": "Open Claw Tool", "description": "A hardware tool"},
    ]
    with patch("kairix.knowledge.entities.validate.requests.get", return_value=_mock_wikidata_response(fake_items)):
        results = search_wikidata("OpenClaw")
    assert len(results) == 2
    assert results[0].qid == "Q123"
    assert results[0].confidence == "high"  # exact label match
    assert results[1].confidence in ("medium", "low")


@pytest.mark.unit
def test_confidence_high_on_exact_match():
    fake_items = [{"id": "Q1", "label": "ACME", "description": "Example company"}]
    with patch("kairix.knowledge.entities.validate.requests.get", return_value=_mock_wikidata_response(fake_items)):
        results = search_wikidata("ACME")
    assert results[0].confidence == "high"


@pytest.mark.unit
def test_validate_entity_no_neo4j_match():
    neo4j = FakeNeo4jClient(entities=[])
    fake_items = [{"id": "Q999", "label": "Unknown", "description": "Unknown entity"}]
    with patch("kairix.knowledge.entities.validate.requests.get", return_value=_mock_wikidata_response(fake_items)):
        result = validate_entity("Unknown", neo4j)
    assert result["neo4j_id"] is None
    assert len(result["matches"]) == 1
    assert result["updated"] is False


@pytest.mark.unit
def test_validate_entity_with_neo4j_match():
    neo4j = FakeNeo4jClient()  # has OpenClaw in default entities
    fake_items = [{"id": "Q100", "label": "OpenClaw", "description": "AI platform"}]
    with patch("kairix.knowledge.entities.validate.requests.get", return_value=_mock_wikidata_response(fake_items)):
        result = validate_entity("OpenClaw", neo4j)
    assert result["neo4j_id"] == "openclaw"
    assert result["matches"][0]["qid"] == "Q100"


@pytest.mark.unit
def test_validate_entity_update_writes_qid():
    neo4j = FakeNeo4jClient()
    fake_items = [{"id": "Q100", "label": "OpenClaw", "description": "AI platform"}]
    with patch("kairix.knowledge.entities.validate.requests.get", return_value=_mock_wikidata_response(fake_items)):
        result = validate_entity("OpenClaw", neo4j, update=True)
    assert result["updated"] is True


@pytest.mark.unit
def test_validate_entity_never_raises_on_api_failure():
    neo4j = FakeNeo4jClient()
    with patch("kairix.knowledge.entities.validate.requests.get", side_effect=Exception("connection refused")):
        result = validate_entity("OpenClaw", neo4j)
    assert result["matches"] == []
    assert result["error"] == ""


@pytest.mark.contract
def test_wikidata_match_has_required_fields():
    m = WikidataMatch(qid="Q1", label="Test", description="Desc", url="https://wikidata.org/wiki/Q1", confidence="high")
    assert hasattr(m, "qid")
    assert hasattr(m, "label")
    assert hasattr(m, "confidence")
    assert m.confidence in ("high", "medium", "low")
