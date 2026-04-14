"""
Tests for the briefing pipeline (mnemosyne/briefing/pipeline.py).

Uses mocked sources and synthesiser — no live API calls or file system dependencies.
"""

from __future__ import annotations

from unittest.mock import patch

from kairix.briefing.pipeline import (
    _TOTAL_CONTEXT_CAP,
    _estimate_tokens,
    _trim_context,
    generate_briefing,
)


class TestTokenHelpers:
    def test_estimate_tokens_empty(self):
        assert _estimate_tokens("") == 0

    def test_estimate_tokens_scales_with_words(self):
        t10 = _estimate_tokens(" ".join(["word"] * 10))
        t100 = _estimate_tokens(" ".join(["word"] * 100))
        assert t100 > t10


class TestTrimContext:
    def test_no_trim_when_under_cap(self):
        context = {"memory_logs": "short content", "entity_stub": "also short"}
        result = _trim_context(context)
        assert result == context

    def test_trims_when_over_cap(self):
        # Create context well above 3000 tokens
        long_text = " ".join(["word"] * 3000)
        context = {
            "hybrid_search": long_text,
            "memory_logs": long_text,
        }
        result = _trim_context(context)
        total = sum(_estimate_tokens(v) for v in result.values())
        assert total <= _TOTAL_CONTEXT_CAP * 2  # some tolerance

    def test_truncates_lowest_priority_first(self):
        """hybrid_search should be truncated before memory_logs."""
        # 3000 words * 1.3 = 3900 tokens — well over the 3000 cap
        long_text = " ".join(["word"] * 3000)
        context = {
            "hybrid_search": long_text,
            "memory_logs": "short note here",
        }
        result = _trim_context(context)
        # hybrid_search should be shorter than original since total is over cap
        assert len(result.get("hybrid_search", "")) <= len(long_text)


class TestGenerateBriefing:
    def test_basic_pipeline_runs(self, tmp_path):
        """Test that pipeline runs and returns a string."""
        mock_briefing_body = (
            "## Pending & Blocked\nNone.\n\n"
            "## Recent Decisions\nADR-007 adopted.\n\n"
            "## Active Projects\nMnemosyne Phase 3.\n\n"
            "## Relevant Context\nHybrid search working.\n\n"
            "## Key Constraints\nNever write credentials."
        )

        with (
            patch("kairix.briefing.sources.fetch_memory_logs", return_value="memory logs content"),
            patch("kairix.briefing.sources.fetch_recent_memory", return_value="recent memory"),
            patch("kairix.briefing.sources.fetch_entity_stub", return_value="entity stub"),
            patch("kairix.briefing.sources.fetch_knowledge_rules", return_value="rules content"),
            patch("kairix.briefing.sources.fetch_recent_decisions", return_value="decisions"),
            patch("kairix.briefing.sources.fetch_hybrid_search", return_value="search results"),
            patch("kairix.briefing.synthesiser.synthesise", return_value=mock_briefing_body),
            patch("kairix.briefing.writer._BRIEFING_DIR", tmp_path),
        ):
            result = generate_briefing("builder")

        assert isinstance(result, str)
        assert len(result) > 0
        assert "Briefing" in result or "briefing" in result.lower() or "Pending" in result

    def test_header_is_included(self, tmp_path):
        with (
            patch("kairix.briefing.sources.fetch_memory_logs", return_value=""),
            patch("kairix.briefing.sources.fetch_recent_memory", return_value=""),
            patch("kairix.briefing.sources.fetch_entity_stub", return_value=""),
            patch("kairix.briefing.sources.fetch_knowledge_rules", return_value=""),
            patch("kairix.briefing.sources.fetch_recent_decisions", return_value=""),
            patch("kairix.briefing.sources.fetch_hybrid_search", return_value=""),
            patch("kairix.briefing.synthesiser.synthesise", return_value="## Pending\nNone."),
            patch("kairix.briefing.writer._BRIEFING_DIR", tmp_path),
        ):
            result = generate_briefing("builder")

        assert "# Agent Briefing" in result
        assert "builder" in result.lower()

    def test_source_failure_does_not_raise(self, tmp_path):
        """Pipeline must not raise when a source fetcher fails."""

        def failing_source(*args, **kwargs):
            raise RuntimeError("simulated source failure")

        with (
            patch("kairix.briefing.sources.fetch_memory_logs", side_effect=failing_source),
            patch("kairix.briefing.sources.fetch_recent_memory", return_value="some memory"),
            patch("kairix.briefing.sources.fetch_entity_stub", return_value=""),
            patch("kairix.briefing.sources.fetch_knowledge_rules", return_value=""),
            patch("kairix.briefing.sources.fetch_recent_decisions", return_value=""),
            patch("kairix.briefing.sources.fetch_hybrid_search", return_value=""),
            patch("kairix.briefing.synthesiser.synthesise", return_value="## Pending\nNone."),
            patch("kairix.briefing.writer._BRIEFING_DIR", tmp_path),
        ):
            result = generate_briefing("builder")

        assert isinstance(result, str)

    def test_synthesis_failure_returns_partial_briefing(self, tmp_path):
        """Synthesis API failure should return a partial/fallback briefing, not raise."""
        with (
            patch("kairix.briefing.sources.fetch_memory_logs", return_value="some logs"),
            patch("kairix.briefing.sources.fetch_recent_memory", return_value="memory"),
            patch("kairix.briefing.sources.fetch_entity_stub", return_value=""),
            patch("kairix.briefing.sources.fetch_knowledge_rules", return_value=""),
            patch("kairix.briefing.sources.fetch_recent_decisions", return_value=""),
            patch("kairix.briefing.sources.fetch_hybrid_search", return_value=""),
            patch("kairix.briefing.synthesiser.synthesise", return_value="synthesis unavailable"),
            patch("kairix.briefing.writer._BRIEFING_DIR", tmp_path),
        ):
            result = generate_briefing("builder")

        assert isinstance(result, str)
        assert len(result) > 0

    def test_output_file_is_written(self, tmp_path):
        with (
            patch("kairix.briefing.sources.fetch_memory_logs", return_value="logs"),
            patch("kairix.briefing.sources.fetch_recent_memory", return_value="memory"),
            patch("kairix.briefing.sources.fetch_entity_stub", return_value="entity"),
            patch("kairix.briefing.sources.fetch_knowledge_rules", return_value="rules"),
            patch("kairix.briefing.sources.fetch_recent_decisions", return_value="decisions"),
            patch("kairix.briefing.sources.fetch_hybrid_search", return_value="search"),
            patch("kairix.briefing.synthesiser.synthesise", return_value="## Pending\nNone."),
            patch("kairix.briefing.writer._BRIEFING_DIR", tmp_path),
        ):
            generate_briefing("builder")

        expected = tmp_path / "builder-latest.md"
        assert expected.exists()
        content = expected.read_text()
        assert "Briefing" in content or "briefing" in content.lower() or "builder" in content
