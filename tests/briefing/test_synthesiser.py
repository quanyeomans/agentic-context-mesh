"""
Tests for the briefing synthesiser (kairix/briefing/synthesiser.py).

Uses canonical FakeLLMBackend from tests/fakes.py — no MagicMock.
"""

from __future__ import annotations

import pytest

from kairix.agents.briefing.synthesiser import fallback_briefing, synthesise
from tests.fakes import FakeLLMBackend


def _make_backend(return_value: str | None = None, side_effect: BaseException | None = None) -> FakeLLMBackend:
    """Build a FakeLLMBackend with the given chat behaviour."""
    if side_effect is not None:
        return FakeLLMBackend(chat_raises=side_effect)
    return FakeLLMBackend(chat_response=return_value or "")


@pytest.mark.unit
class TestSynthesise:
    @pytest.mark.unit
    def test_successful_synthesis(self):
        mock_body = (
            "## Pending & Blocked\n- Fix the RRF bug [pending]\n\n"
            "## Recent Decisions\n- ADR-007: Use RRF for fusion\n\n"
            "## Active Projects\n- Kairix Phase 3\n\n"
            "## Relevant Context\nHybrid search working well.\n\n"
            "## Key Constraints\n- Never write credentials to disk"
        )
        context = {
            "memory_logs": "[pending] Fix the RRF bug",
            "recent_decisions": "ADR-007: Use RRF",
            "knowledge_rules": "Never write credentials to disk",
        }
        result = synthesise("builder", context, llm_backend=_make_backend(return_value=mock_body))
        assert "Pending" in result
        assert "Decisions" in result or "ADR" in result

    @pytest.mark.unit
    def test_empty_context_returns_fallback(self):
        result = synthesise("builder", {})
        assert "synthesis unavailable" in result.lower() or "fallback" in result.lower() or "failed" in result.lower()

    @pytest.mark.unit
    def test_api_failure_returns_fallback(self):
        result = synthesise(
            "builder",
            {"memory_logs": "some content"},
            llm_backend=_make_backend(return_value=""),
        )
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.unit
    def test_api_exception_returns_fallback(self):
        result = synthesise(
            "builder",
            {"memory_logs": "some content"},
            llm_backend=_make_backend(side_effect=Exception("API down")),
        )
        assert isinstance(result, str)
        # Should contain fallback message
        assert "synthesis" in result.lower() or "failed" in result.lower()

    @pytest.mark.unit
    def test_offline_synthesis_degrades_to_non_llm_brief(self):
        """PLA-267: when the chat call fails (provider offline), the brief
        degrades to the gathered non-LLM context — the 5/6 sources that need
        no LLM still surface their content, instead of empty
        '(synthesis unavailable)' placeholders.

        Sabotage-proof (executed): reverting the synthesiser's offline branch
        to ``fallback_briefing`` drops the gathered content and the three
        content assertions below fail. Restored.
        """
        context = {
            "memory_logs": "[pending] ship the connector refactor",
            "recent_decisions": "ADR-007: adopt RRF fusion",
            "knowledge_rules": "Never write credentials to disk",
        }
        result = synthesise(
            "builder",
            context,
            llm_backend=_make_backend(side_effect=RuntimeError("provider offline")),
        )

        # The gathered (non-LLM) content survives the offline path.
        assert "ship the connector refactor" in result
        assert "ADR-007: adopt RRF fusion" in result
        assert "Never write credentials to disk" in result
        # It is NOT the empty placeholder brief.
        assert "synthesis unavailable - check memory logs manually" not in result

    @pytest.mark.unit
    def test_whitespace_only_context_falls_back_to_empty_brief(self):
        """When the gathered context is only whitespace (no real content to
        surface), the offline degrade returns the empty-placeholder fallback
        rather than a 'showing gathered context' note over blank sections —
        there is genuinely nothing to show.

        Pins the all-empty guard in ``_degrade_to_non_llm_brief``: mutating
        its ``value and value.strip()`` to ``value or value.strip()`` treats a
        whitespace-only value as content and emits the section brief instead
        of the fallback, which this assertion then catches. No ``llm_backend``
        is passed so the provider-unconfigured degrade path runs (mirrors
        ``test_empty_context_returns_fallback``).
        """
        result = synthesise("builder", {"memory_logs": "   ", "knowledge_rules": "\n\t "})
        assert "synthesis unavailable - check memory logs manually" in result

    @pytest.mark.unit
    def test_context_is_included_in_prompt(self):
        """Verify context content is passed to the LLM."""
        context = {"memory_logs": "UNIQUE_MARKER_12345"}
        backend = FakeLLMBackend(chat_response="## Pending & Blocked\nNone.")
        synthesise("builder", context, llm_backend=backend)

        # FakeLLMBackend captures every chat() call with its messages.
        all_messages = [m for call in backend.chat_calls for m in call["messages"]]
        full_prompt = " ".join(str(m) for m in all_messages)
        assert "UNIQUE_MARKER_12345" in full_prompt

    @pytest.mark.unit
    def test_long_context_is_truncated(self):
        """Verify very long context doesn't exceed limits."""
        context = {"memory_logs": " ".join(["word"] * 5000)}
        backend = FakeLLMBackend(chat_response="## Pending & Blocked\nNone.")
        synthesise("builder", context, llm_backend=backend)

        all_messages = [m for call in backend.chat_calls for m in call["messages"]]
        full_prompt = " ".join(str(m) for m in all_messages)
        assert len(full_prompt) < 25000, f"context not truncated: {len(full_prompt)} chars"


@pytest.mark.unit
class TestFallbackBriefing:
    @pytest.mark.unit
    def test_contains_all_sections(self):
        result = fallback_briefing("builder", "test error")
        assert "Pending" in result
        assert "Decisions" in result
        assert "Active Projects" in result
        assert "Key Constraints" in result

    @pytest.mark.unit
    def test_includes_reason(self):
        result = fallback_briefing("builder", "network timeout")
        assert "network timeout" in result

    @pytest.mark.unit
    def test_includes_fallback_path(self):
        result = fallback_briefing("builder", "any error")
        assert "builder" in result
