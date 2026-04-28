"""Tests for per-collection retrieval config resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kairix.core.search.config import (
    RetrievalConfig,
)
from kairix.core.search.config_loader import (
    _merge_retrieval_config,
    resolve_retrieval_config,
)

pytestmark = pytest.mark.unit


class TestMergeRetrievalConfig:
    def test_top_level_override(self) -> None:
        base = RetrievalConfig.defaults()
        merged = _merge_retrieval_config(base, {"fusion_strategy": "rrf", "vec_limit": 30})
        assert merged.fusion_strategy == "rrf"
        assert merged.vec_limit == 30
        assert merged.bm25_limit == base.bm25_limit  # unchanged

    def test_nested_entity_override(self) -> None:
        base = RetrievalConfig.defaults()
        merged = _merge_retrieval_config(base, {"boosts": {"entity": {"factor": 0.50}}})
        assert merged.entity.factor == 0.50
        assert merged.entity.cap == base.entity.cap  # unchanged
        assert merged.entity.enabled == base.entity.enabled  # unchanged

    def test_nested_procedural_override(self) -> None:
        base = RetrievalConfig.defaults()
        merged = _merge_retrieval_config(base, {"boosts": {"procedural": {"factor": 2.0}}})
        assert merged.procedural.factor == 2.0
        assert merged.procedural.enabled == base.procedural.enabled

    def test_empty_override_returns_base(self) -> None:
        base = RetrievalConfig.defaults()
        merged = _merge_retrieval_config(base, {})
        assert merged == base

    def test_full_override(self) -> None:
        base = RetrievalConfig.defaults()
        merged = _merge_retrieval_config(
            base,
            {
                "fusion_strategy": "rrf",
                "rrf_k": 40,
                "bm25_limit": 10,
                "vec_limit": 5,
                "boosts": {
                    "entity": {"enabled": False},
                    "procedural": {"enabled": False},
                },
            },
        )
        assert merged.fusion_strategy == "rrf"
        assert merged.rrf_k == 40
        assert merged.bm25_limit == 10
        assert merged.vec_limit == 5
        assert merged.entity.enabled is False
        assert merged.procedural.enabled is False


class TestResolveRetrievalConfig:
    def test_explicit_config_wins(self) -> None:
        explicit = RetrievalConfig.minimal()
        result = resolve_retrieval_config(
            collection="reference-library",
            explicit_config=explicit,
        )
        assert result is explicit

    def test_reflib_returns_hardcoded(self) -> None:
        from kairix.knowledge.reflib.retrieval_config import REFLIB_RETRIEVAL_CONFIG

        result = resolve_retrieval_config(collections=["reference-library"])
        assert result is REFLIB_RETRIEVAL_CONFIG
        assert result.vec_limit == 5
        assert result.fusion_strategy == "bm25_primary"

    def test_single_collection_from_list(self) -> None:
        from kairix.knowledge.reflib.retrieval_config import REFLIB_RETRIEVAL_CONFIG

        result = resolve_retrieval_config(collections=["reference-library"])
        assert result is REFLIB_RETRIEVAL_CONFIG

    @patch("kairix.core.search.config_loader._get_collection_overrides")
    @patch("kairix.core.search.config_loader.load_config")
    def test_single_collection_with_yaml_config(self, mock_load, mock_overrides) -> None:
        mock_load.return_value = RetrievalConfig.defaults()
        mock_overrides.return_value = {
            "my-docs": {"fusion_strategy": "rrf", "vec_limit": 30},
        }
        result = resolve_retrieval_config(collections=["my-docs"])
        assert result.fusion_strategy == "rrf"
        assert result.vec_limit == 30

    @patch("kairix.core.search.config_loader.load_config")
    def test_multi_collection_uses_global(self, mock_load) -> None:
        global_cfg = RetrievalConfig.defaults()
        mock_load.return_value = global_cfg
        result = resolve_retrieval_config(collections=["a", "b"])
        assert result is global_cfg

    @patch("kairix.core.search.config_loader.load_config")
    def test_no_collection_uses_global(self, mock_load) -> None:
        global_cfg = RetrievalConfig.defaults()
        mock_load.return_value = global_cfg
        result = resolve_retrieval_config()
        assert result is global_cfg

    @patch("kairix.core.search.config_loader._get_collection_overrides")
    @patch("kairix.core.search.config_loader.load_config")
    def test_unknown_collection_uses_global(self, mock_load, mock_overrides) -> None:
        global_cfg = RetrievalConfig.defaults()
        mock_load.return_value = global_cfg
        mock_overrides.return_value = {}
        result = resolve_retrieval_config(collections=["unknown"])
        assert result is global_cfg


class TestRefLibConfig:
    def test_baseline_values(self) -> None:
        from kairix.knowledge.reflib.retrieval_config import REFLIB_RETRIEVAL_CONFIG

        assert REFLIB_RETRIEVAL_CONFIG.fusion_strategy == "bm25_primary"
        assert REFLIB_RETRIEVAL_CONFIG.bm25_limit == 20
        assert REFLIB_RETRIEVAL_CONFIG.vec_limit == 5
        assert REFLIB_RETRIEVAL_CONFIG.entity.enabled is True
        assert REFLIB_RETRIEVAL_CONFIG.procedural.enabled is True


class TestParseCollectionsWithRetrieval:
    def test_retrieval_overrides_parsed(self) -> None:
        from kairix.core.search.config_loader import parse_collections

        data = {
            "collections": {
                "shared": [
                    {
                        "name": "docs",
                        "path": "docs",
                        "retrieval": {"fusion_strategy": "rrf", "vec_limit": 30},
                    },
                ],
            },
        }
        result = parse_collections(data)
        assert result is not None
        assert result.shared[0].retrieval_overrides == {"fusion_strategy": "rrf", "vec_limit": 30}

    def test_no_retrieval_block_is_none(self) -> None:
        from kairix.core.search.config_loader import parse_collections

        data = {
            "collections": {
                "shared": [{"name": "docs", "path": "docs"}],
            },
        }
        result = parse_collections(data)
        assert result is not None
        assert result.shared[0].retrieval_overrides is None
