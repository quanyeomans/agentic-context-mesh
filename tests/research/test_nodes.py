"""Tests for kairix.agents.research.nodes — individual node functions."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from kairix.agents.research.nodes import (
    ClassifyIntentDeps,
    RetrieveDeps,
    SynthesiseDeps,
    classify_intent,
    evaluate_sufficiency,
    refine_query,
    retrieve,
    route_after_evaluation,
    synthesise,
)
from kairix.agents.research.state import ResearcherState
from kairix.core.protocols import SourceRef
from kairix.use_cases.expand import ExpandedChunk, ExpandOutput
from tests.fakes import FakeLLMBackend


def _state(**overrides) -> ResearcherState:
    base: ResearcherState = {
        "query": "test question",
        "refined_query": "test question",
        "intent": "",
        "retrieved_chunks": [],
        "entities_found": [],
        "gaps": [],
        "synthesis": "",
        "turns": 0,
        "confidence": 0.0,
        "max_turns": 4,
        "error": "",
    }
    base.update(overrides)
    return base


@pytest.mark.unit
class TestClassifyIntent:
    @pytest.mark.unit
    def test_sets_intent(self) -> None:
        result = classify_intent(
            _state(query="who is Jordan Blake"),
            deps=ClassifyIntentDeps(classify_fn=lambda q: MagicMock(value="entity")),
        )
        assert result["intent"] == "entity"

    @pytest.mark.unit
    def test_defaults_to_semantic_on_error(self) -> None:
        def _failing(q):
            raise RuntimeError("boom")

        result = classify_intent(_state(), deps=ClassifyIntentDeps(classify_fn=_failing))
        assert result["intent"] == "semantic"


def _mock_search_result(paths_snippets: list[tuple[str, str]]):
    """Build a mock SearchResult with BudgetedResult-like objects."""
    results = []
    for path, snippet in paths_snippets:
        fused = MagicMock()
        fused.path = path
        budgeted = MagicMock()
        budgeted.result = fused
        budgeted.content = snippet
        results.append(budgeted)
    sr = MagicMock()
    sr.results = results
    return sr


@pytest.mark.unit
class TestRetrieve:
    @pytest.mark.unit
    def test_calls_search(self) -> None:
        mock_search = MagicMock(return_value=_mock_search_result([("a.md", "hello")]))
        result = retrieve(_state(), deps=RetrieveDeps(search_fn=mock_search))
        assert len(result["retrieved_chunks"]) == 1
        assert result["retrieved_chunks"][0]["path"] == "a.md"

    @pytest.mark.unit
    def test_accumulates_across_turns(self) -> None:
        existing = [{"path": "old.md", "snippet": "existing"}]
        mock_search = MagicMock(return_value=_mock_search_result([("new.md", "new")]))
        result = retrieve(
            _state(retrieved_chunks=existing, turns=1),
            deps=RetrieveDeps(search_fn=mock_search),
        )
        assert len(result["retrieved_chunks"]) == 2

    @pytest.mark.unit
    def test_deduplicates_by_path(self) -> None:
        existing = [{"path": "same.md", "snippet": "v1"}]
        mock_search = MagicMock(return_value=_mock_search_result([("same.md", "v2")]))
        result = retrieve(
            _state(retrieved_chunks=existing),
            deps=RetrieveDeps(search_fn=mock_search),
        )
        assert len(result["retrieved_chunks"]) == 1

    @pytest.mark.unit
    def test_chunk_carries_full_sourceref_breadcrumb(self) -> None:
        """PLA-274 — the retrieve node carries the canonical breadcrumb
        (source_uri / title / collection / source_page) off each fused result
        into the chunk dict, distinct from the display path.

        Sabotage-proof (executed): flipping any ``or`` default in
        ``_budgeted_to_research_chunk`` (e.g. source_uri ``or ""`` -> ``and ""``)
        blanks the carried field and these assertions fire."""
        from types import SimpleNamespace

        fused = SimpleNamespace(
            path="archive/handbook.zip#7",
            source_uri="sharepoint://acme/handbook.zip",
            title="Acme Handbook",
            collection="shared",
            source_page=4,
        )
        budgeted = SimpleNamespace(result=fused, content="deployment runbook body")
        sr = SimpleNamespace(results=[budgeted])
        mock_search = MagicMock(return_value=sr)

        result = retrieve(_state(), deps=RetrieveDeps(search_fn=mock_search))
        chunk = result["retrieved_chunks"][0]
        ref = chunk["source_ref"]
        assert ref["source_uri"] == "sharepoint://acme/handbook.zip"
        assert ref["path"] == "archive/handbook.zip#7"
        assert ref["title"] == "Acme Handbook"
        assert ref["collection"] == "shared"
        assert ref["source_page"] == 4

    @pytest.mark.unit
    def test_higher_budget_on_refinement(self) -> None:
        mock_search = MagicMock(return_value=_mock_search_result([]))
        retrieve(_state(turns=2), deps=RetrieveDeps(search_fn=mock_search))
        mock_search.assert_called_once()
        assert mock_search.call_args.kwargs["budget"] == 5000


@pytest.mark.unit
class TestEvaluateSufficiency:
    @pytest.mark.unit
    def test_parses_llm_response(self) -> None:
        llm_response = json.dumps(
            {
                "confidence": 0.85,
                "sufficient": True,
                "refined_query": None,
                "reasoning": "good",
            }
        )
        mock_backend = MagicMock()
        mock_backend.chat.return_value = llm_response
        result = evaluate_sufficiency(
            _state(retrieved_chunks=[{"path": "a.md", "snippet": "content"}]),
            llm_backend=mock_backend,
        )
        assert result["confidence"] == pytest.approx(0.85)

    @pytest.mark.unit
    def test_returns_zero_on_empty_chunks(self) -> None:
        result = evaluate_sufficiency(_state(retrieved_chunks=[]))
        assert result["confidence"] == pytest.approx(0.0)

    @pytest.mark.unit
    def test_returns_zero_on_llm_failure(self) -> None:
        mock_backend = MagicMock()
        mock_backend.chat.side_effect = RuntimeError("llm down")
        result = evaluate_sufficiency(
            _state(retrieved_chunks=[{"path": "a.md", "snippet": "x"}]),
            llm_backend=mock_backend,
        )
        assert result["confidence"] == pytest.approx(0.0)

    @pytest.mark.unit
    def test_returns_gaps_from_llm(self) -> None:
        """S18-5: evaluate_sufficiency parses and returns gaps from LLM response."""
        llm_response = json.dumps(
            {
                "confidence": 0.6,
                "sufficient": False,
                "refined_query": "better query",
                "gaps": ["missing deployment details", "no cost information"],
                "reasoning": "partial coverage",
            }
        )
        mock_backend = MagicMock()
        mock_backend.chat.return_value = llm_response
        result = evaluate_sufficiency(
            _state(retrieved_chunks=[{"path": "a.md", "snippet": "content"}]),
            llm_backend=mock_backend,
        )
        assert result["gaps"] == ["missing deployment details", "no cost information"]

    @pytest.mark.unit
    def test_returns_empty_gaps_on_failure(self) -> None:
        """S18-5: gaps defaults to empty list on LLM failure."""
        mock_backend = MagicMock()
        mock_backend.chat.side_effect = RuntimeError("llm down")
        result = evaluate_sufficiency(
            _state(retrieved_chunks=[{"path": "a.md", "snippet": "x"}]),
            llm_backend=mock_backend,
        )
        assert result["gaps"] == []


@pytest.mark.unit
class TestRefineQuery:
    @pytest.mark.unit
    def test_increments_turns(self) -> None:
        result = refine_query(_state(turns=1))
        assert result["turns"] == 2


@pytest.mark.unit
class TestSynthesise:
    @pytest.mark.unit
    def test_calls_llm(self) -> None:
        mock_backend = MagicMock()
        mock_backend.chat.return_value = "Here is the answer based on sources."
        result = synthesise(
            _state(retrieved_chunks=[{"path": "doc.md", "snippet": "content"}]),
            llm_backend=mock_backend,
        )
        assert "answer" in result["synthesis"].lower()

    @pytest.mark.unit
    def test_handles_llm_failure(self) -> None:
        mock_backend = MagicMock()
        mock_backend.chat.side_effect = RuntimeError("down")
        result = synthesise(
            _state(retrieved_chunks=[{"path": "a.md"}]),
            llm_backend=mock_backend,
        )
        assert "failed" in result["synthesis"].lower()

    @pytest.mark.unit
    def test_synthesise_carries_confidence_from_state(self) -> None:
        """S18-5: synthesise must re-emit confidence from state so it reaches the final result."""
        mock_backend = MagicMock()
        mock_backend.chat.return_value = "Synthesised answer."
        result = synthesise(
            _state(confidence=0.85, retrieved_chunks=[{"path": "a.md", "snippet": "x"}]),
            llm_backend=mock_backend,
        )
        assert "confidence" in result
        assert result["confidence"] == pytest.approx(0.85)

    @pytest.mark.unit
    def test_synthesise_carries_confidence_on_failure(self) -> None:
        """S18-5: even on LLM failure, confidence from state is preserved."""
        mock_backend = MagicMock()
        mock_backend.chat.side_effect = RuntimeError("down")
        result = synthesise(
            _state(confidence=0.42, retrieved_chunks=[{"path": "a.md"}]),
            llm_backend=mock_backend,
        )
        assert result["confidence"] == pytest.approx(0.42)

    @pytest.mark.unit
    def test_synthesise_completes_enumeration_from_cohesive_source(self) -> None:
        """#437 — when the accumulated chunks cohere on one enumerable source,
        synthesise grounds the LLM in the COMPLETE ordered source, so a
        list-of-techniques is answered whole rather than clipped to the top
        snippets.

        Sabotage-proof (executed 2026-07-03): reverting the
        ``_augment_with_enumeration`` call in ``synthesise`` to a no-op leaves
        the LLM grounded only in the top chunks, and the ``Pretend-to-Own`` /
        ``Re-label`` assertions on the prompt fail.
        """
        source = "reflib://methods.md"
        techniques = ["Mechanical Turk", "Pinocchio", "Fake Door", "Pretend-to-Own", "Re-label"]
        # Only the first two techniques reach the accumulated (top) chunks.
        chunks = [
            {
                "path": f"{source}#{i}",
                "snippet": f"- {techniques[i]}: a technique for validating demand before building.",
                "source_ref": SourceRef.of(path=f"{source}#{i}", source_uri=source).to_envelope(),
            }
            for i in range(2)
        ]

        def fake_expand(uri: str) -> ExpandOutput:
            # The full source carries every technique, ordered across two chunks.
            first = "\n".join(f"- {name}" for name in techniques[:3])
            second = "\n".join(f"- {name}" for name in techniques[3:])
            return ExpandOutput(
                source_uri=uri,
                chunks=[
                    ExpandedChunk(path=f"{uri}#0", seq=0, text=first, tokens=10, source_uri=uri),
                    ExpandedChunk(path=f"{uri}#1", seq=1, text=second, tokens=10, source_uri=uri),
                ],
            )

        fake_llm = FakeLLMBackend(chat_response="Synthesised answer citing the sources.")
        result = synthesise(
            _state(retrieved_chunks=chunks),
            llm_backend=fake_llm,
            deps=SynthesiseDeps(expand_fn=fake_expand),
        )

        assert result["synthesis"] == "Synthesised answer citing the sources."
        prompt = fake_llm.chat_calls[0]["messages"][1]["content"]
        for name in techniques:
            assert name in prompt, f"synthesis prompt dropped an enumerated technique: {name!r}"
        # The clipped techniques prove the fix is load-bearing.
        assert "Pretend-to-Own" in prompt
        assert "Re-label" in prompt


@pytest.mark.unit
class TestRouteAfterEvaluation:
    @pytest.mark.unit
    def test_sufficient_routes_to_synthesise(self) -> None:
        assert route_after_evaluation(_state(confidence=0.8)) == "synthesise"

    @pytest.mark.unit
    def test_insufficient_with_turns_left_routes_to_refine(self) -> None:
        assert route_after_evaluation(_state(confidence=0.3, turns=1, max_turns=4)) == "refine_query"

    @pytest.mark.unit
    def test_insufficient_at_max_turns_routes_to_synthesise(self) -> None:
        """When turns are exhausted, synthesise anyway (best effort) instead of giving up."""
        assert route_after_evaluation(_state(confidence=0.3, turns=3, max_turns=4)) == "synthesise"

    @pytest.mark.unit
    def test_threshold_boundary(self) -> None:
        assert route_after_evaluation(_state(confidence=0.5)) == "synthesise"
        assert route_after_evaluation(_state(confidence=0.49)) == "refine_query"
