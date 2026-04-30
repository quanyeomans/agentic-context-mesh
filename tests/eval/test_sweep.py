"""Unit tests for scoring metrics used by sweep — delegates to kairix.quality.eval.metrics."""

from __future__ import annotations

import math

import pytest

from kairix.quality.eval.metrics import (
    hit_at_k_graded as _compute_hit_at_k,
)
from kairix.quality.eval.metrics import (
    ndcg_graded as _compute_ndcg,
)
from kairix.quality.eval.metrics import (
    reciprocal_rank_graded as _compute_mrr,
)

# ---------------------------------------------------------------------------
# ndcg_graded (was _compute_ndcg)
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

        expected_dcg = 2.0 / math.log2(2) + 0.0 + 1.0 / math.log2(4)
        expected_idcg = 2.0 / math.log2(2) + 1.0 / math.log2(3)
        expected = expected_dcg / expected_idcg

        assert _compute_ndcg(retrieved, gold, k=10) == pytest.approx(expected, rel=1e-6)

    @pytest.mark.unit
    def test_k_truncation(self):
        """Only top-k results count."""
        gold = [{"title": "late", "relevance": 2}]
        retrieved = ["/a.md", "/b.md", "/late.md"]
        assert _compute_ndcg(retrieved, gold, k=2) == 0.0

    @pytest.mark.unit
    def test_empty_retrieved(self):
        gold = [{"title": "a", "relevance": 1}]
        assert _compute_ndcg([], gold, k=10) == 0.0


# ---------------------------------------------------------------------------
# hit_at_k_graded (was _compute_hit_at_k)
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
# reciprocal_rank_graded (was _compute_mrr)
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
