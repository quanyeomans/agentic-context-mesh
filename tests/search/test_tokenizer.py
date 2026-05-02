"""Tests for kairix.core.search.tokenizer — shared FTS5 query tokenization."""

from __future__ import annotations

import pytest

from kairix.core.search.tokenizer import tokenize_fts_query


class TestTokenizeFtsQuery:
    """Tests for the unified FTS5 query tokenizer."""

    @pytest.mark.unit
    def test_bare_style_returns_space_separated_tokens(self):
        """Bare style: simple space-separated lowercase tokens."""
        result = tokenize_fts_query("Architecture Decision Record", style="bare")
        assert result == "architecture decision record"

    @pytest.mark.unit
    def test_prefix_style_returns_quoted_prefix_match(self):
        """Prefix style: each token quoted with wildcard, AND-joined."""
        result = tokenize_fts_query("Architecture Decision", style="prefix")
        assert result == '"architecture"* AND "decision"*'

    @pytest.mark.unit
    def test_quoted_style_returns_exact_quoted_tokens(self):
        """Quoted style: each token quoted without wildcard, AND-joined."""
        result = tokenize_fts_query("Architecture Decision", style="quoted")
        assert result == '"architecture" AND "decision"'

    @pytest.mark.unit
    def test_empty_query_returns_none(self):
        """Empty or whitespace-only query returns None."""
        assert tokenize_fts_query("") is None
        assert tokenize_fts_query("   ") is None

    @pytest.mark.unit
    def test_stop_words_removed(self):
        """Common stop words are filtered out."""
        result = tokenize_fts_query("what is the best architecture", style="bare")
        # "what", "is", "the" are stop words; "best" is too short? No, 4 chars.
        # Actually "best" is not a stop word. Let's check: only "architecture" and "best" remain
        assert "what" not in result
        assert "the" not in result
        assert "is" not in result
        assert "architecture" in result
        assert "best" in result

    @pytest.mark.unit
    def test_hyphens_treated_as_separators(self):
        """Hyphens in tokens are split into separate tokens."""
        result = tokenize_fts_query("ADR-012", style="prefix")
        assert '"adr"*' in result
        assert '"012"*' in result

    @pytest.mark.unit
    def test_underscores_treated_as_separators(self):
        """Underscores in tokens are split into separate tokens."""
        result = tokenize_fts_query("my_document_name", style="bare")
        assert "my" not in result  # "my" is a stop word
        assert "document" in result
        assert "name" in result

    @pytest.mark.unit
    def test_short_tokens_filtered(self):
        """Single-character tokens are removed."""
        result = tokenize_fts_query("a b cd ef", style="bare")
        # "a" is stop word, "b" is 1 char, "cd" and "ef" are 2 chars (kept)
        assert result == "cd ef"

    @pytest.mark.unit
    def test_all_stop_words_returns_none(self):
        """Query with only stop words returns None."""
        assert tokenize_fts_query("the is a") is None

    @pytest.mark.unit
    def test_default_style_is_prefix(self):
        """Default style parameter is prefix."""
        result = tokenize_fts_query("architecture decision")
        assert result == '"architecture"* AND "decision"*'

    @pytest.mark.unit
    def test_apostrophes_treated_as_separators(self):
        """Apostrophes (straight and curly) are split."""
        result = tokenize_fts_query("don\u2019t won't", style="bare")
        assert "don" in result
        assert "won" in result
