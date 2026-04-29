"""Unit tests for kairix.quality.eval.sweep — metric calculation functions."""

from __future__ import annotations

import math

import pytest

# ---------------------------------------------------------------------------
# Helpers: import pure functions under test
# ---------------------------------------------------------------------------
from kairix.quality.eval.sweep import (
    _build_rel_map,
    _compute_hit_at_k,
    _compute_mrr,
    _compute_ndcg,
    _match_path,
)

# ---------------------------------------------------------------------------
# _build_rel_map
# ---------------------------------------------------------------------------


class TestBuildRelMap:
    @pytest.mark.unit
    def test_empty_gold(self):
        rel_map, mode = _build_rel_map([])
        assert rel_map == {}
        assert mode == "stem"

    @pytest.mark.unit
    def test_title_format(self):
        gold = [
            {"title": "My-Doc", "relevance": 2},
            {"title": "Other", "relevance": 1},
        ]
        rel_map, mode = _build_rel_map(gold)
        assert mode == "stem"
        assert rel_map["my-doc"] == 2
        assert rel_map["other"] == 1

    @pytest.mark.unit
    def test_path_format(self):
        gold = [
            {"path": "engineering/adr-001.md", "relevance": 2},
        ]
        rel_map, mode = _build_rel_map(gold)
        assert mode == "path"
        assert rel_map["engineering/adr-001.md"] == 2

    @pytest.mark.unit
    def test_no_title_no_path(self):
        gold = [{"something": "else"}]
        rel_map, mode = _build_rel_map(gold)  # noqa: RUF059
        assert rel_map == {}

    @pytest.mark.unit
    def test_default_relevance(self):
        gold = [{"title": "Doc"}]
        rel_map, _ = _build_rel_map(gold)
        assert rel_map["doc"] == 0


# ---------------------------------------------------------------------------
# _match_path
# ---------------------------------------------------------------------------


class TestMatchPath:
    @pytest.mark.unit
    def test_stem_mode_exact(self):
        rel_map = {"my-doc": 2}
        assert _match_path("/some/path/My-Doc.md", rel_map, "stem") == 2

    @pytest.mark.unit
    def test_stem_mode_miss(self):
        rel_map = {"my-doc": 2}
        assert _match_path("/some/path/other.md", rel_map, "stem") == 0

    @pytest.mark.unit
    def test_path_mode_exact(self):
        rel_map = {"engineering/adr-001.md": 2}
        assert _match_path("engineering/adr-001.md", rel_map, "path") == 2

    @pytest.mark.unit
    def test_path_mode_suffix_match(self):
        rel_map = {"adr-001.md": 2}
        assert _match_path("/full/path/adr-001.md", rel_map, "path") == 2

    @pytest.mark.unit
    def test_path_mode_miss(self):
        rel_map = {"adr-001.md": 2}
        assert _match_path("/full/path/other.md", rel_map, "path") == 0

    @pytest.mark.unit
    def test_unknown_mode(self):
        assert _match_path("foo.md", {"foo": 1}, "unknown") == 0


# ---------------------------------------------------------------------------
# _compute_ndcg
# ---------------------------------------------------------------------------


class TestComputeNDCG:
    @pytest.mark.unit
    def test_perfect_ranking(self):
        """Single relevant doc at rank 1 → NDCG = 1.0."""
        gold = [{"title": "target", "relevance": 2}]
        retrieved = ["/path/target.md"]
        assert _compute_ndcg(retrieved, gold, k=10) == pytest.approx(1.0)

    @pytest.mark.unit
    def test_empty_gold(self):
        assert _compute_ndcg(["/a.md"], [], k=10) == 0.0

    @pytest.mark.unit
    def test_no_relevant_retrieved(self):
        gold = [{"title": "target", "relevance": 2}]
        retrieved = ["/path/wrong.md", "/path/also-wrong.md"]
        assert _compute_ndcg(retrieved, gold, k=10) == 0.0

    @pytest.mark.unit
    def test_known_ndcg_value(self):
        """Two gold docs, retrieved at rank 1 and 3 — manual DCG/IDCG."""
        gold = [
            {"title": "a", "relevance": 2},
            {"title": "b", "relevance": 1},
        ]
        retrieved = ["/path/a.md", "/path/irrelevant.md", "/path/b.md"]

        # DCG: 2/log2(2) + 0/log2(3) + 1/log2(4) = 2.0 + 0.0 + 0.5 = 2.5
        # IDCG: 2/log2(2) + 1/log2(3) = 2.0 + 0.6309... = 2.6309...
        expected_dcg = 2.0 / math.log2(2) + 0.0 + 1.0 / math.log2(4)
        expected_idcg = 2.0 / math.log2(2) + 1.0 / math.log2(3)
        expected = expected_dcg / expected_idcg

        assert _compute_ndcg(retrieved, gold, k=10) == pytest.approx(expected, rel=1e-6)

    @pytest.mark.unit
    def test_k_truncation(self):
        """Only top-k results count."""
        gold = [{"title": "late", "relevance": 2}]
        retrieved = ["/a.md", "/b.md", "/late.md"]
        # k=2 means /late.md is excluded
        assert _compute_ndcg(retrieved, gold, k=2) == 0.0

    @pytest.mark.unit
    def test_empty_retrieved(self):
        gold = [{"title": "a", "relevance": 1}]
        assert _compute_ndcg([], gold, k=10) == 0.0


# ---------------------------------------------------------------------------
# _compute_hit_at_k
# ---------------------------------------------------------------------------


class TestComputeHitAtK:
    @pytest.mark.unit
    def test_hit_in_top1(self):
        gold = [{"title": "target", "relevance": 1}]
        assert _compute_hit_at_k(["/target.md"], gold, k=5) is True

    @pytest.mark.unit
    def test_hit_at_boundary(self):
        gold = [{"title": "target", "relevance": 1}]
        retrieved = ["/a.md", "/b.md", "/c.md", "/d.md", "/target.md"]
        assert _compute_hit_at_k(retrieved, gold, k=5) is True

    @pytest.mark.unit
    def test_miss_beyond_k(self):
        gold = [{"title": "target", "relevance": 1}]
        retrieved = ["/a.md", "/b.md", "/c.md", "/d.md", "/e.md", "/target.md"]
        assert _compute_hit_at_k(retrieved, gold, k=5) is False

    @pytest.mark.unit
    def test_no_gold(self):
        assert _compute_hit_at_k(["/a.md"], [], k=5) is False

    @pytest.mark.unit
    def test_k1(self):
        gold = [{"title": "target", "relevance": 2}]
        assert _compute_hit_at_k(["/target.md"], gold, k=1) is True
        assert _compute_hit_at_k(["/other.md", "/target.md"], gold, k=1) is False


# ---------------------------------------------------------------------------
# _compute_mrr
# ---------------------------------------------------------------------------


class TestComputeMRR:
    @pytest.mark.unit
    def test_first_position(self):
        gold = [{"title": "target", "relevance": 1}]
        assert _compute_mrr(["/target.md"], gold, k=10) == pytest.approx(1.0)

    @pytest.mark.unit
    def test_second_position(self):
        gold = [{"title": "target", "relevance": 1}]
        assert _compute_mrr(["/other.md", "/target.md"], gold, k=10) == pytest.approx(0.5)

    @pytest.mark.unit
    def test_third_position(self):
        gold = [{"title": "target", "relevance": 1}]
        retrieved = ["/a.md", "/b.md", "/target.md"]
        assert _compute_mrr(retrieved, gold, k=10) == pytest.approx(1.0 / 3)

    @pytest.mark.unit
    def test_no_relevant(self):
        gold = [{"title": "target", "relevance": 1}]
        assert _compute_mrr(["/a.md", "/b.md"], gold, k=10) == 0.0

    @pytest.mark.unit
    def test_beyond_k(self):
        gold = [{"title": "target", "relevance": 1}]
        retrieved = ["/a.md", "/b.md", "/target.md"]
        assert _compute_mrr(retrieved, gold, k=2) == 0.0

    @pytest.mark.unit
    def test_empty_gold(self):
        assert _compute_mrr(["/a.md"], [], k=10) == 0.0


# ---------------------------------------------------------------------------
# Category alias resolution (via constants)
# ---------------------------------------------------------------------------


class TestCategoryAliases:
    @pytest.mark.unit
    def test_semantic_alias(self):
        from kairix.quality.eval.constants import CATEGORY_ALIASES

        assert CATEGORY_ALIASES["semantic"] == "recall"

    @pytest.mark.unit
    def test_keyword_alias(self):
        from kairix.quality.eval.constants import CATEGORY_ALIASES

        assert CATEGORY_ALIASES["keyword"] == "conceptual"

    @pytest.mark.unit
    def test_weights_sum(self):
        from kairix.quality.eval.constants import CATEGORY_WEIGHTS

        total = sum(CATEGORY_WEIGHTS.values())
        assert total == pytest.approx(1.0)
