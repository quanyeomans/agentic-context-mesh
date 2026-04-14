"""
Tests for kairix.search.hybrid + kairix.search.budget + kairix.search.cli.

Tests cover:
  - Successful hybrid search (BM25 + vector)
  - Fallback: vector fails → BM25-only results returned
  - Parallel dispatch via ThreadPoolExecutor
  - KEYWORD intent runs full hybrid (BM25 + vector) like SEMANTIC
  - Token budget is applied and limits results
  - Search log file is written
  - CLI formats output correctly
  - CLI --json flag
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kairix.search.bm25 import BM25Result
from kairix.search.budget import DEFAULT_BUDGET, apply_budget
from kairix.search.hybrid import SearchResult, _collections_for, search
from kairix.search.intent import QueryIntent
from kairix.search.rrf import rrf
from kairix.search.vector import VecResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _bm25_result(path: str = "/vault/doc.md", score: float = 2.0) -> BM25Result:
    return BM25Result(
        file=path,
        title="Test Document",
        snippet="This is a test snippet with enough content to consume tokens.",
        score=score,
        collection="knowledge-shared",
    )


def _vec_result(path: str = "/vault/doc.md", distance: float = 0.1) -> VecResult:
    return VecResult(
        hash_seq="abc_0",
        distance=distance,
        path=path,
        collection="knowledge-shared",
        title="Test Document",
        snippet="Vector search snippet content.",
    )


# ---------------------------------------------------------------------------
# apply_budget tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_apply_budget_returns_results_within_cap() -> None:
    """Results are returned up to the token budget cap."""
    fused = rrf(
        [_bm25_result(f"/vault/{i}.md") for i in range(10)],
        [],
    )
    budgeted = apply_budget(fused, budget=100)
    total = sum(r.token_estimate for r in budgeted)
    assert total <= 100


@pytest.mark.unit
def test_apply_budget_empty_results() -> None:
    """Empty fused results → []."""
    assert apply_budget([], budget=DEFAULT_BUDGET) == []


@pytest.mark.unit
def test_apply_budget_zero_budget() -> None:
    """Zero budget → []."""
    fused = rrf([_bm25_result()], [])
    assert apply_budget(fused, budget=0) == []


@pytest.mark.unit
def test_apply_budget_all_results_fit() -> None:
    """When budget is ample, all results are included."""
    fused = rrf([_bm25_result(f"/vault/{i}.md") for i in range(3)], [])
    budgeted = apply_budget(fused, budget=DEFAULT_BUDGET)
    assert len(budgeted) == 3


@pytest.mark.unit
def test_apply_budget_assigns_l2_tier_in_phase1() -> None:
    """Phase 1: all results assigned L2 tier."""
    fused = rrf([_bm25_result()], [])
    budgeted = apply_budget(fused, budget=DEFAULT_BUDGET)
    assert all(r.tier == "L2" for r in budgeted)


# ---------------------------------------------------------------------------
# _collections_for helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collections_for_shared_only() -> None:
    """scope='shared' returns only shared collections."""
    cols = _collections_for("shape", "shared")
    assert "knowledge-shared" in cols
    assert "knowledge-shape" not in cols


@pytest.mark.unit
def test_collections_for_shared_plus_agent() -> None:
    """scope='shared+agent' includes agent-specific collections."""
    cols = _collections_for("shape", "shared+agent")
    assert "knowledge-shared" in cols
    assert "vault-agent-knowledge" in cols
    assert "shape-memory" in cols


@pytest.mark.unit
def test_collections_for_no_agent() -> None:
    """None agent → only shared collections regardless of scope."""
    from kairix.search.hybrid import _SHARED_COLLECTIONS

    cols = _collections_for(None, "shared+agent")
    assert cols == list(_SHARED_COLLECTIONS)


# ---------------------------------------------------------------------------
# search() — mock BM25 + vector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_search_returns_search_result_type() -> None:
    """search() always returns SearchResult."""
    with (
        patch("kairix.search.hybrid.bm25_search", return_value=[]),
        patch("kairix.search.hybrid._run_vector_search", return_value=[]),
        patch("kairix.search.hybrid._log_search_event"),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
    ):
        result = search("test query")
    assert isinstance(result, SearchResult)


@pytest.mark.unit
def test_search_returns_bm25_results_when_vec_fails() -> None:
    """Vector search failure → BM25-only results still returned."""
    bm25_data = [_bm25_result("/vault/a.md"), _bm25_result("/vault/b.md")]

    with (
        patch("kairix.search.hybrid.bm25_search", return_value=bm25_data),
        patch("kairix.search.hybrid._run_vector_search", return_value=[]),
        patch("kairix.search.hybrid._log_search_event"),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
    ):
        result = search("test semantic query about memory systems")

    assert len(result.results) == 2
    assert result.bm25_count == 2
    assert result.vec_count == 0


@pytest.mark.unit
def test_search_fuses_both_lists() -> None:
    """When both BM25 and vector return results, fused count >= max(b, v)."""
    bm25_data = [_bm25_result("/vault/a.md"), _bm25_result("/vault/b.md")]
    vec_data = [_vec_result("/vault/a.md"), _vec_result("/vault/c.md")]

    with (
        patch("kairix.search.hybrid.bm25_search", return_value=bm25_data),
        patch("kairix.search.hybrid._run_vector_search", return_value=vec_data),
        patch("kairix.search.hybrid._log_search_event"),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
    ):
        result = search("test semantic query about knowledge retrieval")

    # 3 unique paths: a, b, c
    assert result.fused_count == 3
    assert result.bm25_count == 2
    assert result.vec_count == 2


@pytest.mark.unit
def test_search_keyword_intent_runs_hybrid() -> None:
    """KEYWORD intent runs full hybrid (BM25 + vector), not BM25-only.

    Previously KEYWORD skipped vector search, which degraded NDCG@10 to 0.439.
    Now it runs the same BM25+vector RRF path as SEMANTIC and PROCEDURAL.
    """
    with (
        patch("kairix.search.hybrid.bm25_search", return_value=[_bm25_result()]),
        patch("kairix.search.hybrid._run_vector_search", return_value=[_vec_result()]) as mock_vec,
        patch("kairix.search.hybrid._log_search_event"),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
    ):
        result = search("SchemaVersionError")  # KEYWORD intent

    assert result.intent == QueryIntent.KEYWORD
    mock_vec.assert_called_once()  # vector IS dispatched for keyword queries


@pytest.mark.unit
def test_search_logs_event(tmp_path: Path) -> None:
    """Search log is written to the JSONL file."""
    log_file = tmp_path / "search.jsonl"

    with (
        patch("kairix.search.hybrid.SEARCH_LOG_PATH", log_file),
        patch("kairix.search.hybrid.bm25_search", return_value=[]),
        patch("kairix.search.hybrid._run_vector_search", return_value=[]),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
    ):
        search("test query about rules")

    assert log_file.exists()
    events = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert len(events) == 1
    assert "query_hash" in events[0]
    assert "intent" in events[0]
    assert "latency_ms" in events[0]


@pytest.mark.unit
def test_search_log_directory_created(tmp_path: Path) -> None:
    """Search log directory is created if it doesn't exist."""
    log_file = tmp_path / "nested" / "dir" / "search.jsonl"

    with (
        patch("kairix.search.hybrid.SEARCH_LOG_PATH", log_file),
        patch("kairix.search.hybrid.bm25_search", return_value=[]),
        patch("kairix.search.hybrid._run_vector_search", return_value=[]),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
    ):
        search("another query")

    assert log_file.exists()


@pytest.mark.unit
def test_search_records_latency() -> None:
    """latency_ms is recorded in the result."""
    with (
        patch("kairix.search.hybrid.bm25_search", return_value=[]),
        patch("kairix.search.hybrid._run_vector_search", return_value=[]),
        patch("kairix.search.hybrid._log_search_event"),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
    ):
        result = search("latency test")

    assert result.latency_ms >= 0.0


@pytest.mark.unit
def test_search_intent_is_classified() -> None:
    """Intent is classified and included in SearchResult."""
    with (
        patch("kairix.search.hybrid.bm25_search", return_value=[]),
        patch("kairix.search.hybrid._run_vector_search", return_value=[]),
        patch("kairix.search.hybrid._log_search_event"),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
    ):
        result = search("how to fetch a Key Vault secret")

    assert result.intent == QueryIntent.PROCEDURAL


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cli_formats_output(capsys: pytest.CaptureFixture) -> None:
    """CLI prints formatted search results."""
    from kairix.search.cli import main as search_cli

    mock_sr = SearchResult(
        query="test query",
        intent=QueryIntent.SEMANTIC,
        results=[],
        bm25_count=0,
        vec_count=0,
        fused_count=0,
    )

    with patch("kairix.search.cli.search", return_value=mock_sr):
        search_cli(["test query"])

    captured = capsys.readouterr()
    assert "test query" in captured.out
    assert "semantic" in captured.out


@pytest.mark.unit
def test_cli_json_flag(capsys: pytest.CaptureFixture) -> None:
    """--json flag outputs valid JSON."""
    from kairix.search.cli import main as search_cli

    mock_sr = SearchResult(
        query="test",
        intent=QueryIntent.SEMANTIC,
        results=[],
        bm25_count=0,
        vec_count=0,
        fused_count=0,
    )

    with patch("kairix.search.cli.search", return_value=mock_sr):
        search_cli(["test", "--json"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["query"] == "test"
    assert "results" in data


@pytest.mark.unit
def test_cli_agent_flag_passed_to_search(capsys: pytest.CaptureFixture) -> None:
    """--agent flag is forwarded to search()."""
    from kairix.search.cli import main as search_cli

    mock_sr = SearchResult(
        query="test",
        intent=QueryIntent.SEMANTIC,
        results=[],
        bm25_count=0,
        vec_count=0,
        fused_count=0,
    )

    with patch("kairix.search.cli.search", return_value=mock_sr) as mock_search:
        search_cli(["test", "--agent", "shape"])

    mock_search.assert_called_once_with(
        query="test",
        agent="shape",
        scope="shared+agent",
        budget=3000,
    )


# ---------------------------------------------------------------------------
# keyword hybrid dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_search_keyword_uses_both_bm25_and_vector() -> None:
    """KEYWORD intent now runs hybrid: both BM25 and vector contribute to RRF."""
    bm25_data = [_bm25_result("/vault/schema-error.md")]
    vec_data = [_vec_result("/vault/schema-overview.md")]

    with (
        patch("kairix.search.hybrid.bm25_search", return_value=bm25_data),
        patch("kairix.search.hybrid._run_vector_search", return_value=vec_data),
        patch("kairix.search.hybrid._log_search_event"),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
    ):
        result = search("SchemaVersionError")  # KEYWORD intent

    assert result.intent == QueryIntent.KEYWORD
    # Both sources contribute — fused results should include both paths
    paths = [r.result.path for r in result.results]
    assert "/vault/schema-error.md" in paths
    assert "/vault/schema-overview.md" in paths


@pytest.mark.unit
def test_search_keyword_vec_only_when_bm25_empty() -> None:
    """When BM25 returns nothing for a keyword query, vector results are still used."""
    vec_data = [_vec_result("/vault/keyword-vec.md")]

    with (
        patch("kairix.search.hybrid.bm25_search", return_value=[]),
        patch("kairix.search.hybrid._run_vector_search", return_value=vec_data),
        patch("kairix.search.hybrid._log_search_event"),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
    ):
        result = search("SchemaVersionError")  # KEYWORD intent — BM25 returns nothing

    assert result.intent == QueryIntent.KEYWORD
    assert len(result.results) == 1


@pytest.mark.unit
def test_search_result_has_fallback_used_field() -> None:
    """SearchResult always has fallback_used field."""
    with (
        patch("kairix.search.hybrid.bm25_search", return_value=[]),
        patch("kairix.search.hybrid._run_vector_search", return_value=[]),
        patch("kairix.search.hybrid._log_search_event"),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
    ):
        result = search("anything")

    assert hasattr(result, "fallback_used")
    assert isinstance(result.fallback_used, bool)


# ---------------------------------------------------------------------------
# Additional coverage: logging, DB open, temporal rewriting, keyword fallback
# ---------------------------------------------------------------------------


def test_rotate_query_log_moves_file(tmp_path: Path) -> None:
    """_rotate_query_log() moves path → path.1 and removes older rotated file."""

    import kairix.search.hybrid as hybrid_mod

    log_file = tmp_path / "queries.jsonl"
    log_file.write_text('{"q": "test"}\n')

    rotated = tmp_path / "queries.jsonl.1"
    rotated.write_text("old rotated\n")

    hybrid_mod._rotate_query_log(log_file)

    assert not log_file.exists()
    assert rotated.exists()
    assert rotated.read_text() != "old rotated\n"


def test_log_query_event_writes_when_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_log_query_event() appends to JSONL log when _LOG_QUERIES is True."""
    import kairix.search.hybrid as hybrid_mod

    log_path = tmp_path / "queries.jsonl"
    monkeypatch.setattr(hybrid_mod, "_LOG_QUERIES", True)
    monkeypatch.setattr(hybrid_mod, "_QUERY_LOG_PATH", log_path)

    hybrid_mod._log_query_event({"q": "test query", "t": 123})

    assert log_path.exists()
    import json

    event = json.loads(log_path.read_text().strip())
    assert event["q"] == "test query"


def test_log_query_event_noop_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_log_query_event() is a no-op when _LOG_QUERIES is False."""
    import kairix.search.hybrid as hybrid_mod

    log_path = tmp_path / "queries.jsonl"
    monkeypatch.setattr(hybrid_mod, "_LOG_QUERIES", False)
    monkeypatch.setattr(hybrid_mod, "_QUERY_LOG_PATH", log_path)

    hybrid_mod._log_query_event({"q": "test"})
    assert not log_path.exists()


def test_log_query_event_rotates_large_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_log_query_event() rotates the file when it exceeds the size threshold."""
    import kairix.search.hybrid as hybrid_mod

    log_path = tmp_path / "queries.jsonl"
    log_path.write_text("x" * 100)

    monkeypatch.setattr(hybrid_mod, "_LOG_QUERIES", True)
    monkeypatch.setattr(hybrid_mod, "_QUERY_LOG_PATH", log_path)
    monkeypatch.setattr(hybrid_mod, "_QUERY_LOG_MAX_BYTES", 10)  # Very small threshold

    hybrid_mod._log_query_event({"q": "trigger rotation"})

    rotated = Path(str(log_path) + ".1")
    assert rotated.exists()


def test_open_vec_db_returns_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """_open_vec_db() returns None when QMD DB is unavailable."""
    from kairix.search import hybrid as hybrid_mod

    monkeypatch.setattr(hybrid_mod, "get_qmd_db_path", lambda: (_ for _ in ()).throw(FileNotFoundError("no db")))
    result = hybrid_mod._open_vec_db()
    assert result is None




def test_search_temporal_intent_runs_rewriting(monkeypatch: pytest.MonkeyPatch) -> None:
    """search() with TEMPORAL intent calls temporal rewriting (mocked)."""
    from kairix.search.bm25 import BM25Result
    from kairix.search.hybrid import search
    from kairix.search.intent import QueryIntent

    bm25_items = [BM25Result(path="memory/2026-01-01.md", snippet="Session from last week", score=0.9)]

    with (
        patch("kairix.search.intent.classify", return_value=QueryIntent.TEMPORAL),
        patch("kairix.search.bm25.bm25_search", return_value=bm25_items),
        patch("kairix.search.hybrid._open_vec_db", return_value=None),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
        patch(
            "kairix.temporal.rewriter.rewrite_temporal_query",
            return_value="recent session logs",
        ),
        patch(
            "kairix.temporal.rewriter.extract_time_window",
            return_value=(None, None),
        ),
        patch(
            "kairix.temporal.index.query_temporal_chunks",
            return_value=[],
        ),
    ):
        result = search("what happened last week?", agent="builder")

    assert result is not None
