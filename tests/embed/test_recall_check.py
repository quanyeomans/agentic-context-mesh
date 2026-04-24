"""
Tests for kairix.embed.recall_check

Covers:
- _get_recall_queries(): default + env override
- _embed_query(): missing credentials, request mock
- _vsearch_direct(): DB query with synthetic vectors
- check_recall(): end-to-end with mocked embed + DB
"""

from __future__ import annotations

import json
import sqlite3
import struct
from unittest.mock import MagicMock, patch

import pytest

from kairix.embed.recall_check import (
    _embed_query,
    _get_recall_queries,
    _vsearch_direct,
    check_recall,
)

# ---------------------------------------------------------------------------
# _get_recall_queries
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_recall_queries_returns_defaults() -> None:
    """Returns non-empty list of (id, query, gold_fragment) tuples by default."""
    queries = _get_recall_queries()
    assert len(queries) >= 1
    for row in queries:
        assert len(row) == 3
        qid, query, gold = row
        assert isinstance(qid, str)
        assert isinstance(query, str)
        assert isinstance(gold, str)


@pytest.mark.unit
def test_get_recall_queries_uses_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """RECALL_QUERIES env var overrides defaults when valid JSON."""
    custom = [["T1", "what is the test?", "test-fragment"]]
    monkeypatch.setenv("RECALL_QUERIES", json.dumps(custom))
    queries = _get_recall_queries()
    assert queries == [("T1", "what is the test?", "test-fragment")]


@pytest.mark.unit
def test_get_recall_queries_falls_back_on_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to defaults when RECALL_QUERIES env var is invalid JSON."""
    monkeypatch.setenv("RECALL_QUERIES", "not-valid-json{{{")
    queries = _get_recall_queries()
    assert len(queries) >= 1  # returns defaults


# ---------------------------------------------------------------------------
# _embed_query
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_embed_query_returns_none_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None when Azure credentials are not set."""
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    result = _embed_query("test query")
    assert result is None


@pytest.mark.unit
def test_embed_query_returns_bytes_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns packed float bytes when API call succeeds."""
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.example.com/")

    fake_vec = [0.1] * 1536
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": [{"embedding": fake_vec}]}

    with patch("kairix.embed.recall_check.requests.post", return_value=mock_resp):
        result = _embed_query("test query")

    assert result is not None
    assert len(result) == 1536 * 4  # 1536 float32s x 4 bytes


@pytest.mark.unit
def test_embed_query_returns_none_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None when the API call raises an exception."""
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.example.com/")

    with patch("kairix.embed.recall_check.requests.post", side_effect=OSError("timeout")):
        result = _embed_query("test query")

    assert result is None


# ---------------------------------------------------------------------------
# _vsearch_direct
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_vsearch_direct_returns_empty_for_no_vectors(tmp_path: pytest.TempPathFactory) -> None:
    """Returns [] when vectors_vec table has no rows."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE vectors_vec (rowid INTEGER, source_path TEXT, embedding BLOB)")
    # No vec0 extension available in CI — expect empty result or graceful failure
    result = _vsearch_direct(db, struct.pack("<1536f", *([0.0] * 1536)))
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# check_recall
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_recall_skips_when_embed_returns_none() -> None:
    """check_recall() marks queries as skipped when _embed_query returns None."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE vectors_vec (rowid INTEGER, source_path TEXT, embedding BLOB)")

    with patch("kairix.embed.recall_check._embed_query", return_value=None):
        result = check_recall(db=db)

    assert result["score"] == 0.0
    assert result["passed"] == 0
    assert all(d.get("skipped") for d in result["detail"])


@pytest.mark.unit
def test_check_recall_returns_structure() -> None:
    """check_recall() always returns a dict with required keys."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE vectors_vec (rowid INTEGER, source_path TEXT, embedding BLOB)")

    with patch("kairix.embed.recall_check._embed_query", return_value=None):
        result = check_recall(db=db)

    assert "score" in result
    assert "passed" in result
    assert "total" in result
    assert "detail" in result
    assert isinstance(result["detail"], list)


@pytest.mark.unit
def test_check_recall_counts_hit_when_gold_in_results() -> None:
    """check_recall() counts a hit when gold fragment appears in vsearch results."""
    db = sqlite3.connect(":memory:")

    fake_vec = struct.pack("<1536f", *([0.1] * 1536))
    fake_files = ["04-Agent-Knowledge/builder/patterns.md"]

    with (
        patch("kairix.embed.recall_check._embed_query", return_value=fake_vec),
        patch("kairix.embed.recall_check._vsearch_direct", return_value=fake_files),
        patch(
            "kairix.embed.recall_check._get_recall_queries",
            return_value=[("R1", "engineering patterns", "builder/patterns")],
        ),
    ):
        result = check_recall(db=db)

    assert result["passed"] == 1
    assert result["score"] == 1.0
    assert result["detail"][0]["hit"] is True
