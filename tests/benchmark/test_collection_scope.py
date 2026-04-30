"""Tests for benchmark --collection and --fusion CLI flags (Sprint 17 Track C1)."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from kairix.core.search.config import RetrievalConfig

pytestmark = pytest.mark.unit


class TestFusionOverride:
    """Verify that fusion_override creates a new RetrievalConfig with the correct strategy."""

    def test_replace_frozen_dataclass(self) -> None:
        cfg = RetrievalConfig.defaults()
        assert cfg.fusion_strategy == "rrf"

        overridden = replace(cfg, fusion_strategy="bm25_primary")
        assert overridden.fusion_strategy == "bm25_primary"
        # Original unchanged
        assert cfg.fusion_strategy == "rrf"

    def test_replace_preserves_other_fields(self) -> None:
        cfg = RetrievalConfig.defaults()
        overridden = replace(cfg, fusion_strategy="rrf")
        assert overridden.entity == cfg.entity
        assert overridden.procedural == cfg.procedural
        assert overridden.rrf_k == cfg.rrf_k


class TestRetrieveCollectionWiring:
    """Verify that _retrieve passes collection and fusion_override to search()."""

    @patch("kairix.core.search.hybrid.search")
    def test_collection_passed_to_search(self, mock_search: MagicMock) -> None:
        from kairix.quality.benchmark.runner import _retrieve

        # Set up mock search result
        mock_sr = MagicMock()
        mock_sr.results = []
        mock_sr.intent.value = "SEMANTIC"
        mock_sr.bm25_count = 0
        mock_sr.vec_count = 0
        mock_sr.fused_count = 0
        mock_sr.vec_failed = False
        mock_sr.latency_ms = 1.0
        mock_search.return_value = mock_sr

        _retrieve("test query", "hybrid", "shape", collection="reference-library")

        mock_search.assert_called_once()
        call_kwargs = mock_search.call_args[1]
        assert call_kwargs["collections"] == ["reference-library"]

    @patch("kairix.core.search.hybrid.search")
    def test_no_collection_passes_none(self, mock_search: MagicMock) -> None:
        from kairix.quality.benchmark.runner import _retrieve

        mock_sr = MagicMock()
        mock_sr.results = []
        mock_sr.intent.value = "SEMANTIC"
        mock_sr.bm25_count = 0
        mock_sr.vec_count = 0
        mock_sr.fused_count = 0
        mock_sr.vec_failed = False
        mock_sr.latency_ms = 1.0
        mock_search.return_value = mock_sr

        _retrieve("test query", "hybrid", "shape")

        call_kwargs = mock_search.call_args[1]
        assert call_kwargs["collections"] is None

    @patch("kairix.core.search.config_loader.load_config")
    @patch("kairix.core.search.hybrid.search")
    def test_fusion_override_applied(self, mock_search: MagicMock, mock_load: MagicMock) -> None:
        from kairix.quality.benchmark.runner import _retrieve

        mock_load.return_value = RetrievalConfig.defaults()

        mock_sr = MagicMock()
        mock_sr.results = []
        mock_sr.intent.value = "SEMANTIC"
        mock_sr.bm25_count = 0
        mock_sr.vec_count = 0
        mock_sr.fused_count = 0
        mock_sr.vec_failed = False
        mock_sr.latency_ms = 1.0
        mock_search.return_value = mock_sr

        _retrieve("test query", "hybrid", "shape", fusion_override="rrf")

        call_kwargs = mock_search.call_args[1]
        assert call_kwargs["config"].fusion_strategy == "rrf"


class TestRunBenchmarkMetadata:
    """Verify that collection and fusion_override appear in result metadata."""

    @patch("kairix.quality.benchmark.runner._retrieve")
    def test_metadata_includes_collection(self, mock_retrieve: MagicMock) -> None:
        from kairix.quality.benchmark.runner import run_benchmark
        from kairix.quality.benchmark.suite import BenchmarkCase, BenchmarkSuite

        mock_retrieve.return_value = (["path/a.md"], ["snippet"], {"system": "hybrid"})

        suite = BenchmarkSuite(
            meta={"name": "test", "version": "1.0"},
            cases=[
                BenchmarkCase(
                    id="R01",
                    category="recall",
                    query="test",
                    gold_path="path/a.md",
                    score_method="exact",
                ),
            ],
        )

        result = run_benchmark(suite, collection="reference-library", fusion_override="rrf")
        assert result.meta["collection"] == "reference-library"
        assert result.meta["fusion_override"] == "rrf"

    @patch("kairix.quality.benchmark.runner._retrieve")
    def test_metadata_none_when_unset(self, mock_retrieve: MagicMock) -> None:
        from kairix.quality.benchmark.runner import run_benchmark
        from kairix.quality.benchmark.suite import BenchmarkCase, BenchmarkSuite

        mock_retrieve.return_value = (["path/a.md"], ["snippet"], {"system": "hybrid"})

        suite = BenchmarkSuite(
            meta={"name": "test", "version": "1.0"},
            cases=[
                BenchmarkCase(
                    id="R01",
                    category="recall",
                    query="test",
                    gold_path="path/a.md",
                    score_method="exact",
                ),
            ],
        )

        result = run_benchmark(suite)
        assert result.meta["collection"] is None
        assert result.meta["fusion_override"] is None
