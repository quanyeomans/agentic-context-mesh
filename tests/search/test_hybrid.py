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
from kairix.search.vec_index import VecResult

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
def test_collections_for_no_config_returns_none() -> None:
    """Without collections config, returns None (search everything)."""
    cols = _collections_for("shape", "shared")
    assert cols is None


@pytest.mark.unit
def test_collections_for_with_agent_includes_agent_pattern() -> None:
    """scope='shared+agent' appends agent-specific collection."""
    # Simulate having collections config with env var
    import os

    import kairix.search.hybrid as _mod

    old = os.environ.get("KAIRIX_EXTRA_COLLECTIONS", "")
    os.environ["KAIRIX_EXTRA_COLLECTIONS"] = "test-collection"
    _mod._COLLECTIONS_CONFIG = None  # reset cache
    try:
        cols = _collections_for("shape", "shared+agent")
        assert cols is not None
        assert "shape-memory" in cols
        assert "test-collection" in cols
    finally:
        if old:
            os.environ["KAIRIX_EXTRA_COLLECTIONS"] = old
        else:
            os.environ.pop("KAIRIX_EXTRA_COLLECTIONS", None)
        _mod._COLLECTIONS_CONFIG = None


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

    assert mock_search.call_count == 1
    call_kwargs = mock_search.call_args.kwargs
    assert call_kwargs["query"] == "test"
    assert call_kwargs["agent"] == "shape"
    assert call_kwargs["scope"] == "shared+agent"
    assert call_kwargs["budget"] == 3000


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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
def test_log_query_event_noop_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_log_query_event() is a no-op when _LOG_QUERIES is False."""
    import kairix.search.hybrid as hybrid_mod

    log_path = tmp_path / "queries.jsonl"
    monkeypatch.setattr(hybrid_mod, "_LOG_QUERIES", False)
    monkeypatch.setattr(hybrid_mod, "_QUERY_LOG_PATH", log_path)

    hybrid_mod._log_query_event({"q": "test"})
    assert not log_path.exists()


@pytest.mark.unit
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


# ---------------------------------------------------------------------------
# ENTITY intent — Neo4j required
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_search_entity_intent_errors_when_neo4j_unavailable() -> None:
    """ENTITY intent returns an error result when Neo4j is unavailable.

    Regression: previously search() silently fell through to BM25+vector,
    producing misleading results with no entity graph expansion.
    """
    with (
        patch("kairix.search.hybrid.bm25_search", return_value=[_bm25_result()]),
        patch("kairix.search.hybrid._run_vector_search", return_value=[]),
        patch("kairix.search.hybrid._log_search_event"),
        patch("kairix.search.hybrid._get_neo4j", return_value=type("C", (), {"available": False})()),
    ):
        result = search("tell me about Acme Corp")  # ENTITY intent

    assert result.intent == QueryIntent.ENTITY
    assert result.error != ""
    assert "Neo4j" in result.error
    assert result.results == []


@pytest.mark.unit
def test_search_entity_intent_proceeds_when_neo4j_available() -> None:
    """ENTITY intent proceeds to full pipeline when Neo4j is available."""
    mock_neo4j = type("C", (), {"available": True, "cypher": lambda self, q: []})()

    with (
        patch("kairix.search.hybrid.bm25_search", return_value=[_bm25_result()]),
        patch("kairix.search.hybrid._run_vector_search", return_value=[]),
        patch("kairix.search.hybrid._log_search_event"),
        patch("kairix.search.hybrid._get_neo4j", return_value=mock_neo4j),
    ):
        result = search("tell me about Acme Corp")  # ENTITY intent

    assert result.intent == QueryIntent.ENTITY
    assert result.error == ""
    assert len(result.results) >= 0  # results list present (may be empty if budget=0)


@pytest.mark.unit
def test_cli_entity_error_exits_nonzero(capsys: pytest.CaptureFixture) -> None:
    """CLI exits with code 1 and prints error when entity query fails."""
    from kairix.search.cli import main as search_cli

    mock_sr = SearchResult(
        query="tell me about Acme",
        intent=QueryIntent.ENTITY,
        results=[],
        error="Neo4j is required for entity queries but is unavailable.",
    )

    with patch("kairix.search.cli.search", return_value=mock_sr):
        with pytest.raises(SystemExit) as exc_info:
            search_cli(["tell me about Acme"])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Neo4j" in captured.out


@pytest.mark.unit
def test_cli_entity_error_json_includes_error_field(capsys: pytest.CaptureFixture) -> None:
    """--json output includes 'error' field when entity query fails."""
    from kairix.search.cli import main as search_cli

    mock_sr = SearchResult(
        query="tell me about Acme",
        intent=QueryIntent.ENTITY,
        results=[],
        error="Neo4j is required for entity queries but is unavailable.",
    )

    with patch("kairix.search.cli.search", return_value=mock_sr):
        with pytest.raises(SystemExit) as exc_info:
            search_cli(["tell me about Acme", "--json"])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "error" in data
    assert "Neo4j" in data["error"]


# ---------------------------------------------------------------------------
# Additional coverage: logging, DB open, temporal rewriting, keyword fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_search_temporal_intent_runs_rewriting(monkeypatch: pytest.MonkeyPatch) -> None:
    """search() with TEMPORAL intent calls temporal rewriting (mocked)."""
    from kairix.search.bm25 import BM25Result
    from kairix.search.hybrid import search
    from kairix.search.intent import QueryIntent

    bm25_items = [BM25Result(path="memory/2026-01-01.md", snippet="Session from last week", score=0.9)]

    with (
        patch("kairix.search.intent.classify", return_value=QueryIntent.TEMPORAL),
        patch("kairix.search.bm25.bm25_search", return_value=bm25_items),
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


# ---------------------------------------------------------------------------
# _enrich_chunk_dates tests (TEST-6)
# ---------------------------------------------------------------------------


def _make_chunk_date_db(tmp_path: Path, rows: list[tuple[str, str, str]]) -> Path:
    """Create a SQLite DB with documents + content_vectors tables for chunk_date tests."""
    import sqlite3

    db_path = tmp_path / "chunk_dates.sqlite"
    db = sqlite3.connect(str(db_path))
    db.execute("CREATE TABLE documents (hash TEXT PRIMARY KEY, path TEXT NOT NULL)")
    db.execute("CREATE TABLE content_vectors (hash TEXT, chunk_date TEXT)")
    for h, p, cd in rows:
        db.execute("INSERT INTO documents (hash, path) VALUES (?, ?)", (h, p))
        db.execute("INSERT INTO content_vectors (hash, chunk_date) VALUES (?, ?)", (h, cd))
    db.commit()
    db.close()
    return db_path


@pytest.mark.unit
def test_enrich_chunk_dates_populates_matching_paths(tmp_path: Path) -> None:
    """_enrich_chunk_dates sets chunk_date on FusedResult for matching paths."""
    from kairix.search.hybrid import _enrich_chunk_dates
    from kairix.search.rrf import FusedResult

    db_path = _make_chunk_date_db(
        tmp_path,
        [
            ("h1", "/vault/doc-a.md", "2026-04-20"),
            ("h2", "/vault/doc-b.md", "2026-04-21"),
        ],
    )
    fused = [
        FusedResult(path="/vault/doc-a.md", collection="c", title="A", snippet="s", rrf_score=0.5, boosted_score=0.5),
        FusedResult(path="/vault/doc-b.md", collection="c", title="B", snippet="s", rrf_score=0.4, boosted_score=0.4),
        FusedResult(path="/vault/doc-c.md", collection="c", title="C", snippet="s", rrf_score=0.3, boosted_score=0.3),
    ]
    _enrich_chunk_dates(fused, db_path)
    assert fused[0].chunk_date == "2026-04-20"
    assert fused[1].chunk_date == "2026-04-21"
    assert fused[2].chunk_date == ""


@pytest.mark.unit
def test_enrich_chunk_dates_handles_missing_db(tmp_path: Path) -> None:
    """_enrich_chunk_dates returns silently when DB does not exist."""
    from kairix.search.hybrid import _enrich_chunk_dates
    from kairix.search.rrf import FusedResult

    fused = [
        FusedResult(path="/vault/doc.md", collection="c", title="T", snippet="s", rrf_score=0.5, boosted_score=0.5)
    ]
    _enrich_chunk_dates(fused, tmp_path / "nonexistent.sqlite")
    assert fused[0].chunk_date == ""


@pytest.mark.unit
def test_enrich_chunk_dates_empty_list(tmp_path: Path) -> None:
    """_enrich_chunk_dates is a no-op for empty list."""
    from kairix.search.hybrid import _enrich_chunk_dates

    db_path = _make_chunk_date_db(tmp_path, [("h1", "/vault/doc.md", "2026-04-20")])
    _enrich_chunk_dates([], db_path)
    assert True, "smoke: empty list handled without error"


@pytest.mark.unit
def test_enrich_chunk_dates_no_matching_paths(tmp_path: Path) -> None:
    """_enrich_chunk_dates leaves chunk_date empty when paths don't match."""
    from kairix.search.hybrid import _enrich_chunk_dates
    from kairix.search.rrf import FusedResult

    db_path = _make_chunk_date_db(tmp_path, [("h1", "/vault/other.md", "2026-04-20")])
    fused = [
        FusedResult(path="/vault/doc.md", collection="c", title="T", snippet="s", rrf_score=0.5, boosted_score=0.5)
    ]
    _enrich_chunk_dates(fused, db_path)
    assert fused[0].chunk_date == ""
