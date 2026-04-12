"""
Tests for kairix.search.bm25 — BM25 subprocess wrapper.

All subprocess calls are mocked. Tests cover:
  - Successful parse of --json output
  - qmd not found (FileNotFoundError)
  - Subprocess timeout
  - Non-zero exit code
  - Empty output
  - Malformed JSON
  - Unexpected JSON shape
  - OSError launching subprocess
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from kairix.search.bm25 import BM25Result, _normalise_fts_query, _parse_bm25_output, bm25_search

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_QMD_OUTPUT: list[dict] = [
    {
        "file": "/vault/agent-knowledge/shared/facts.md",
        "title": "Shared Facts",
        "snippet": "The VM has 4 vCPUs and 16 GB RAM.",
        "score": 2.45,
        "collection": "knowledge-shared",
    },
    {
        "file": "/vault/agent-knowledge/builder/patterns.md",
        "title": "Builder Patterns",
        "snippet": "Use trash instead of rm for safety.",
        "score": 1.87,
        "collection": "knowledge-builder",
    },
]


def _make_completed_process(
    stdout: str = "",
    returncode: int = 0,
    stderr: str = "",
) -> MagicMock:
    mock = MagicMock(spec=subprocess.CompletedProcess)
    mock.stdout = stdout
    mock.returncode = returncode
    mock.stderr = stderr
    return mock


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bm25_search_returns_results_on_success() -> None:
    """Successful qmd call returns parsed BM25Result list."""
    with (
        patch("kairix.search.bm25.get_qmd_binary", return_value="/usr/bin/qmd"),
        patch("subprocess.run", return_value=_make_completed_process(stdout=json.dumps(VALID_QMD_OUTPUT))),
    ):
        results = bm25_search("vault memory facts")

    assert len(results) == 2
    assert results[0]["file"] == VALID_QMD_OUTPUT[0]["file"]
    assert results[0]["score"] == 2.45
    assert results[0]["collection"] == "knowledge-shared"


@pytest.mark.unit
def test_bm25_search_passes_limit_to_cmd() -> None:
    """limit parameter is passed as --limit to qmd."""
    with (
        patch("kairix.search.bm25.get_qmd_binary", return_value="/usr/bin/qmd"),
        patch("subprocess.run", return_value=_make_completed_process(stdout=json.dumps([]))) as mock_run,
    ):
        bm25_search("test query", limit=5)

    cmd = mock_run.call_args[0][0]
    assert "--limit" in cmd
    assert "5" in cmd


@pytest.mark.unit
def test_bm25_search_passes_collections_to_cmd() -> None:
    """collections parameter is passed as --collection flags."""
    with (
        patch("kairix.search.bm25.get_qmd_binary", return_value="/usr/bin/qmd"),
        patch("subprocess.run", return_value=_make_completed_process(stdout=json.dumps([]))) as mock_run,
    ):
        bm25_search("test", collections=["knowledge-shared", "knowledge-builder"])

    cmd = mock_run.call_args[0][0]
    assert cmd.count("--collection") == 2
    assert "knowledge-shared" in cmd
    assert "knowledge-builder" in cmd


# ---------------------------------------------------------------------------
# Failure modes — all must return []
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bm25_search_returns_empty_when_binary_not_found() -> None:
    """FileNotFoundError from get_qmd_binary → []."""
    with patch("kairix.search.bm25.get_qmd_binary", side_effect=FileNotFoundError("not found")):
        results = bm25_search("query")
    assert results == []


@pytest.mark.unit
def test_bm25_search_returns_empty_on_timeout() -> None:
    """TimeoutExpired → []."""
    with (
        patch("kairix.search.bm25.get_qmd_binary", return_value="/usr/bin/qmd"),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="qmd", timeout=5)),
    ):
        results = bm25_search("query")
    assert results == []


@pytest.mark.unit
def test_bm25_search_returns_empty_on_nonzero_exit() -> None:
    """Non-zero returncode → []."""
    with (
        patch("kairix.search.bm25.get_qmd_binary", return_value="/usr/bin/qmd"),
        patch("subprocess.run", return_value=_make_completed_process(returncode=1, stderr="error")),
    ):
        results = bm25_search("query")
    assert results == []


@pytest.mark.unit
def test_bm25_search_returns_empty_on_empty_output() -> None:
    """Empty stdout → []."""
    with (
        patch("kairix.search.bm25.get_qmd_binary", return_value="/usr/bin/qmd"),
        patch("subprocess.run", return_value=_make_completed_process(stdout="")),
    ):
        results = bm25_search("query")
    assert results == []


@pytest.mark.unit
def test_bm25_search_returns_empty_on_oserror() -> None:
    """OSError launching subprocess → []."""
    with (
        patch("kairix.search.bm25.get_qmd_binary", return_value="/usr/bin/qmd"),
        patch("subprocess.run", side_effect=OSError("permission denied")),
    ):
        results = bm25_search("query")
    assert results == []


@pytest.mark.unit
def test_bm25_search_returns_empty_on_malformed_json() -> None:
    """Invalid JSON → []."""
    with (
        patch("kairix.search.bm25.get_qmd_binary", return_value="/usr/bin/qmd"),
        patch("subprocess.run", return_value=_make_completed_process(stdout="not json {")),
    ):
        results = bm25_search("query")
    assert results == []


@pytest.mark.unit
def test_bm25_search_returns_empty_for_empty_query() -> None:
    """Empty query string → [] without calling subprocess."""
    with patch("subprocess.run") as mock_run:
        results = bm25_search("")
    assert results == []
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# _parse_bm25_output unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_bm25_output_valid_list() -> None:
    """Valid list JSON is parsed correctly."""
    raw = json.dumps(VALID_QMD_OUTPUT)
    results = _parse_bm25_output(raw)
    assert len(results) == 2
    assert results[0]["title"] == "Shared Facts"


@pytest.mark.unit
def test_parse_bm25_output_dict_with_results_key() -> None:
    """Dict with 'results' key is unwrapped."""
    raw = json.dumps({"results": VALID_QMD_OUTPUT})
    results = _parse_bm25_output(raw)
    assert len(results) == 2


@pytest.mark.unit
def test_parse_bm25_output_empty_list() -> None:
    """Empty JSON list → []."""
    assert _parse_bm25_output("[]") == []


@pytest.mark.unit
def test_parse_bm25_output_unknown_dict_structure() -> None:
    """Dict without 'results' key → []."""
    assert _parse_bm25_output('{"error": "no results"}') == []


@pytest.mark.unit
def test_parse_bm25_output_uses_path_fallback() -> None:
    """'path' key is accepted when 'file' is absent."""
    raw = json.dumps([{"path": "/vault/doc.md", "title": "Doc", "score": 1.0, "collection": "c"}])
    results = _parse_bm25_output(raw)
    assert results[0]["file"] == "/vault/doc.md"


@pytest.mark.unit
def test_bm25_result_typeddict_fields() -> None:
    """BM25Result TypedDict has the expected fields."""
    r: BM25Result = BM25Result(
        file="/f.md",
        title="T",
        snippet="s",
        score=1.0,
        collection="c",
    )
    assert r["file"] == "/f.md"
    assert r["score"] == 1.0


# ---------------------------------------------------------------------------
# Tests for _normalise_fts_query
# ---------------------------------------------------------------------------


class TestNormaliseFtsQuery:
    def test_removes_stop_words(self) -> None:
        result = _normalise_fts_query("what do we know about Alice Chen")
        assert "what" not in result
        assert "do" not in result
        assert "we" not in result
        assert "know" not in result
        assert "about" not in result
        assert "alice" in result.lower()
        assert "chen" in result.lower()

    def test_replaces_hyphens_with_spaces(self) -> None:
        result = _normalise_fts_query("project-x")
        assert "-" not in result
        assert "project" in result

    def test_filters_short_tokens(self) -> None:
        # Single-character tokens should be removed
        result = _normalise_fts_query("a b c builder")
        assert result.strip() == "builder"

    def test_preserves_meaningful_terms(self) -> None:
        result = _normalise_fts_query("tell me about Acme Corp as an organisation")
        assert "acme" in result
        assert "corp" in result
        assert "organisation" in result

    def test_empty_query_returns_empty(self) -> None:
        result = _normalise_fts_query("")
        assert result == ""

    def test_all_stop_words_returns_empty(self) -> None:
        result = _normalise_fts_query("what is the a an")
        assert result == ""

    def test_platform(self) -> None:
        result = _normalise_fts_query("what does the platform do")
        tokens = result.split()
        assert "platform" in tokens
        assert "who" not in tokens
        assert "for" not in tokens
