"""Tests for kairix.eval.tune — search parameter recommendations."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class TestAnalyseResults:
    def test_identifies_weak_categories(self) -> None:
        from kairix.eval.tune import analyse_results

        scores = {"recall": 0.65, "temporal": 0.35, "procedural": 0.40, "entity": 0.70}
        analysis = analyse_results(scores, floor=0.50)
        assert "temporal" in analysis.weak_categories
        assert "procedural" in analysis.weak_categories
        assert "recall" not in analysis.weak_categories

    def test_all_above_floor(self) -> None:
        from kairix.eval.tune import analyse_results

        scores = {"recall": 0.80, "temporal": 0.75, "entity": 0.70}
        analysis = analyse_results(scores, floor=0.50)
        assert len(analysis.weak_categories) == 0


class TestRecommend:
    def test_recommends_temporal_boost(self) -> None:
        from kairix.eval.tune import CorpusHints, recommend

        hints = CorpusHints(has_date_files=True, has_procedural_docs=False, has_entity_folders=False)
        weak = ["temporal"]
        recs = recommend(weak, hints)
        assert any(r.parameter == "temporal" for r in recs)

    def test_recommends_procedural_boost(self) -> None:
        from kairix.eval.tune import CorpusHints, recommend

        hints = CorpusHints(has_date_files=False, has_procedural_docs=True, has_entity_folders=False)
        weak = ["procedural"]
        recs = recommend(weak, hints)
        assert any(r.parameter == "procedural" for r in recs)

    def test_no_recommendations_when_no_weak(self) -> None:
        from kairix.eval.tune import CorpusHints, recommend

        hints = CorpusHints(has_date_files=True, has_procedural_docs=True, has_entity_folders=True)
        recs = recommend([], hints)
        assert len(recs) == 0

    def test_recommendation_has_required_fields(self) -> None:
        from kairix.eval.tune import CorpusHints, recommend

        hints = CorpusHints(has_date_files=True, has_procedural_docs=True, has_entity_folders=False)
        recs = recommend(["temporal", "procedural"], hints)
        for r in recs:
            assert hasattr(r, "parameter")
            assert hasattr(r, "action")
            assert hasattr(r, "reason")
            assert hasattr(r, "expected_impact")
