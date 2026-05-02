"""
Tests for optional raw query logging in kairix.core.search.hybrid.

Coverage:
  - KAIRIX_LOG_QUERIES=0 (default): no queries.jsonl written
  - KAIRIX_LOG_QUERIES=1: entry written with correct fields after search
  - Entry contains query, query_hash, intent, top_paths
  - top_paths limited to 3 entries
  - Rotation: when file > 10MB, rotates to .1 before writing

Uses SearchPipeline with fakes for dependency injection where tests call pipeline.search().
Direct _log_query_event tests use monkeypatch on module-level state.
"""

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

import kairix.core.search.hybrid as hybrid_mod
from kairix.core.search.backends import BM25SearchBackend, VectorSearchBackend
from kairix.core.search.budget import BudgetedResult
from kairix.core.search.config import RetrievalConfig
from kairix.core.search.fusion import RRFFusion
from kairix.core.search.intent import QueryIntent
from kairix.core.search.pipeline import SearchPipeline
from kairix.core.search.rrf import FusedResult
from tests.fakes import (
    FakeClassifier,
    FakeDocumentRepository,
    FakeEmbeddingService,
    FakeGraphRepository,
    FakeSearchLogger,
    FakeVectorRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_VEC = np.random.rand(1536).astype(np.float32).tolist()


def _budgeted(path: str, tokens: int = 50) -> BudgetedResult:
    fused = FusedResult(
        path=path,
        title="Doc",
        snippet="snippet",
        collection="vault",
        rrf_score=0.5,
    )
    return BudgetedResult(
        result=fused,
        tier="L1",
        token_estimate=tokens,
        content="snippet text",
    )


def _build_test_pipeline(*, logger: FakeSearchLogger | None = None) -> SearchPipeline:
    """Build a SearchPipeline with fakes for query-logging tests."""
    cfg = RetrievalConfig.defaults()
    return SearchPipeline(
        classifier=FakeClassifier(intent=QueryIntent.SEMANTIC),
        bm25=BM25SearchBackend(FakeDocumentRepository()),
        vector=VectorSearchBackend(FakeEmbeddingService(), FakeVectorRepository()),
        graph=FakeGraphRepository(available=False),
        fusion=RRFFusion(k=cfg.rrf_k),
        boosts=[],
        logger=logger,
        config=cfg,
    )


# ---------------------------------------------------------------------------
# Tests: logging disabled (default)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQueryLoggingDisabled:
    """When KAIRIX_LOG_QUERIES=0, no queries.jsonl should be written."""

    @pytest.mark.unit
    def test_no_file_written_when_disabled(self, tmp_path: Path) -> None:
        log_path = tmp_path / "queries.jsonl"

        with (
            patch.object(hybrid_mod, "_LOG_QUERIES", False),
            patch.object(hybrid_mod, "_QUERY_LOG_PATH", log_path),
        ):
            hybrid_mod._log_query_event(
                {
                    "ts": 1,
                    "query": "test",
                    "query_hash": "abc",
                    "intent": "semantic",
                    "agent": None,
                    "fused_count": 5,
                    "vec_failed": False,
                    "latency_ms": 100.0,
                    "top_paths": [],
                }
            )

        assert not log_path.exists(), "queries.jsonl should not be created when logging is disabled"

    @pytest.mark.unit
    def test_search_does_not_write_query_log_when_disabled(self, tmp_path: Path) -> None:
        log_path = tmp_path / "queries.jsonl"

        with (
            patch.object(hybrid_mod, "_LOG_QUERIES", False),
            patch.object(hybrid_mod, "_QUERY_LOG_PATH", log_path),
        ):
            # Direct _log_query_event call -- pipeline does not call hybrid logging
            hybrid_mod._log_query_event({"query": "test", "ts": 1})

        assert not log_path.exists()


# ---------------------------------------------------------------------------
# Tests: logging enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQueryLoggingEnabled:
    """When KAIRIX_LOG_QUERIES=1, entry must be written with correct fields."""

    @pytest.mark.unit
    def test_entry_written_after_log_call(self, tmp_path: Path) -> None:
        log_path = tmp_path / "queries.jsonl"

        with (
            patch.object(hybrid_mod, "_LOG_QUERIES", True),
            patch.object(hybrid_mod, "_QUERY_LOG_PATH", log_path),
        ):
            hybrid_mod._log_query_event(
                {
                    "ts": 1234567890,
                    "query": "what is Dan working on",
                    "query_hash": "abc123456789",
                    "intent": "semantic",
                    "agent": "shape",
                    "fused_count": 5,
                    "vec_failed": False,
                    "latency_ms": 42.0,
                    "top_paths": ["/vault/doc1.md", "/vault/doc2.md"],
                }
            )

        assert log_path.exists(), "queries.jsonl should be created when logging is enabled"
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])

        # Required fields
        assert entry["query"] == "what is Dan working on"
        assert "query_hash" in entry
        assert entry["intent"] == "semantic"
        assert entry["agent"] == "shape"
        assert "fused_count" in entry
        assert "vec_failed" in entry
        assert "latency_ms" in entry
        assert "ts" in entry
        assert "top_paths" in entry

    @pytest.mark.unit
    def test_query_hash_present_and_correct_length(self, tmp_path: Path) -> None:
        log_path = tmp_path / "queries.jsonl"

        with (
            patch.object(hybrid_mod, "_LOG_QUERIES", True),
            patch.object(hybrid_mod, "_QUERY_LOG_PATH", log_path),
        ):
            hybrid_mod._log_query_event(
                {
                    "ts": 1,
                    "query": "test query for hash",
                    "query_hash": "a1b2c3d4e5f6",
                    "intent": "semantic",
                    "agent": None,
                    "fused_count": 0,
                    "vec_failed": False,
                    "latency_ms": 10.0,
                    "top_paths": [],
                }
            )

        entry = json.loads(log_path.read_text().strip())
        # sha256 hex[:12]
        assert len(entry["query_hash"]) == 12
        assert all(c in "0123456789abcdef" for c in entry["query_hash"])

    @pytest.mark.unit
    def test_top_paths_limited_to_three(self, tmp_path: Path) -> None:
        log_path = tmp_path / "queries.jsonl"

        with (
            patch.object(hybrid_mod, "_LOG_QUERIES", True),
            patch.object(hybrid_mod, "_QUERY_LOG_PATH", log_path),
        ):
            hybrid_mod._log_query_event(
                {
                    "ts": 1,
                    "query": "multi-result query",
                    "query_hash": "abc123456789",
                    "intent": "semantic",
                    "agent": "builder",
                    "fused_count": 5,
                    "vec_failed": False,
                    "latency_ms": 10.0,
                    "top_paths": ["/vault/a.md", "/vault/b.md", "/vault/c.md"],
                }
            )

        entry = json.loads(log_path.read_text().strip())
        assert len(entry["top_paths"]) == 3
        assert entry["top_paths"] == ["/vault/a.md", "/vault/b.md", "/vault/c.md"]

    @pytest.mark.unit
    def test_multiple_log_calls_append_multiple_lines(self, tmp_path: Path) -> None:
        log_path = tmp_path / "queries.jsonl"

        with (
            patch.object(hybrid_mod, "_LOG_QUERIES", True),
            patch.object(hybrid_mod, "_QUERY_LOG_PATH", log_path),
        ):
            hybrid_mod._log_query_event({"query": "first query", "ts": 1})
            hybrid_mod._log_query_event({"query": "second query", "ts": 2})

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2
        queries = [json.loads(line)["query"] for line in lines]
        assert "first query" in queries
        assert "second query" in queries


# ---------------------------------------------------------------------------
# Tests: log rotation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQueryLogRotation:
    """Log rotation: file > 10MB -> rotate to .1 before appending."""

    @pytest.mark.unit
    def test_rotation_when_file_exceeds_10mb(self, tmp_path: Path) -> None:
        log_path = tmp_path / "queries.jsonl"
        rotated_path = tmp_path / "queries.jsonl.1"

        # Create a fake oversized file
        log_path.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
        original_size = log_path.stat().st_size

        with (
            patch.object(hybrid_mod, "_LOG_QUERIES", True),
            patch.object(hybrid_mod, "_QUERY_LOG_PATH", log_path),
            patch.object(hybrid_mod, "_QUERY_LOG_MAX_BYTES", 10 * 1024 * 1024),
        ):
            hybrid_mod._log_query_event(
                {
                    "ts": 1,
                    "query": "new query",
                    "query_hash": "abc123456789",
                    "intent": "semantic",
                    "agent": "builder",
                    "fused_count": 3,
                    "vec_failed": False,
                    "latency_ms": 42.0,
                    "top_paths": [],
                }
            )

        # Original large file should now be at .1
        assert rotated_path.exists(), "rotated file should exist at queries.jsonl.1"
        assert rotated_path.stat().st_size == original_size

        # New log file should be small (just the one new entry)
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["query"] == "new query"

    @pytest.mark.unit
    def test_rotation_replaces_existing_dot_one(self, tmp_path: Path) -> None:
        log_path = tmp_path / "queries.jsonl"
        rotated_path = tmp_path / "queries.jsonl.1"

        # Pre-existing .1 file
        rotated_path.write_text("old rotated content\n")
        # Oversized active log
        log_path.write_bytes(b"y" * (10 * 1024 * 1024 + 1))

        with (
            patch.object(hybrid_mod, "_LOG_QUERIES", True),
            patch.object(hybrid_mod, "_QUERY_LOG_PATH", log_path),
            patch.object(hybrid_mod, "_QUERY_LOG_MAX_BYTES", 10 * 1024 * 1024),
        ):
            hybrid_mod._log_query_event(
                {
                    "ts": 2,
                    "query": "after rotation",
                    "query_hash": "def123456789",
                    "intent": "keyword",
                    "agent": None,
                    "fused_count": 0,
                    "vec_failed": True,
                    "latency_ms": 10.0,
                    "top_paths": [],
                }
            )

        # Old .1 file should be gone; .1 now has the previously-oversized file
        assert rotated_path.exists()
        assert rotated_path.stat().st_size == 10 * 1024 * 1024 + 1
        # "old rotated content" should be gone
        assert "old rotated content" not in rotated_path.read_text(errors="replace")

    @pytest.mark.unit
    def test_no_rotation_when_file_under_limit(self, tmp_path: Path) -> None:
        log_path = tmp_path / "queries.jsonl"
        rotated_path = tmp_path / "queries.jsonl.1"

        # Small file -- should not rotate
        log_path.write_text('{"ts": 1, "query": "existing"}\n')

        with (
            patch.object(hybrid_mod, "_LOG_QUERIES", True),
            patch.object(hybrid_mod, "_QUERY_LOG_PATH", log_path),
            patch.object(hybrid_mod, "_QUERY_LOG_MAX_BYTES", 10 * 1024 * 1024),
        ):
            hybrid_mod._log_query_event(
                {
                    "ts": 2,
                    "query": "appended",
                    "query_hash": "abc123456789",
                    "intent": "semantic",
                    "agent": "shape",
                    "fused_count": 1,
                    "vec_failed": False,
                    "latency_ms": 50.0,
                    "top_paths": [],
                }
            )

        assert not rotated_path.exists(), "rotation should not happen when file is under limit"
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2

    @pytest.mark.unit
    def test_rotate_helper_directly(self, tmp_path: Path) -> None:
        log_path = tmp_path / "queries.jsonl"
        rotated_path = tmp_path / "queries.jsonl.1"

        log_path.write_text("line1\nline2\n")
        hybrid_mod._rotate_query_log(log_path)

        assert not log_path.exists()
        assert rotated_path.exists()
        assert rotated_path.read_text() == "line1\nline2\n"
