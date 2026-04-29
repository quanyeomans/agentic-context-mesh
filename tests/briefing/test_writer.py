"""
Tests for the briefing writer (kairix/briefing/writer.py).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kairix.agents.briefing.writer import write_briefing


@pytest.mark.unit
class TestWriteBriefing:
    @pytest.mark.unit
    def test_creates_output_file(self, tmp_path):
        with patch("kairix.agents.briefing.writer.BRIEFING_DIR", tmp_path):
            out = write_briefing("builder", "## Pending & Blocked\nNone.", sources_count=3, token_estimate=200)
        assert out.exists()
        assert out.name == "builder-latest.md"

    @pytest.mark.unit
    def test_creates_directory_if_missing(self, tmp_path):
        target_dir = tmp_path / "nested" / "briefing"
        assert not target_dir.exists()
        with patch("kairix.agents.briefing.writer.BRIEFING_DIR", target_dir):
            out = write_briefing("shape", "## Section\nContent", sources_count=2, token_estimate=100)
        assert target_dir.exists()
        assert out.exists()

    @pytest.mark.unit
    def test_file_contains_header(self, tmp_path):
        with patch("kairix.agents.briefing.writer.BRIEFING_DIR", tmp_path):
            out = write_briefing("builder", "## Pending\nNone.", sources_count=4, token_estimate=150)
        content = out.read_text()
        assert "# Agent Briefing — builder" in content
        assert "_Generated:" in content
        assert "Sources: 4" in content
        assert "Tokens: ~150" in content

    @pytest.mark.unit
    def test_file_contains_body(self, tmp_path):
        body = "## Pending & Blocked\n- Fix the bug\n\n## Recent Decisions\n- ADR-001"
        with patch("kairix.agents.briefing.writer.BRIEFING_DIR", tmp_path):
            out = write_briefing("builder", body)
        content = out.read_text()
        assert "Fix the bug" in content
        assert "ADR-001" in content

    @pytest.mark.unit
    def test_overwrites_existing_file(self, tmp_path):
        with patch("kairix.agents.briefing.writer.BRIEFING_DIR", tmp_path):
            write_briefing("builder", "First content", sources_count=1)
            out = write_briefing("builder", "Second content", sources_count=2)
        content = out.read_text()
        assert "Second content" in content
        assert "First content" not in content

    @pytest.mark.unit
    def test_correct_filename_per_agent(self, tmp_path):
        with patch("kairix.agents.briefing.writer.BRIEFING_DIR", tmp_path):
            b_out = write_briefing("builder", "b content")
            s_out = write_briefing("shape", "s content")
        assert b_out.name == "builder-latest.md"
        assert s_out.name == "shape-latest.md"

    @pytest.mark.unit
    def test_returns_path_object(self, tmp_path):
        with patch("kairix.agents.briefing.writer.BRIEFING_DIR", tmp_path):
            result = write_briefing("builder", "content")
        assert isinstance(result, Path)
