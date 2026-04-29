"""
Tests for the briefing synthesiser (kairix/briefing/synthesiser.py).

Uses mocked Azure client — no live API calls.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kairix.agents.briefing.synthesiser import _fallback_briefing, synthesise


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
        with patch("kairix._azure.chat_completion", return_value=mock_body):
            result = synthesise("builder", context)
        assert "Pending" in result
        assert "Decisions" in result or "ADR" in result

    @pytest.mark.unit
    def test_empty_context_returns_fallback(self):
        result = synthesise("builder", {})
        assert "synthesis unavailable" in result.lower() or "fallback" in result.lower() or "failed" in result.lower()

    @pytest.mark.unit
    def test_api_failure_returns_fallback(self):
        with patch("kairix._azure.chat_completion", return_value=""):
            result = synthesise("builder", {"memory_logs": "some content"})
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.unit
    def test_api_exception_returns_fallback(self):
        with patch("kairix._azure.chat_completion", side_effect=Exception("API down")):
            result = synthesise("builder", {"memory_logs": "some content"})
        assert isinstance(result, str)
        # Should contain fallback message
        assert "synthesis" in result.lower() or "failed" in result.lower()

    @pytest.mark.unit
    def test_context_is_included_in_prompt(self):
        """Verify context content is passed to the LLM."""
        captured_messages = []

        def mock_chat(messages, max_tokens=800):
            captured_messages.extend(messages)
            return "## Pending & Blocked\nNone."

        context = {"memory_logs": "UNIQUE_MARKER_12345"}
        with patch("kairix._azure.chat_completion", side_effect=mock_chat):
            synthesise("builder", context)

        full_prompt = " ".join(str(m) for m in captured_messages)
        assert "UNIQUE_MARKER_12345" in full_prompt

    @pytest.mark.unit
    def test_long_context_is_truncated(self):
        """Verify very long context doesn't exceed limits."""
        context = {"memory_logs": " ".join(["word"] * 5000)}
        captured_messages = []

        def mock_chat(messages, max_tokens=800):
            captured_messages.extend(messages)
            return "## Pending & Blocked\nNone."

        with patch("kairix._azure.chat_completion", side_effect=mock_chat):
            synthesise("builder", context)

        full_prompt = " ".join(str(m) for m in captured_messages)
        # Should be truncated — context block should not contain all 5000 words
        assert "truncated" in full_prompt or len(full_prompt) < 25000


@pytest.mark.unit
class TestFallbackBriefing:
    @pytest.mark.unit
    def test_contains_all_sections(self):
        result = _fallback_briefing("builder", "test error")
        assert "Pending" in result
        assert "Decisions" in result
        assert "Active Projects" in result
        assert "Key Constraints" in result

    @pytest.mark.unit
    def test_includes_reason(self):
        result = _fallback_briefing("builder", "network timeout")
        assert "network timeout" in result

    @pytest.mark.unit
    def test_includes_fallback_path(self):
        result = _fallback_briefing("builder", "any error")
        assert "builder" in result
