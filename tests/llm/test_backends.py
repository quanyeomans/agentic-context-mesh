"""
Tests for mnemosyne.llm — LLM backend abstraction (P1-2).
"""

from __future__ import annotations

from unittest.mock import patch

from mnemosyne.llm import AnthropicBackend, AzureOpenAIBackend, get_default_backend
from mnemosyne.llm.protocol import LLMBackend

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_azure_backend_conforms_to_protocol() -> None:
    backend = AzureOpenAIBackend()
    assert isinstance(backend, LLMBackend)


def test_anthropic_backend_conforms_to_protocol() -> None:
    backend = AnthropicBackend()
    assert isinstance(backend, LLMBackend)


def test_get_default_backend_returns_azure() -> None:
    backend = get_default_backend()
    assert isinstance(backend, AzureOpenAIBackend)


# ---------------------------------------------------------------------------
# AzureOpenAIBackend — delegates to _azure.py
# ---------------------------------------------------------------------------


def test_azure_backend_chat_delegates_to_azure() -> None:
    backend = AzureOpenAIBackend()
    messages = [{"role": "user", "content": "Hello"}]

    with patch("mnemosyne._azure.chat_completion", return_value="Hi there") as mock_chat:
        result = backend.chat(messages, max_tokens=100)

    mock_chat.assert_called_once_with(messages, max_tokens=100)
    assert result == "Hi there"


def test_azure_backend_chat_default_max_tokens() -> None:
    backend = AzureOpenAIBackend()
    messages = [{"role": "user", "content": "test"}]

    with patch("mnemosyne._azure.chat_completion", return_value="ok") as mock_chat:
        backend.chat(messages)

    mock_chat.assert_called_once_with(messages, max_tokens=800)


def test_azure_backend_embed_delegates_to_azure() -> None:
    backend = AzureOpenAIBackend()
    expected = [0.1, 0.2, 0.3]

    with patch("mnemosyne._azure.embed_text", return_value=expected) as mock_embed:
        result = backend.embed("some text")

    mock_embed.assert_called_once_with("some text")
    assert result == expected


def test_azure_backend_chat_returns_empty_string_on_failure() -> None:
    backend = AzureOpenAIBackend()

    with patch("mnemosyne._azure.chat_completion", return_value=""):
        result = backend.chat([{"role": "user", "content": "test"}])

    assert result == ""


def test_azure_backend_embed_returns_empty_list_on_failure() -> None:
    backend = AzureOpenAIBackend()

    with patch("mnemosyne._azure.embed_text", return_value=[]):
        result = backend.embed("text")

    assert result == []


# ---------------------------------------------------------------------------
# AnthropicBackend — stub
# ---------------------------------------------------------------------------


def test_anthropic_backend_chat_raises_not_implemented() -> None:
    import pytest

    backend = AnthropicBackend()
    with pytest.raises(NotImplementedError):
        backend.chat([{"role": "user", "content": "hi"}])


def test_anthropic_backend_embed_raises_not_implemented() -> None:
    import pytest

    backend = AnthropicBackend()
    with pytest.raises(NotImplementedError):
        backend.embed("text")


# ---------------------------------------------------------------------------
# Protocol usage pattern — callers receive LLMBackend, not concrete class
# ---------------------------------------------------------------------------


def _do_summarise(text: str, llm: LLMBackend) -> str:
    """Example of how production code should accept LLMBackend."""
    return llm.chat([{"role": "user", "content": f"Summarise: {text}"}])


def test_caller_accepts_protocol_type() -> None:
    backend = AzureOpenAIBackend()

    with patch("mnemosyne._azure.chat_completion", return_value="Summary."):
        result = _do_summarise("long document", backend)

    assert result == "Summary."


class _MockLLMBackend:
    """Minimal test double — satisfies the protocol without _azure."""

    def chat(self, messages: list[dict], max_tokens: int = 800) -> str:
        return "mock response"

    def embed(self, text: str) -> list[float]:
        return [0.0] * 1536


def test_mock_backend_satisfies_protocol() -> None:
    mock = _MockLLMBackend()
    assert isinstance(mock, LLMBackend)


def test_caller_works_with_mock_backend() -> None:
    mock = _MockLLMBackend()
    result = _do_summarise("text", mock)
    assert result == "mock response"
