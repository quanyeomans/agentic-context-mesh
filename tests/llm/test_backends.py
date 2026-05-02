"""
Tests for kairix.platform.llm — LLM backend abstraction (P1-2).

Uses injected callables via AzureOpenAIBackend constructor — no monkey-patching needed.
"""

from __future__ import annotations

import pytest

from kairix.platform.llm import AzureOpenAIBackend, get_default_backend
from kairix.platform.llm.protocol import LLMBackend

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_azure_backend_conforms_to_protocol() -> None:
    backend = AzureOpenAIBackend()
    assert isinstance(backend, LLMBackend)


@pytest.mark.unit
def test_get_default_backend_returns_azure() -> None:
    backend = get_default_backend()
    assert isinstance(backend, AzureOpenAIBackend)


# ---------------------------------------------------------------------------
# AzureOpenAIBackend — delegates to injected callables
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_azure_backend_chat_delegates_to_injected_fn() -> None:
    calls = []

    def fake_chat(messages, max_tokens=800):
        calls.append((messages, max_tokens))
        return "Hi there"

    backend = AzureOpenAIBackend(chat_fn=fake_chat)
    messages = [{"role": "user", "content": "Hello"}]
    result = backend.chat(messages, max_tokens=100)

    assert len(calls) == 1
    assert calls[0] == (messages, 100)
    assert result == "Hi there"


@pytest.mark.unit
def test_azure_backend_chat_default_max_tokens() -> None:
    calls = []

    def fake_chat(messages, max_tokens=800):
        calls.append(max_tokens)
        return "ok"

    backend = AzureOpenAIBackend(chat_fn=fake_chat)
    backend.chat([{"role": "user", "content": "test"}])

    assert calls[0] == 800


@pytest.mark.unit
def test_azure_backend_embed_delegates_to_injected_fn() -> None:
    expected = [0.1, 0.2, 0.3]
    backend = AzureOpenAIBackend(embed_fn=lambda text: expected)
    result = backend.embed("some text")
    assert result == expected


@pytest.mark.unit
def test_azure_backend_chat_returns_empty_string_on_failure() -> None:
    backend = AzureOpenAIBackend(chat_fn=lambda msgs, max_tokens=800: "")
    result = backend.chat([{"role": "user", "content": "test"}])
    assert result == ""


@pytest.mark.unit
def test_azure_backend_embed_returns_empty_list_on_failure() -> None:
    backend = AzureOpenAIBackend(embed_fn=lambda text: [])
    result = backend.embed("text")
    assert result == []


# ---------------------------------------------------------------------------
# Protocol usage pattern — callers receive LLMBackend, not concrete class
# ---------------------------------------------------------------------------


def _do_summarise(text: str, llm: LLMBackend) -> str:
    """Example of how production code should accept LLMBackend."""
    return llm.chat([{"role": "user", "content": f"Summarise: {text}"}])


@pytest.mark.unit
def test_caller_accepts_protocol_type() -> None:
    backend = AzureOpenAIBackend(chat_fn=lambda msgs, max_tokens=800: "Summary.")
    result = _do_summarise("long document", backend)
    assert result == "Summary."


class _MockLLMBackend:
    """Minimal test double — satisfies the protocol without _azure."""

    def chat(self, messages: list[dict], max_tokens: int = 800) -> str:
        return "mock response"

    def embed(self, text: str) -> list[float]:
        return [0.0] * 1536

    def embed_as_bytes(self, text: str) -> bytes | None:
        import struct

        vec = self.embed(text)
        return struct.pack(f"<{len(vec)}f", *vec)


@pytest.mark.unit
def test_mock_backend_satisfies_protocol() -> None:
    mock = _MockLLMBackend()
    assert isinstance(mock, LLMBackend)


@pytest.mark.unit
def test_caller_works_with_mock_backend() -> None:
    mock = _MockLLMBackend()
    result = _do_summarise("text", mock)
    assert result == "mock response"
