"""
Tests for kairix.search.vector — vector CTE search wrapper.
Tests for kairix._azure — Azure OpenAI embedding client.

All DB and network calls are mocked. Tests cover:
  - Successful vector search returns VecResult list
  - Extension not loaded (OperationalError) → []
  - DB locked (DatabaseError) → []
  - Empty query_vec → []
  - Collection filtering
  - embed_text returns [] on missing secrets
  - embed_text returns [] on network failure
  - embed_text returns [] on API error response
"""

import sqlite3
import struct
from unittest.mock import MagicMock, patch

import pytest

from kairix._azure import embed_text
from kairix.search.vector import VecResult, vector_search, vector_search_bytes

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_query_vec(dims: int = 4) -> list[float]:
    """Create a tiny test vector."""
    return [0.1 * i for i in range(dims)]


def _pack_vec(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _make_mock_row(
    hash_seq: str = "abc_0",
    distance: float = 0.15,
    path: str = "/vault/doc.md",
    collection: str = "knowledge-shared",
    title: str = "Test Doc",
    snippet: str = "Some content here.",
) -> tuple:
    return (hash_seq, distance, path, collection, title, snippet)


# ---------------------------------------------------------------------------
# vector_search — happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_vector_search_returns_results() -> None:
    """Successful query returns list of VecResult."""
    row = _make_mock_row()
    mock_db = MagicMock(spec=sqlite3.Connection)
    mock_db.execute.return_value.fetchall.return_value = [row]

    results = vector_search(mock_db, _make_query_vec())

    assert len(results) == 1
    assert results[0]["hash_seq"] == "abc_0"
    assert results[0]["distance"] == 0.15
    assert results[0]["path"] == "/vault/doc.md"
    assert results[0]["collection"] == "knowledge-shared"


@pytest.mark.unit
def test_vector_search_passes_k_to_query() -> None:
    """k parameter is passed as the second SQL parameter."""
    mock_db = MagicMock(spec=sqlite3.Connection)
    mock_db.execute.return_value.fetchall.return_value = []

    vector_search(mock_db, _make_query_vec(), k=5)

    call_args = mock_db.execute.call_args
    params = call_args[0][1]  # positional params tuple
    assert params[1] == 5  # second param is k


@pytest.mark.unit
def test_vector_search_collection_filter() -> None:
    """collection filter adds IN clause with correct params."""
    mock_db = MagicMock(spec=sqlite3.Connection)
    mock_db.execute.return_value.fetchall.return_value = []

    vector_search(mock_db, _make_query_vec(), collections=["col-a", "col-b"])

    call_args = mock_db.execute.call_args
    sql: str = call_args[0][0]
    params = call_args[0][1]
    assert "IN" in sql
    assert "col-a" in params
    assert "col-b" in params


@pytest.mark.unit
def test_vector_search_returns_multiple_results_sorted() -> None:
    """Multiple rows are returned in the order given by the DB (sorted by distance)."""
    rows = [
        _make_mock_row(hash_seq="x_0", distance=0.1),
        _make_mock_row(hash_seq="y_0", distance=0.3),
    ]
    mock_db = MagicMock(spec=sqlite3.Connection)
    mock_db.execute.return_value.fetchall.return_value = rows

    results = vector_search(mock_db, _make_query_vec())

    assert results[0]["hash_seq"] == "x_0"
    assert results[1]["hash_seq"] == "y_0"


# ---------------------------------------------------------------------------
# vector_search — failure modes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_vector_search_returns_empty_on_operational_error() -> None:
    """sqlite3.OperationalError (extension not loaded, etc.) → []."""
    mock_db = MagicMock(spec=sqlite3.Connection)
    mock_db.execute.side_effect = sqlite3.OperationalError("no such module: vec0")

    results = vector_search(mock_db, _make_query_vec())
    assert results == []


@pytest.mark.unit
def test_vector_search_returns_empty_on_database_error() -> None:
    """sqlite3.DatabaseError (DB locked) → []."""
    mock_db = MagicMock(spec=sqlite3.Connection)
    mock_db.execute.side_effect = sqlite3.DatabaseError("database is locked")

    results = vector_search(mock_db, _make_query_vec())
    assert results == []


@pytest.mark.unit
def test_vector_search_returns_empty_for_empty_vec() -> None:
    """Empty query_vec → [] without hitting DB."""
    mock_db = MagicMock(spec=sqlite3.Connection)

    results = vector_search(mock_db, [])
    assert results == []
    mock_db.execute.assert_not_called()


@pytest.mark.unit
def test_vector_search_returns_empty_on_unexpected_error() -> None:
    """Any unexpected exception → []."""
    mock_db = MagicMock(spec=sqlite3.Connection)
    mock_db.execute.side_effect = RuntimeError("something unexpected")

    results = vector_search(mock_db, _make_query_vec())
    assert results == []


@pytest.mark.unit
def test_vector_search_bytes_returns_empty_for_empty_bytes() -> None:
    """Empty bytes input → []."""
    mock_db = MagicMock(spec=sqlite3.Connection)
    results = vector_search_bytes(mock_db, b"")
    assert results == []


# ---------------------------------------------------------------------------
# embed_text — failure modes (Azure client)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_embed_text_returns_empty_on_missing_secrets() -> None:
    """When secrets are unavailable, embed_text returns []."""
    import kairix._azure as azure_mod

    # Clear lru_cache so new mock is used
    azure_mod._get_secrets.cache_clear()
    with patch("kairix._azure._get_secrets", return_value={}):
        result = embed_text("hello world")
    assert result == []
    # Restore for other tests
    azure_mod._get_secrets.cache_clear()


@pytest.mark.unit
def test_embed_text_returns_empty_on_network_failure() -> None:
    """RequestException → []."""
    import requests

    import kairix._azure as azure_mod

    azure_mod._get_secrets.cache_clear()
    with (
        patch(
            "kairix._azure._get_secrets",
            return_value={"api_key": "k", "endpoint": "https://x", "deployment": "d"},
        ),
        patch("requests.post", side_effect=requests.exceptions.ConnectionError("network down")),
    ):
        result = embed_text("hello world")
    assert result == []
    azure_mod._get_secrets.cache_clear()


@pytest.mark.unit
def test_embed_text_returns_empty_on_api_error_response() -> None:
    """HTTP error response → []."""
    import requests

    import kairix._azure as azure_mod

    azure_mod._get_secrets.cache_clear()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
    with (
        patch(
            "kairix._azure._get_secrets",
            return_value={"api_key": "k", "endpoint": "https://x", "deployment": "d"},
        ),
        patch("requests.post", return_value=mock_resp),
    ):
        result = embed_text("hello world")
    assert result == []
    azure_mod._get_secrets.cache_clear()


@pytest.mark.unit
def test_embed_text_returns_empty_for_empty_input() -> None:
    """Empty string → [] without calling Azure."""
    with patch("requests.post") as mock_post:
        result = embed_text("")
    assert result == []
    mock_post.assert_not_called()


@pytest.mark.unit
def test_vec_result_typeddict_fields() -> None:
    """VecResult TypedDict has all expected fields."""
    r: VecResult = VecResult(
        hash_seq="x_0",
        distance=0.1,
        path="/vault/doc.md",
        collection="knowledge-shared",
        title="T",
        snippet="s",
    )
    assert r["hash_seq"] == "x_0"
    assert r["distance"] == 0.1


# ---------------------------------------------------------------------------
# _strip_frontmatter — T3-3: YAML frontmatter removal
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_strip_frontmatter():
    from kairix.search.vector import _strip_frontmatter

    text = "---\ntitle: Test Doc\nlicence: MIT\n---\n\n# Real Content\n\nBody text here."
    result = _strip_frontmatter(text)
    assert result.startswith("# Real Content")
    assert "---" not in result
    assert "title:" not in result


@pytest.mark.unit
def test_strip_frontmatter_no_frontmatter():
    from kairix.search.vector import _strip_frontmatter

    text = "Just plain text without frontmatter."
    assert _strip_frontmatter(text) == text


@pytest.mark.unit
def test_vector_search_strips_frontmatter_from_snippet() -> None:
    """vector_search should strip YAML frontmatter from snippet content."""
    row = _make_mock_row(snippet="---\ntitle: My Doc\ntags: [a, b]\n---\n\nActual content here.")
    mock_db = MagicMock(spec=sqlite3.Connection)
    mock_db.execute.return_value.fetchall.return_value = [row]

    results = vector_search(mock_db, _make_query_vec())

    assert len(results) == 1
    assert results[0]["snippet"].startswith("Actual content here.")
    assert "---" not in results[0]["snippet"]
    assert "title:" not in results[0]["snippet"]
