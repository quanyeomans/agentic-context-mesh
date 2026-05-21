"""Unit tests for ScorerRegistry + auto_select_scorers.

Pins:

* ScorerRegistry.register / get / names / __contains__ / __len__.
* get with unknown name → KeyError with affordance markers (F21 shape).
* register replaces existing entry (idempotent on name).
* auto_select_scorers: gold present → NDCG/Hit/MRR; expected_answer
  present → judge; latency_ms populated on results → latency.
* auto_select_scorers: expected_answer without llm → ValueError with
  affordance markers.
* sabotage: dropping the gold field from every case must REMOVE the
  three IR scorers from the registry.
"""

from __future__ import annotations

import pytest

from kairix.quality.scoring.registry import ScorerRegistry, auto_select_scorers
from kairix.quality.scoring.types import QueryRunResult, ScorerResult
from tests.fakes import FakeLLMBackend

pytestmark = pytest.mark.unit


class _StubScorer:
    """Local stub for testing ScorerRegistry — NOT for testing real scorers.

    The registry contract is "map name → Scorer-shaped thing"; this
    stub is the minimal thing that satisfies the Protocol without
    pulling in NDCG/Hit/MRR math.
    """

    def __init__(self, name: str = "stub") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def score(self, run: object, /) -> ScorerResult:
        return ScorerResult(metric_name=self._name, score=1.0)


class TestScorerRegistry:
    def test_register_and_get_round_trip(self) -> None:
        reg = ScorerRegistry()
        scorer = _StubScorer(name="alpha")
        reg.register(scorer)
        assert reg.get("alpha") is scorer

    def test_names_returns_sorted_tuple(self) -> None:
        # Sabotage-proof: register names out-of-order; names() must
        # still return sorted.
        reg = ScorerRegistry([_StubScorer("z"), _StubScorer("a"), _StubScorer("m")])
        assert reg.names() == ("a", "m", "z")

    def test_contains_and_len(self) -> None:
        reg = ScorerRegistry([_StubScorer("x"), _StubScorer("y")])
        assert "x" in reg
        assert "y" in reg
        assert "missing" not in reg
        assert 99 not in reg
        assert len(reg) == 2

    def test_register_replaces_existing_by_name(self) -> None:
        # Two scorers with the same name — second one wins; len stays 1.
        reg = ScorerRegistry()
        first = _StubScorer("dup")
        second = _StubScorer("dup")
        reg.register(first)
        reg.register(second)
        assert len(reg) == 1
        assert reg.get("dup") is second

    def test_get_missing_raises_with_affordance_markers(self) -> None:
        # F21: fix: / next: / run: markers must appear in the error.
        reg = ScorerRegistry([_StubScorer("known")])
        with pytest.raises(KeyError) as exc:
            reg.get("missing")
        msg = str(exc.value)
        assert "missing" in msg
        # F21 markers
        assert "fix:" in msg
        assert "next:" in msg
        assert "run:" in msg
        # Lists known scorers for the caller.
        assert "known" in msg

    def test_constructor_takes_iterable(self) -> None:
        reg = ScorerRegistry((_StubScorer("a"),))
        assert "a" in reg

    def test_constructor_none_yields_empty(self) -> None:
        reg = ScorerRegistry()
        assert len(reg) == 0
        assert reg.names() == ()


class TestAutoSelectScorers:
    def test_gold_titles_enables_ir_scorers(self) -> None:
        # Sabotage-proof: drop gold_titles → ndcg/hit_at_k/mrr not
        # registered (proven by the corresponding test below).
        cases = [{"id": "E-01", "gold_titles": [{"title": "x", "relevance": 2}]}]
        reg = auto_select_scorers(cases=cases)
        assert "ndcg" in reg
        assert "hit_at_k" in reg
        assert "mrr" in reg

    def test_no_gold_skips_ir_scorers(self) -> None:
        cases = [{"id": "X-01", "expected_answer": "the answer"}]
        reg = auto_select_scorers(cases=cases, llm=FakeLLMBackend())
        assert "ndcg" not in reg
        assert "hit_at_k" not in reg
        assert "mrr" not in reg

    def test_expected_answer_enables_judge_with_llm(self) -> None:
        cases = [{"id": "Q-01", "expected_answer": "yes"}]
        reg = auto_select_scorers(cases=cases, llm=FakeLLMBackend())
        assert "judge" in reg

    def test_expected_answer_without_llm_raises(self) -> None:
        cases = [{"id": "Q-01", "expected_answer": "yes"}]
        with pytest.raises(ValueError) as exc:
            auto_select_scorers(cases=cases)
        msg = str(exc.value)
        assert "fix:" in msg
        assert "next:" in msg
        assert "run:" in msg

    def test_latency_results_enable_latency_scorer(self) -> None:
        cases = [{"id": "L-01"}]
        results = [
            QueryRunResult(query_id="L-01", category="recall", query_text="q", latency_ms=42.0),
        ]
        reg = auto_select_scorers(cases=cases, results=results)
        assert "latency" in reg

    def test_zero_latency_does_not_enable_latency_scorer(self) -> None:
        # Boundary: latency_ms == 0 means "no signal", not "real result".
        cases = [{"id": "L-01"}]
        results = [QueryRunResult(query_id="L-01", category="recall", query_text="q", latency_ms=0.0)]
        reg = auto_select_scorers(cases=cases, results=results)
        assert "latency" not in reg

    def test_combined_signals_enable_all(self) -> None:
        cases = [
            {
                "id": "X-01",
                "gold_titles": [{"title": "x", "relevance": 2}],
                "expected_answer": "the answer",
            },
        ]
        results = [
            QueryRunResult(query_id="X-01", category="entity", query_text="q", latency_ms=100.0),
        ]
        reg = auto_select_scorers(cases=cases, results=results, llm=FakeLLMBackend())
        for expected in ("ndcg", "hit_at_k", "mrr", "judge", "latency"):
            assert expected in reg, f"missing: {expected}"

    def test_empty_cases_returns_empty_registry(self) -> None:
        reg = auto_select_scorers(cases=[])
        assert len(reg) == 0

    def test_gold_paths_also_enables_ir(self) -> None:
        # Suite-shape variant: gold_paths instead of gold_titles still
        # enables the IR triad.
        cases = [{"id": "Y", "gold_paths": [{"path": "vault/x.md", "relevance": 2}]}]
        reg = auto_select_scorers(cases=cases)
        assert "ndcg" in reg
