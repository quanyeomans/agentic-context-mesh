"""
Tests for kairix.eval.hybrid_sweep — hybrid pipeline calibration sweep.
"""

import math

import pytest

from kairix.eval.hybrid_sweep import (
    CATEGORY_ALIASES,
    CATEGORY_WEIGHTS,
    HybridSweepConfig,
    HybridSweepReport,
    HybridSweepResult,
    build_default_configs,
    compute_hit_at_k,
    compute_mrr,
    compute_ndcg,
    _build_rel_map,
    _match_path,
)


# ---------------------------------------------------------------------------
# HybridSweepConfig
# ---------------------------------------------------------------------------


class TestHybridSweepConfig:
    def test_defaults(self) -> None:
        cfg = HybridSweepConfig(name="test", mode="hybrid")
        assert cfg.rrf_k == 60
        assert cfg.entity_enabled is True
        assert cfg.procedural_enabled is True
        assert cfg.bm25_limit == 20
        assert cfg.vec_limit == 10

    def test_bm25_only(self) -> None:
        cfg = HybridSweepConfig(name="bm25", mode="bm25_only")
        assert cfg.mode == "bm25_only"

    def test_frozen(self) -> None:
        cfg = HybridSweepConfig(name="test", mode="hybrid")
        with pytest.raises(AttributeError):
            cfg.rrf_k = 100  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_default_configs
# ---------------------------------------------------------------------------


class TestBuildDefaultConfigs:
    def test_returns_configs(self) -> None:
        configs = build_default_configs()
        assert len(configs) > 10

    def test_includes_bm25_only_baseline(self) -> None:
        configs = build_default_configs()
        names = [c.name for c in configs]
        assert "bm25-only" in names

    def test_includes_rrf_k_sweep(self) -> None:
        configs = build_default_configs()
        names = [c.name for c in configs]
        for k in [10, 20, 40, 60, 100]:
            assert f"hybrid-k{k}-minimal" in names

    def test_includes_entity_factor_sweep(self) -> None:
        configs = build_default_configs()
        names = [c.name for c in configs]
        assert any("entity-f" in n for n in names)

    def test_includes_procedural_factor_sweep(self) -> None:
        configs = build_default_configs()
        names = [c.name for c in configs]
        assert any("proc-f" in n for n in names)

    def test_includes_bm25_primary_configs(self) -> None:
        configs = build_default_configs()
        names = [c.name for c in configs]
        assert any("bm25primary" in n for n in names)

    def test_includes_tuned_combos(self) -> None:
        configs = build_default_configs()
        names = [c.name for c in configs]
        assert "hybrid-tuned-a" in names
        assert "hybrid-tuned-b" in names

    def test_all_configs_have_names(self) -> None:
        configs = build_default_configs()
        for cfg in configs:
            assert cfg.name
            assert cfg.mode in ("hybrid", "bm25_only", "bm25_primary")

    def test_no_duplicate_names(self) -> None:
        configs = build_default_configs()
        names = [c.name for c in configs]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Metrics: _build_rel_map / _match_path
# ---------------------------------------------------------------------------


class TestRelMap:
    def test_gold_titles_format(self) -> None:
        gold = [{"title": "My Doc", "relevance": 2}]
        rel_map, mode = _build_rel_map(gold)
        assert mode == "stem"
        assert rel_map == {"my doc": 2}

    def test_gold_paths_format(self) -> None:
        gold = [{"path": "areas/kairix.md", "relevance": 1}]
        rel_map, mode = _build_rel_map(gold)
        assert mode == "path"
        assert rel_map == {"areas/kairix.md": 1}

    def test_empty_gold(self) -> None:
        rel_map, mode = _build_rel_map([])
        assert rel_map == {}


class TestMatchPath:
    def test_stem_match(self) -> None:
        rel_map = {"kairix platform": 2}
        assert _match_path("areas/kairix platform.md", rel_map, "stem") == 2

    def test_path_exact_match(self) -> None:
        rel_map = {"areas/kairix.md": 2}
        assert _match_path("areas/kairix.md", rel_map, "path") == 2

    def test_path_suffix_match(self) -> None:
        rel_map = {"areas/kairix.md": 2}
        assert _match_path("vault/areas/kairix.md", rel_map, "path") == 2

    def test_no_match(self) -> None:
        rel_map = {"other.md": 1}
        assert _match_path("areas/kairix.md", rel_map, "path") == 0


# ---------------------------------------------------------------------------
# Metrics: NDCG, Hit@k, MRR
# ---------------------------------------------------------------------------


class TestComputeNdcg:
    def test_perfect_ranking(self) -> None:
        gold = [{"path": "a.md", "relevance": 2}, {"path": "b.md", "relevance": 1}]
        paths = ["a.md", "b.md"]
        ndcg = compute_ndcg(paths, gold, k=10)
        assert ndcg == pytest.approx(1.0)

    def test_reversed_ranking(self) -> None:
        gold = [{"path": "a.md", "relevance": 2}, {"path": "b.md", "relevance": 1}]
        paths = ["b.md", "a.md"]
        ndcg = compute_ndcg(paths, gold, k=10)
        assert 0.0 < ndcg < 1.0

    def test_no_relevant_docs(self) -> None:
        gold = [{"path": "a.md", "relevance": 2}]
        paths = ["x.md", "y.md"]
        ndcg = compute_ndcg(paths, gold, k=10)
        assert ndcg == 0.0

    def test_empty_gold(self) -> None:
        assert compute_ndcg(["a.md"], [], k=10) == 0.0

    def test_empty_retrieved(self) -> None:
        gold = [{"path": "a.md", "relevance": 2}]
        assert compute_ndcg([], gold, k=10) == 0.0


class TestComputeHitAtK:
    def test_hit_in_top_k(self) -> None:
        gold = [{"path": "a.md", "relevance": 1}]
        assert compute_hit_at_k(["x.md", "a.md", "y.md"], gold, k=5) is True

    def test_miss(self) -> None:
        gold = [{"path": "a.md", "relevance": 1}]
        assert compute_hit_at_k(["x.md", "y.md"], gold, k=5) is False

    def test_hit_at_boundary(self) -> None:
        gold = [{"path": "a.md", "relevance": 1}]
        paths = ["x.md", "y.md", "z.md", "w.md", "a.md"]
        assert compute_hit_at_k(paths, gold, k=5) is True

    def test_miss_beyond_k(self) -> None:
        gold = [{"path": "a.md", "relevance": 1}]
        paths = ["x.md", "y.md", "z.md", "w.md", "v.md", "a.md"]
        assert compute_hit_at_k(paths, gold, k=5) is False


class TestComputeMrr:
    def test_first_position(self) -> None:
        gold = [{"path": "a.md", "relevance": 1}]
        assert compute_mrr(["a.md", "x.md"], gold) == pytest.approx(1.0)

    def test_second_position(self) -> None:
        gold = [{"path": "a.md", "relevance": 1}]
        assert compute_mrr(["x.md", "a.md"], gold) == pytest.approx(0.5)

    def test_no_relevant(self) -> None:
        gold = [{"path": "a.md", "relevance": 1}]
        assert compute_mrr(["x.md", "y.md"], gold) == 0.0


# ---------------------------------------------------------------------------
# Category weights
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_category_weights_sum_to_one() -> None:
    total = sum(CATEGORY_WEIGHTS.values())
    assert total == pytest.approx(1.0)


@pytest.mark.unit
def test_category_aliases_map_to_valid_weights() -> None:
    """All alias targets must exist in CATEGORY_WEIGHTS."""
    for alias, target in CATEGORY_ALIASES.items():
        assert target in CATEGORY_WEIGHTS, f"alias {alias!r}→{target!r} not in weights"


@pytest.mark.unit
def test_category_aliases_covers_suite_names() -> None:
    """semantic and keyword should be mapped."""
    assert "semantic" in CATEGORY_ALIASES
    assert "keyword" in CATEGORY_ALIASES


# ---------------------------------------------------------------------------
# HybridSweepResult and HybridSweepReport
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sweep_result_defaults() -> None:
    cfg = HybridSweepConfig(name="test", mode="hybrid")
    r = HybridSweepResult(config=cfg)
    assert r.weighted_total == 0.0
    assert r.n_vec_failed == 0
    assert r.category_scores == {}


@pytest.mark.unit
def test_sweep_report_defaults() -> None:
    report = HybridSweepReport()
    assert report.results == []
    assert report.best is None
    assert report.total_configs == 0
