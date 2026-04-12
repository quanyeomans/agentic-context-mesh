"""
Tests for TMP-2: date-range path filtering in BM25 and vector search.

Covers:
  - _path_from_file_uri helper (bm25.py)
  - bm25_search date_filter_paths post-filter
  - vector_search_bytes date_filter_paths post-filter
  - hybrid.search TEMPORAL intent passes date_filter to both searches
  - Graceful degradation when date_filter_paths is empty or None
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kairix.search.bm25 import BM25Result, _path_from_file_uri, bm25_search
from kairix.search.vector import VecResult, vector_search_bytes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bm25_result(path: str, collection: str = "vault-areas") -> BM25Result:
    return BM25Result(
        file=f"qmd://{collection}/{path}",
        title="Test",
        snippet="Test snippet",
        score=1.0,
        collection=collection,
    )


def _make_vec_result(path: str) -> VecResult:
    return VecResult(
        hash_seq="abc_0",
        distance=0.1,
        path=path,
        collection="vault-areas",
        title="Test",
        snippet="Test snippet",
    )


# ---------------------------------------------------------------------------
# _path_from_file_uri
# ---------------------------------------------------------------------------


def test_path_from_file_uri_standard() -> None:
    """Standard qmd:// URI returns vault-relative path."""
    uri = "qmd://vault-areas/02-Areas/00-Clients/AcmeCorp/overview.md"
    assert _path_from_file_uri(uri) == "02-Areas/00-Clients/AcmeCorp/overview.md"


def test_path_from_file_uri_single_segment_path() -> None:
    """Collection-only path (no slash after collection) returns after-collection string."""
    uri = "qmd://vault-areas/file.md"
    assert _path_from_file_uri(uri) == "file.md"


def test_path_from_file_uri_non_qmd_passthrough() -> None:
    """Non-qmd:// URIs are returned unchanged."""
    uri = "/data/obsidian-vault/file.md"
    assert _path_from_file_uri(uri) == "/data/obsidian-vault/file.md"


def test_path_from_file_uri_no_scheme() -> None:
    """URI without :// is returned unchanged."""
    assert _path_from_file_uri("bare-path.md") == "bare-path.md"


# ---------------------------------------------------------------------------
# bm25_search date_filter_paths
# ---------------------------------------------------------------------------


def test_bm25_date_filter_none_no_filtering() -> None:
    """date_filter_paths=None → results not filtered."""
    r1 = _make_bm25_result("doc-a.md")
    r2 = _make_bm25_result("doc-b.md")

    with (
        patch("kairix.search.bm25.get_qmd_binary", return_value="/usr/bin/qmd"),
        patch("subprocess.run") as mock_run,
        patch("kairix.search.bm25._bm25_direct_db", return_value=[]),
    ):
        import json

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "file": r1["file"],
                        "title": r1["title"],
                        "snippet": r1["snippet"],
                        "score": r1["score"],
                        "collection": r1["collection"],
                    },
                    {
                        "file": r2["file"],
                        "title": r2["title"],
                        "snippet": r2["snippet"],
                        "score": r2["score"],
                        "collection": r2["collection"],
                    },
                ]
            ),
            stderr="",
        )
        results = bm25_search("test query", date_filter_paths=None)

    assert len(results) == 2


def test_bm25_date_filter_empty_no_filtering() -> None:
    """date_filter_paths=frozenset() → results not filtered (empty = no-filter)."""
    r1 = _make_bm25_result("doc-a.md")

    with (
        patch("kairix.search.bm25.get_qmd_binary", return_value="/usr/bin/qmd"),
        patch("subprocess.run") as mock_run,
        patch("kairix.search.bm25._bm25_direct_db", return_value=[]),
    ):
        import json

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "file": r1["file"],
                        "title": r1["title"],
                        "snippet": r1["snippet"],
                        "score": r1["score"],
                        "collection": r1["collection"],
                    },
                ]
            ),
            stderr="",
        )
        results = bm25_search("test query", date_filter_paths=frozenset())

    assert len(results) == 1


def test_bm25_date_filter_applied() -> None:
    """date_filter_paths with one path → only matching result returned."""
    r1 = _make_bm25_result("02-Areas/good.md")
    r2 = _make_bm25_result("02-Areas/bad.md")

    with (
        patch("kairix.search.bm25.get_qmd_binary", return_value="/usr/bin/qmd"),
        patch("subprocess.run") as mock_run,
        patch("kairix.search.bm25._bm25_direct_db", return_value=[]),
    ):
        import json

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                [
                    {"file": r1["file"], "title": "", "snippet": "", "score": 1.0, "collection": "vault-areas"},
                    {"file": r2["file"], "title": "", "snippet": "", "score": 0.5, "collection": "vault-areas"},
                ]
            ),
            stderr="",
        )
        results = bm25_search("test query", date_filter_paths=frozenset({"02-Areas/good.md"}))

    assert len(results) == 1
    assert _path_from_file_uri(results[0]["file"]) == "02-Areas/good.md"


# ---------------------------------------------------------------------------
# vector_search_bytes date_filter_paths
# ---------------------------------------------------------------------------


def test_vector_date_filter_none_passthrough() -> None:
    """date_filter_paths=None → vector results not filtered."""
    mock_db = MagicMock()
    r1 = _make_vec_result("doc-a.md")
    r2 = _make_vec_result("doc-b.md")

    with patch("kairix.search.vector._vsearch_with_bytes", return_value=[r1, r2]):
        results = vector_search_bytes(mock_db, b"\x00" * 4, date_filter_paths=None)

    assert len(results) == 2


def test_vector_date_filter_empty_passthrough() -> None:
    """date_filter_paths=frozenset() → results not filtered."""
    mock_db = MagicMock()
    r1 = _make_vec_result("doc-a.md")

    with patch("kairix.search.vector._vsearch_with_bytes", return_value=[r1]):
        results = vector_search_bytes(mock_db, b"\x00" * 4, date_filter_paths=frozenset())

    assert len(results) == 1


def test_vector_date_filter_applied() -> None:
    """date_filter_paths with one path → only matching result returned."""
    mock_db = MagicMock()
    r1 = _make_vec_result("02-Areas/good.md")
    r2 = _make_vec_result("02-Areas/bad.md")

    with patch("kairix.search.vector._vsearch_with_bytes", return_value=[r1, r2]):
        results = vector_search_bytes(mock_db, b"\x00" * 4, date_filter_paths=frozenset({"02-Areas/good.md"}))

    assert len(results) == 1
    assert results[0]["path"] == "02-Areas/good.md"
