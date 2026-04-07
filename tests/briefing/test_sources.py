"""
Tests for briefing source fetchers (mnemosyne/briefing/sources.py).

All tests use fixtures or temporary directories — no live API calls.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from mnemosyne.briefing.sources import (
    _estimate_tokens,
    _truncate_to_tokens,
    fetch_entity_stub,
    fetch_knowledge_rules,
    fetch_memory_logs,
    fetch_recent_decisions,
    fetch_recent_memory,
)

# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------


class TestTokenHelpers:
    def test_estimate_tokens_empty(self):
        assert _estimate_tokens("") == 0

    def test_estimate_tokens_small(self):
        # "hello world" = 2 words * 1.3 = 2
        count = _estimate_tokens("hello world")
        assert count >= 2

    def test_truncate_to_tokens_short(self):
        text = "hello world"
        result = _truncate_to_tokens(text, 100)
        assert result == text  # no truncation needed

    def test_truncate_to_tokens_truncates(self):
        words = ["word"] * 1000
        text = " ".join(words)
        result = _truncate_to_tokens(text, 50)
        assert len(result) < len(text)
        assert "[truncated]" in result


# ---------------------------------------------------------------------------
# Memory log tests
# ---------------------------------------------------------------------------


class TestFetchMemoryLogs:
    def test_returns_empty_for_missing_dir(self):
        result = fetch_memory_logs("nonexistent_agent_xyz")
        assert result == ""

    def test_reads_tagged_items(self, tmp_path):
        today = date.today()
        memory_dir = tmp_path / "builder" / "memory"
        memory_dir.mkdir(parents=True)

        content = (
            "## Session\n"
            "[pending] Fix the RRF bug\n"
            "[blocked] Waiting for Azure quota\n"
            "[action: send summary to Shape]\n"
            "Normal log entry\n"
        )
        (memory_dir / f"{today.isoformat()}.md").write_text(content)

        with patch("mnemosyne.briefing.sources._WORKSPACE_ROOT", tmp_path):
            result = fetch_memory_logs("builder")

        assert "[pending]" in result or "pending" in result.lower()
        assert "[blocked]" in result or "blocked" in result.lower()

    def test_handles_read_error_gracefully(self, tmp_path):
        memory_dir = tmp_path / "builder" / "memory"
        memory_dir.mkdir(parents=True)
        today = date.today()
        bad_file = memory_dir / f"{today.isoformat()}.md"
        bad_file.write_bytes(b"\xff\xfe invalid utf-8")

        with patch("mnemosyne.briefing.sources._WORKSPACE_ROOT", tmp_path):
            result = fetch_memory_logs("builder")
        # Should not raise — may return empty or partial content
        assert isinstance(result, str)

    def test_respects_token_cap(self, tmp_path):
        today = date.today()
        memory_dir = tmp_path / "builder" / "memory"
        memory_dir.mkdir(parents=True)

        # Create large content
        content = "\n".join([f"[pending] item {i}" for i in range(1000)])
        (memory_dir / f"{today.isoformat()}.md").write_text(content)

        with patch("mnemosyne.briefing.sources._WORKSPACE_ROOT", tmp_path):
            result = fetch_memory_logs("builder", max_tokens=50)

        assert _estimate_tokens(result) <= 100  # some buffer


# ---------------------------------------------------------------------------
# Recent memory tests
# ---------------------------------------------------------------------------


class TestFetchRecentMemory:
    def test_returns_empty_for_missing_dir(self):
        result = fetch_recent_memory("nonexistent_agent_xyz")
        assert result == ""

    def test_reads_today_and_yesterday(self, tmp_path):
        today = date.today()
        yesterday = today - timedelta(days=1)
        memory_dir = tmp_path / "builder" / "memory"
        memory_dir.mkdir(parents=True)

        (memory_dir / f"{today.isoformat()}.md").write_text("Today's content here")
        (memory_dir / f"{yesterday.isoformat()}.md").write_text("Yesterday content here")

        with patch("mnemosyne.briefing.sources._WORKSPACE_ROOT", tmp_path):
            result = fetch_recent_memory("builder")

        assert today.isoformat() in result
        assert yesterday.isoformat() in result


# ---------------------------------------------------------------------------
# Entity stub tests
# ---------------------------------------------------------------------------


class TestFetchEntityStub:
    def test_returns_empty_for_missing_entity(self):
        result = fetch_entity_stub("nonexistent_agent_xyz")
        assert result == ""

    def test_reads_concept_stub(self, tmp_path):
        entity_dir = tmp_path / "04-Agent-Knowledge" / "entities" / "concept"
        entity_dir.mkdir(parents=True)
        (entity_dir / "builder.md").write_text("# Builder\nThe engineering agent.")

        with patch("mnemosyne.briefing.sources._VAULT_ROOT", tmp_path):
            result = fetch_entity_stub("builder")

        assert "Builder" in result or "builder" in result.lower()


# ---------------------------------------------------------------------------
# Knowledge rules tests
# ---------------------------------------------------------------------------


class TestFetchKnowledgeRules:
    def test_returns_empty_for_missing_rules(self, tmp_path):
        # Use an isolated vault root with no rules files
        with patch("mnemosyne.briefing.sources._VAULT_ROOT", tmp_path):
            result = fetch_knowledge_rules("nonexistent_agent_xyz")
        assert result == ""

    def test_reads_rules_file(self, tmp_path):
        rules_dir = tmp_path / "04-Agent-Knowledge" / "builder"
        rules_dir.mkdir(parents=True)
        (rules_dir / "rules.md").write_text("# Rules\n1. Never commit secrets\n2. Always test")

        with patch("mnemosyne.briefing.sources._VAULT_ROOT", tmp_path):
            result = fetch_knowledge_rules("builder")

        assert "secrets" in result.lower() or "rules" in result.lower()


# ---------------------------------------------------------------------------
# Recent decisions tests
# ---------------------------------------------------------------------------


class TestFetchRecentDecisions:
    def test_returns_empty_for_missing_decisions(self):
        result = fetch_recent_decisions("nonexistent_agent_xyz")
        assert result == ""

    def test_reads_decisions_file(self, tmp_path):
        decisions_dir = tmp_path / "04-Agent-Knowledge" / "builder"
        decisions_dir.mkdir(parents=True)
        (decisions_dir / "decisions.md").write_text(
            "# Decisions\n- ADR-001: Use Azure embeddings\n- ADR-002: SQLite for entity facts"
        )

        with patch("mnemosyne.briefing.sources._VAULT_ROOT", tmp_path):
            result = fetch_recent_decisions("builder")

        assert "ADR" in result or "decision" in result.lower()

    def test_handles_missing_entities_db(self, tmp_path):
        # Should not raise when entities.db doesn't exist
        fake_db = tmp_path / "nonexistent.db"
        with patch("mnemosyne.briefing.sources._ENTITIES_DB", fake_db):
            result = fetch_recent_decisions("builder")
        assert isinstance(result, str)
