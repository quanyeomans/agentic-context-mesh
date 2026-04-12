"""
Tests for the LLM judge (mnemosyne/classify/judge.py).

Uses mocked Azure client to test LLM classification without live API calls.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from kairix.classify.judge import classify_with_llm
from kairix.classify.rules import ClassificationResult


class TestClassifyWithLLM:
    def test_successful_classification(self):
        mock_response = json.dumps(
            {
                "type": "semantic-decision",
                "confidence": 0.85,
                "reason": "Contains 'we decided' and rationale",
            }
        )
        with patch("kairix._azure.chat_completion", return_value=mock_response):
            result = classify_with_llm("some ambiguous content", agent="builder")
        assert isinstance(result, ClassificationResult)
        assert result.type == "semantic-decision"
        assert result.confidence == 0.85
        assert not result.needs_confirmation

    def test_low_confidence_sets_needs_confirmation(self):
        mock_response = json.dumps(
            {
                "type": "episodic",
                "confidence": 0.55,
                "reason": "Could be episodic or procedural",
            }
        )
        with patch("kairix._azure.chat_completion", return_value=mock_response):
            result = classify_with_llm("ambiguous content", agent="builder")
        assert result.needs_confirmation is True
        assert result.confidence == 0.55

    def test_api_failure_returns_unknown(self):
        with patch("kairix._azure.chat_completion", return_value=""):
            result = classify_with_llm("some content", agent="builder")
        assert result.type == "unknown"
        assert result.confidence == 0.0
        assert result.needs_confirmation is True

    def test_json_parse_error_returns_unknown(self):
        with patch("kairix._azure.chat_completion", return_value="not valid json"):
            result = classify_with_llm("some content", agent="builder")
        assert result.type == "unknown"
        assert result.needs_confirmation is True

    def test_empty_content_returns_unknown(self):
        with patch("kairix._azure.chat_completion", return_value="{}"):
            result = classify_with_llm("", agent="builder")
        assert result.type == "unknown"
        assert result.needs_confirmation is True

    def test_code_fence_wrapped_json(self):
        mock_response = '```json\n{"type": "episodic", "confidence": 0.9, "reason": "timestamp"}\n```'
        with patch("kairix._azure.chat_completion", return_value=mock_response):
            result = classify_with_llm("## 09:15 did stuff", agent="builder")
        assert result.type == "episodic"
        assert result.confidence == 0.9

    def test_path_resolved_for_valid_type(self):
        mock_response = json.dumps(
            {
                "type": "procedural-rule",
                "confidence": 0.92,
                "reason": "contains normative constraint",
            }
        )
        with patch("kairix._azure.chat_completion", return_value=mock_response):
            result = classify_with_llm("never do X", agent="builder")
        assert result.target_path != ""
        assert "rules.md" in result.target_path

    def test_invalid_agent_raises(self):
        with pytest.raises(ValueError, match="Invalid agent"):
            classify_with_llm("some content", agent="invalid")

    def test_shared_agent_is_valid(self):
        mock_response = json.dumps(
            {
                "type": "semantic-fact",
                "confidence": 0.80,
                "reason": "infrastructure fact",
            }
        )
        with patch("kairix._azure.chat_completion", return_value=mock_response):
            result = classify_with_llm("endpoint: https://api.example.com", agent="shared")
        assert result.type == "semantic-fact"
