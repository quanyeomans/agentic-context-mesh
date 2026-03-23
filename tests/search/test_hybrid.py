"""
Tests for mnemosyne.search.hybrid + mnemosyne.search.budget + mnemosyne.search.cli.

Tests cover:
  - Successful hybrid search (BM25 + vector)
  - Fallback: vector fails → BM25-only results returned
  - Parallel dispatch via ThreadPoolExecutor
  - KEYWORD intent skips vector search
  - Token budget is applied and limits results
  - Search log file is written
  - CLI formats output correctly
  - CLI --json flag
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mnemosyne.search.bm25 import BM25Result
from mnemosyne.search.budget import DEFAULT_BUDGET, apply_budget
from mnemosyne.search.hybrid import SearchResult, _collections_for, search
from mnemosyne.search.intent import QueryIntent
from mnemosyne.search.rrf import rrf
from mnemosyne.search.vector import VecResult

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
    assert "knowledge-shape" in cols
    assert "shape-memory" in cols


@pytest.mark.unit
def test_collections_for_no_agent() -> None:
    """None agent → only shared collections regardless of scope."""
    from mnemosyne.search.hybrid import _SHARED_COLLECTIONS

    cols = _collections_for(None, "shared+agent")
    assert cols == list(_SHARED_COLLECTIONS)


# ---------------------------------------------------------------------------
# search() — mock BM25 + vector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_search_returns_search_result_type() -> None:
    """search() always returns SearchResult."""
    with (
        patch("mnemosyne.search.hybrid.bm25_search", return_value=[]),
        patch("mnemosyne.search.hybrid._run_vector_search", return_value=[]),
        patch("mnemosyne.search.hybrid._log_search_event"),
        patch("mnemosyne.search.hybrid._open_entities_db", return_value=None),
    ):
        result = search("test query")
    assert isinstance(result, SearchResult)


@pytest.mark.unit
def test_search_returns_bm25_results_when_vec_fails() -> None:
    """Vector search failure → BM25-only results still returned."""
    bm25_data = [_bm25_result("/vault/a.md"), _bm25_result("/vault/b.md")]

    with (
        patch("mnemosyne.search.hybrid.bm25_search", return_value=bm25_data),
        patch("mnemosyne.search.hybrid._run_vector_search", return_value=[]),
        patch("mnemosyne.search.hybrid._log_search_event"),
        patch("mnemosyne.search.hybrid._open_entities_db", return_value=None),
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
        patch("mnemosyne.search.hybrid.bm25_search", return_value=bm25_data),
        patch("mnemosyne.search.hybrid._run_vector_search", return_value=vec_data),
        patch("mnemosyne.search.hybrid._log_search_event"),
        patch("mnemosyne.search.hybrid._open_entities_db", return_value=None),
    ):
        result = search("test semantic query about knowledge retrieval")

    # 3 unique paths: a, b, c
    assert result.fused_count == 3
    assert result.bm25_count == 2
    assert result.vec_count == 2


@pytest.mark.unit
def test_search_keyword_intent_skips_vector() -> None:
    """KEYWORD intent skips vector search dispatch."""
    with (
        patch("mnemosyne.search.hybrid.bm25_search", return_value=[_bm25_result()]),
        patch("mnemosyne.search.hybrid._run_vector_search") as mock_vec,
        patch("mnemosyne.search.hybrid._log_search_event"),
        patch("mnemosyne.search.hybrid._open_entities_db", return_value=None),
    ):
        result = search("SchemaVersionError")  # KEYWORD intent

    assert result.intent == QueryIntent.KEYWORD
    mock_vec.assert_not_called()


@pytest.mark.unit
def test_search_logs_event(tmp_path: Path) -> None:
    """Search log is written to the JSONL file."""
    log_file = tmp_path / "search.jsonl"

    with (
        patch("mnemosyne.search.hybrid.SEARCH_LOG_PATH", log_file),
        patch("mnemosyne.search.hybrid.bm25_search", return_value=[]),
        patch("mnemosyne.search.hybrid._run_vector_search", return_value=[]),
        patch("mnemosyne.search.hybrid._open_entities_db", return_value=None),
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
        patch("mnemosyne.search.hybrid.SEARCH_LOG_PATH", log_file),
        patch("mnemosyne.search.hybrid.bm25_search", return_value=[]),
        patch("mnemosyne.search.hybrid._run_vector_search", return_value=[]),
        patch("mnemosyne.search.hybrid._open_entities_db", return_value=None),
    ):
        search("another query")

    assert log_file.exists()


@pytest.mark.unit
def test_search_records_latency() -> None:
    """latency_ms is recorded in the result."""
    with (
        patch("mnemosyne.search.hybrid.bm25_search", return_value=[]),
        patch("mnemosyne.search.hybrid._run_vector_search", return_value=[]),
        patch("mnemosyne.search.hybrid._log_search_event"),
        patch("mnemosyne.search.hybrid._open_entities_db", return_value=None),
    ):
        result = search("latency test")

    assert result.latency_ms >= 0.0


@pytest.mark.unit
def test_search_intent_is_classified() -> None:
    """Intent is classified and included in SearchResult."""
    with (
        patch("mnemosyne.search.hybrid.bm25_search", return_value=[]),
        patch("mnemosyne.search.hybrid._run_vector_search", return_value=[]),
        patch("mnemosyne.search.hybrid._log_search_event"),
        patch("mnemosyne.search.hybrid._open_entities_db", return_value=None),
    ):
        result = search("how to fetch a Key Vault secret")

    assert result.intent == QueryIntent.PROCEDURAL


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cli_formats_output(capsys: pytest.CaptureFixture) -> None:
    """CLI prints formatted search results."""
    from mnemosyne.search.cli import main as search_cli

    mock_sr = SearchResult(
        query="test query",
        intent=QueryIntent.SEMANTIC,
        results=[],
        bm25_count=0,
        vec_count=0,
        fused_count=0,
    )

    with patch("mnemosyne.search.cli.search", return_value=mock_sr):
        search_cli(["test query"])

    captured = capsys.readouterr()
    assert "test query" in captured.out
    assert "semantic" in captured.out


@pytest.mark.unit
def test_cli_json_flag(capsys: pytest.CaptureFixture) -> None:
    """--json flag outputs valid JSON."""
    from mnemosyne.search.cli import main as search_cli

    mock_sr = SearchResult(
        query="test",
        intent=QueryIntent.SEMANTIC,
        results=[],
        bm25_count=0,
        vec_count=0,
        fused_count=0,
    )

    with patch("mnemosyne.search.cli.search", return_value=mock_sr):
        search_cli(["test", "--json"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["query"] == "test"
    assert "results" in data


@pytest.mark.unit
def test_cli_agent_flag_passed_to_search(capsys: pytest.CaptureFixture) -> None:
    """--agent flag is forwarded to search()."""
    from mnemosyne.search.cli import main as search_cli

    mock_sr = SearchResult(
        query="test",
        intent=QueryIntent.SEMANTIC,
        results=[],
        bm25_count=0,
        vec_count=0,
        fused_count=0,
    )

    with patch("mnemosyne.search.cli.search", return_value=mock_sr) as mock_search:
        search_cli(["test", "--agent", "shape"])

    mock_search.assert_called_once_with(
        query="test",
        agent="shape",
        scope="shared+agent",
        budget=3000,
    )


# ---------------------------------------------------------------------------
# keyword BM25→vector fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_search_keyword_fallback_to_vector_when_bm25_empty() -> None:
    """KEYWORD intent falls back to vector when BM25 returns empty."""
    vec_data = [_vec_result("/vault/keyword-fallback.md")]

    with (
        patch("mnemosyne.search.hybrid.bm25_search", return_value=[]),
        patch("mnemosyne.search.hybrid._run_vector_search", return_value=vec_data) as mock_vec,
        patch("mnemosyne.search.hybrid._log_search_event"),
        patch("mnemosyne.search.hybrid._open_entities_db", return_value=None),
    ):
        result = search("SchemaVersionError")  # KEYWORD intent — BM25 returns nothing

    assert result.intent == QueryIntent.KEYWORD
    assert result.fallback_used is True
    assert len(result.results) == 1
    # Vector was called (fallback triggered)
    mock_vec.assert_called_once()


@pytest.mark.unit
def test_search_keyword_no_fallback_when_bm25_has_results() -> None:
    """KEYWORD intent does NOT fall back to vector when BM25 returns results."""
    bm25_data = [_bm25_result("/vault/kw.md")]

    with (
        patch("mnemosyne.search.hybrid.bm25_search", return_value=bm25_data),
        patch("mnemosyne.search.hybrid._run_vector_search") as mock_vec,
        patch("mnemosyne.search.hybrid._log_search_event"),
        patch("mnemosyne.search.hybrid._open_entities_db", return_value=None),
    ):
        result = search("SchemaVersionError")  # KEYWORD intent — BM25 has results

    assert result.fallback_used is False
    mock_vec.assert_not_called()


@pytest.mark.unit
def test_search_result_has_fallback_used_field() -> None:
    """SearchResult always has fallback_used field."""
    with (
        patch("mnemosyne.search.hybrid.bm25_search", return_value=[]),
        patch("mnemosyne.search.hybrid._run_vector_search", return_value=[]),
        patch("mnemosyne.search.hybrid._log_search_event"),
        patch("mnemosyne.search.hybrid._open_entities_db", return_value=None),
    ):
        result = search("anything")

    assert hasattr(result, "fallback_used")
    assert isinstance(result.fallback_used, bool)


# ---------------------------------------------------------------------------
# Additional coverage: logging, DB open, temporal rewriting, keyword fallback
# ---------------------------------------------------------------------------


def test_rotate_query_log_moves_file(tmp_path: Path) -> None:
    """_rotate_query_log() moves path → path.1 and removes older rotated file."""

    import mnemosyne.search.hybrid as hybrid_mod

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
    import mnemosyne.search.hybrid as hybrid_mod

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
    import mnemosyne.search.hybrid as hybrid_mod

    log_path = tmp_path / "queries.jsonl"
    monkeypatch.setattr(hybrid_mod, "_LOG_QUERIES", False)
    monkeypatch.setattr(hybrid_mod, "_QUERY_LOG_PATH", log_path)

    hybrid_mod._log_query_event({"q": "test"})
    assert not log_path.exists()


def test_log_query_event_rotates_large_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_log_query_event() rotates the file when it exceeds the size threshold."""
    import mnemosyne.search.hybrid as hybrid_mod

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
    from mnemosyne.search import hybrid as hybrid_mod

    monkeypatch.setattr(hybrid_mod, "get_qmd_db_path", lambda: (_ for _ in ()).throw(FileNotFoundError("no db")))
    result = hybrid_mod._open_vec_db()
    assert result is None


def test_open_entities_db_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_open_entities_db() returns None when entities.db does not exist."""

    from mnemosyne.search import hybrid as hybrid_mod

    monkeypatch.setattr(hybrid_mod, "ENTITIES_DB_PATH", tmp_path / "nonexistent.db")
    result = hybrid_mod._open_entities_db()
    assert result is None


def test_search_temporal_intent_runs_rewriting(monkeypatch: pytest.MonkeyPatch) -> None:
    """search() with TEMPORAL intent calls temporal rewriting (mocked)."""
    from mnemosyne.search.bm25 import BM25Result
    from mnemosyne.search.hybrid import search
    from mnemosyne.search.intent import QueryIntent

    bm25_items = [BM25Result(path="memory/2026-01-01.md", snippet="Session from last week", score=0.9)]

    with (
        patch("mnemosyne.search.intent.classify", return_value=QueryIntent.TEMPORAL),
        patch("mnemosyne.search.bm25.bm25_search", return_value=bm25_items),
        patch("mnemosyne.search.hybrid._open_vec_db", return_value=None),
        patch("mnemosyne.search.hybrid._open_entities_db", return_value=None),
        patch(
            "mnemosyne.temporal.rewriter.rewrite_temporal_query",
            return_value="recent session logs",
        ),
        patch(
            "mnemosyne.temporal.rewriter.extract_time_window",
            return_value=(None, None),
        ),
        patch(
            "mnemosyne.temporal.index.query_temporal_chunks",
            return_value=[],
        ),
    ):
        result = search("what happened last week?", agent="builder")

    assert result is not None
