"""
Tests for kairix.core.embed.recall_check

Covers:
- _get_recall_queries(): default, env override, adaptive from DB
- _embed_query(): missing credentials, request mock
- _vsearch_usearch(): search via usearch index
- _build_adaptive_queries(): derive queries from indexed titles
- check_recall(): end-to-end with mocked embed + search
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from kairix.core.embed.recall_check import (
    _build_adaptive_queries,
    _embed_query,
    _get_recall_queries,
    _vsearch_usearch,
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
# _build_adaptive_queries
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_adaptive_queries_from_db() -> None:
    """Builds queries from indexed document titles."""
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (path TEXT, title TEXT, active INTEGER);
        INSERT INTO documents VALUES ('docs/architecture.md', 'architecture', 1);
        INSERT INTO documents VALUES ('docs/deploy-guide.md', 'deploy-guide', 1);
        INSERT INTO documents VALUES ('docs/testing.md', 'testing', 1);
    """)

    queries = _build_adaptive_queries(db)
    assert len(queries) == 3
    # Each query should be a tuple of (id, readable_title, path_stem)
    for qid, query, gold in queries:
        assert qid.startswith("A")
        assert isinstance(query, str)
        assert isinstance(gold, str)


@pytest.mark.unit
def test_build_adaptive_queries_empty_db() -> None:
    """Returns empty list when no documents indexed."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE documents (path TEXT, title TEXT, active INTEGER)")

    queries = _build_adaptive_queries(db)
    assert queries == []


@pytest.mark.unit
def test_build_adaptive_queries_no_table() -> None:
    """Returns empty list when documents table doesn't exist."""
    db = sqlite3.connect(":memory:")
    queries = _build_adaptive_queries(db)
    assert queries == []


@pytest.mark.unit
def test_adaptive_queries_used_when_db_available() -> None:
    """_get_recall_queries prefers adaptive queries over defaults when db is available."""
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (path TEXT, title TEXT, active INTEGER);
        INSERT INTO documents VALUES ('docs/my-doc.md', 'my-doc', 1);
    """)

    queries = _get_recall_queries(db)
    assert len(queries) == 1
    assert queries[0][0] == "A01"


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
def test_embed_query_returns_array_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns normalised numpy array when API call succeeds."""
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.example.com/")

    fake_vec = [0.1] * 1536
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": [{"embedding": fake_vec}]}

    with patch("requests.post", return_value=mock_resp):
        result = _embed_query("test query")

    assert result is not None
    assert isinstance(result, np.ndarray)
    assert result.shape == (1536,)
    # Should be normalised
    assert abs(np.linalg.norm(result) - 1.0) < 1e-5


@pytest.mark.unit
def test_embed_query_returns_none_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None when the API call raises an exception."""
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.example.com/")

    with patch("requests.post", side_effect=OSError("timeout")):
        result = _embed_query("test query")

    assert result is None


# ---------------------------------------------------------------------------
# _vsearch_usearch
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_vsearch_usearch_returns_empty_when_index_unavailable() -> None:
    """Returns [] when usearch index is not available."""
    with patch("kairix.core.search.hybrid.get_vector_index", return_value=None):
        result = _vsearch_usearch(np.zeros(1536, dtype=np.float32))
    assert result == []


# ---------------------------------------------------------------------------
# check_recall
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_recall_skips_when_embed_returns_none() -> None:
    """check_recall() marks queries as skipped when _embed_query returns None."""
    db = sqlite3.connect(":memory:")

    with patch("kairix.core.embed.recall_check._embed_query", return_value=None):
        result = check_recall(db=db)

    assert result["score"] == 0.0
    assert result["passed"] == 0
    assert all(d.get("skipped") for d in result["detail"])


@pytest.mark.unit
def test_check_recall_returns_structure() -> None:
    """check_recall() always returns a dict with required keys."""
    db = sqlite3.connect(":memory:")

    with patch("kairix.core.embed.recall_check._embed_query", return_value=None):
        result = check_recall(db=db)

    assert "score" in result
    assert "passed" in result
    assert "total" in result
    assert "detail" in result
    assert isinstance(result["detail"], list)


@pytest.mark.unit
def test_check_recall_counts_hit_when_gold_in_results() -> None:
    """check_recall() counts a hit when gold fragment appears in usearch results."""
    db = sqlite3.connect(":memory:")

    fake_vec = np.array([0.1] * 1536, dtype=np.float32)
    fake_files = ["04-Agent-Knowledge/builder/patterns.md"]

    with (
        patch("kairix.core.embed.recall_check._embed_query", return_value=fake_vec),
        patch("kairix.core.embed.recall_check._vsearch_usearch", return_value=fake_files),
        patch(
            "kairix.core.embed.recall_check._get_recall_queries",
            return_value=[("R1", "engineering patterns", "builder/patterns")],
        ),
    ):
        result = check_recall(db=db)

    assert result["passed"] == 1
    assert result["score"] == 1.0
    assert result["detail"][0]["hit"] is True
