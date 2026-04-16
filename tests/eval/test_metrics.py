"""Unit tests for kairix.eval.metrics."""
import pytest
from kairix.eval.metrics import ndcg_score, hit_at_k, mean_reciprocal_rank


@pytest.mark.unit
class TestNdcg:
    def test_perfect_ranking(self):
        retrieved = ["a.md", "b.md", "c.md"]
        gold = ["a.md", "b.md"]
        assert ndcg_score(retrieved, gold, k=10) == pytest.approx(1.0)

    def test_empty_gold(self):
        assert ndcg_score(["a.md"], [], k=10) == 0.0

    def test_no_hits(self):
        assert ndcg_score(["x.md", "y.md"], ["a.md"], k=10) == 0.0

    def test_partial_hit(self):
        retrieved = ["x.md", "a.md"]  # hit at rank 2
        gold = ["a.md"]
        score = ndcg_score(retrieved, gold, k=10)
        assert 0.0 < score < 1.0

    def test_cutoff_respected(self):
        retrieved = ["x.md", "a.md"]  # hit at rank 2
        gold = ["a.md"]
        score_k1 = ndcg_score(retrieved, gold, k=1)
        score_k10 = ndcg_score(retrieved, gold, k=10)
        assert score_k1 == 0.0   # hit is beyond k=1
        assert score_k10 > 0.0


@pytest.mark.unit
class TestHitAtK:
    def test_hit_first(self):
        assert hit_at_k(["a.md", "b.md"], ["a.md"], k=3) == 1.0

    def test_hit_at_boundary(self):
        assert hit_at_k(["x.md", "y.md", "a.md"], ["a.md"], k=3) == 1.0

    def test_miss_beyond_k(self):
        assert hit_at_k(["x.md", "y.md", "a.md"], ["a.md"], k=2) == 0.0

    def test_empty_gold(self):
        assert hit_at_k(["a.md"], [], k=10) == 0.0


@pytest.mark.unit
class TestMrr:
    def test_hit_first(self):
        assert mean_reciprocal_rank(["a.md"], ["a.md"]) == pytest.approx(1.0)

    def test_hit_second(self):
        assert mean_reciprocal_rank(["x.md", "a.md"], ["a.md"]) == pytest.approx(0.5)

    def test_no_hit(self):
        assert mean_reciprocal_rank(["x.md"], ["a.md"]) == 0.0

    def test_empty_gold(self):
        assert mean_reciprocal_rank(["a.md"], []) == 0.0
