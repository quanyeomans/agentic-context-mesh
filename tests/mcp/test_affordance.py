"""
Tests for KFEAT-010 MCP affordance improvements (AFF-1 through AFF-3, AFF-5).

Covers:
  AFF-1  Automatic budget inference
  AFF-2  Plain-language tool descriptions
  AFF-3  Entity-first hint in search results
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from kairix.agents.mcp.server import (
    _extract_entity_name,
    _infer_budget,
    tool_entity,
    tool_prep,
    tool_search,
    tool_timeline,
    tool_usage_guide,
)

# ---------------------------------------------------------------------------
# AFF-1: Budget inference
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBudgetInference:
    """_infer_budget selects the right token budget by query intent."""

    @pytest.mark.unit
    def test_entity_intent_returns_1500(self) -> None:
        from kairix.core.search.intent import QueryIntent

        assert _infer_budget("tell me about Acme", 3000, classify_fn=lambda q: QueryIntent.ENTITY) == 1500

    @pytest.mark.unit
    def test_keyword_intent_returns_1500(self) -> None:
        from kairix.core.search.intent import QueryIntent

        assert _infer_budget("KFEAT-010", 3000, classify_fn=lambda q: QueryIntent.KEYWORD) == 1500

    @pytest.mark.unit
    def test_research_query_returns_5000(self) -> None:
        from kairix.core.search.intent import QueryIntent

        assert (
            _infer_budget(
                "research the competitive landscape",
                3000,
                classify_fn=lambda q: QueryIntent.SEMANTIC,
            )
            == 5000
        )

    @pytest.mark.unit
    def test_compare_query_returns_5000(self) -> None:
        from kairix.core.search.intent import QueryIntent

        assert (
            _infer_budget(
                "compare the two frameworks",
                3000,
                classify_fn=lambda q: QueryIntent.SEMANTIC,
            )
            == 5000
        )

    @pytest.mark.unit
    def test_analyse_query_returns_5000(self) -> None:
        from kairix.core.search.intent import QueryIntent

        assert (
            _infer_budget(
                "analyse the quarterly results",
                3000,
                classify_fn=lambda q: QueryIntent.SEMANTIC,
            )
            == 5000
        )

    @pytest.mark.unit
    def test_comprehensive_query_returns_5000(self) -> None:
        from kairix.core.search.intent import QueryIntent

        assert (
            _infer_budget(
                "give me a comprehensive overview",
                3000,
                classify_fn=lambda q: QueryIntent.SEMANTIC,
            )
            == 5000
        )

    @pytest.mark.unit
    def test_detailed_query_returns_5000(self) -> None:
        from kairix.core.search.intent import QueryIntent

        assert (
            _infer_budget(
                "detailed breakdown of costs",
                3000,
                classify_fn=lambda q: QueryIntent.SEMANTIC,
            )
            == 5000
        )

    @pytest.mark.unit
    def test_default_returns_3000(self) -> None:
        from kairix.core.search.intent import QueryIntent

        assert (
            _infer_budget(
                "how does the build system work",
                3000,
                classify_fn=lambda q: QueryIntent.SEMANTIC,
            )
            == 3000
        )

    @pytest.mark.unit
    def test_explicit_override_preserved(self) -> None:
        """Non-default explicit budget is returned unchanged regardless of intent."""
        assert _infer_budget("tell me about Acme", 2000) == 2000
        assert _infer_budget("research everything", 1000) == 1000

    @pytest.mark.unit
    def test_classify_failure_falls_back_to_heuristics(self) -> None:
        """When classify raises, research words still trigger 5000."""

        def _failing_classify(q):
            raise RuntimeError("broken")

        assert _infer_budget("research the topic", 3000, classify_fn=_failing_classify) == 5000

    @pytest.mark.unit
    def test_classify_failure_default_3000(self) -> None:
        """When classify raises and no research words, default is 3000."""

        def _failing_classify(q):
            raise RuntimeError("broken")

        assert _infer_budget("hello world", 3000, classify_fn=_failing_classify) == 3000


# ---------------------------------------------------------------------------
# AFF-2: Plain-language tool descriptions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlainLanguageDocstrings:
    """Verify tool docstrings are written at grade 8 reading level."""

    @pytest.mark.unit
    def test_tool_search_docstring(self) -> None:
        doc = inspect.getdoc(tool_search) or ""
        first_sentence = doc.split(".")[0]
        assert "Search for anything" in first_sentence

    @pytest.mark.unit
    def test_tool_entity_docstring(self) -> None:
        doc = inspect.getdoc(tool_entity) or ""
        first_sentence = doc.split(".")[0]
        assert "Look up" in first_sentence

    @pytest.mark.unit
    def test_tool_prep_docstring(self) -> None:
        doc = inspect.getdoc(tool_prep) or ""
        first_sentence = doc.split(".")[0]
        assert "summary" in first_sentence.lower()

    @pytest.mark.unit
    def test_tool_timeline_docstring(self) -> None:
        doc = inspect.getdoc(tool_timeline) or ""
        first_sentence = doc.split(".")[0]
        assert "date" in first_sentence.lower()

    @pytest.mark.unit
    def test_tool_usage_guide_docstring(self) -> None:
        doc = inspect.getdoc(tool_usage_guide) or ""
        first_sentence = doc.split(".")[0]
        assert "help" in first_sentence.lower() or "guide" in first_sentence.lower()

    @pytest.mark.unit
    def test_no_temporal_as_leading_term(self) -> None:
        """Docstring first sentences should not lead with jargon like 'temporal'."""
        tools = [tool_search, tool_entity, tool_prep, tool_timeline, tool_usage_guide]
        for fn in tools:
            doc = inspect.getdoc(fn) or ""
            first_sentence = doc.split(".")[0].lower()
            assert not first_sentence.startswith("temporal"), (
                f"{fn.__name__} docstring starts with 'temporal': {first_sentence}"
            )


# ---------------------------------------------------------------------------
# AFF-3: Entity-first hint in search results
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEntityFirstHint:
    """When intent is ENTITY, the entity graph result appears first."""

    @pytest.mark.unit
    def test_entity_intent_prepends_entity_graph_result(self) -> None:
        mock_budgeted = SimpleNamespace(
            result=SimpleNamespace(path="notes/acme.md", boosted_score=0.7),
            content="some context about Acme",
            token_estimate=20,
        )
        mock_result = SimpleNamespace(
            query="tell me about Acme",
            intent=SimpleNamespace(value="entity"),
            results=[mock_budgeted],
            total_tokens=20,
            latency_ms=30.0,
            error="",
        )

        entity_card = {
            "id": "acme",
            "name": "Acme",
            "type": "Organisation",
            "summary": "A health org",
            "vault_path": "02-Areas/00-Clients/Acme/Acme.md",
        }

        result = tool_search(
            query="tell me about Acme",
            search_fn=lambda **kwargs: mock_result,
            entity_card_fn=lambda name: entity_card,
        )

        assert len(result["results"]) == 2
        first = result["results"][0]
        assert first["source"] == "entity_graph"
        assert first["entity"]["name"] == "Acme"
        assert first["entity"]["type"] == "Organisation"

    @pytest.mark.unit
    def test_entity_not_found_no_prepend(self) -> None:
        """When entity lookup fails, no entity_graph result is prepended."""
        mock_budgeted = SimpleNamespace(
            result=SimpleNamespace(path="notes/unknown.md", boosted_score=0.5),
            content="some text",
            token_estimate=10,
        )
        mock_result = SimpleNamespace(
            query="tell me about UnknownCorp",
            intent=SimpleNamespace(value="entity"),
            results=[mock_budgeted],
            total_tokens=10,
            latency_ms=20.0,
            error="",
        )

        result = tool_search(
            query="tell me about UnknownCorp",
            search_fn=lambda **kwargs: mock_result,
            entity_card_fn=lambda name: None,
        )

        assert len(result["results"]) == 1
        assert result["results"][0].get("source") is None

    @pytest.mark.unit
    def test_non_entity_intent_no_prepend(self) -> None:
        """Non-entity intents never trigger entity-first hint."""
        mock_result = SimpleNamespace(
            query="how to deploy",
            intent=SimpleNamespace(value="procedural"),
            results=[],
            total_tokens=0,
            latency_ms=5.0,
            error="",
        )

        result = tool_search(
            query="how to deploy",
            search_fn=lambda **kwargs: mock_result,
        )

        assert all(r.get("source") != "entity_graph" for r in result["results"])


# ---------------------------------------------------------------------------
# _extract_entity_name helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractEntityName:
    @pytest.mark.unit
    def test_strips_what_is(self) -> None:
        assert _extract_entity_name("what is Acme") == "Acme"

    @pytest.mark.unit
    def test_strips_who_is(self) -> None:
        assert _extract_entity_name("who is Alice") == "Alice"

    @pytest.mark.unit
    def test_strips_tell_me_about(self) -> None:
        assert _extract_entity_name("tell me about Acme Corp") == "Acme Corp"

    @pytest.mark.unit
    def test_strips_trailing_punctuation(self) -> None:
        assert _extract_entity_name("what is Acme?") == "Acme"

    @pytest.mark.unit
    def test_plain_name(self) -> None:
        assert _extract_entity_name("Acme") == "Acme"
