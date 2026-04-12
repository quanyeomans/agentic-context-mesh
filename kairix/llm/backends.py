"""
Concrete LLMBackend implementations.

AzureOpenAIBackend — thin wrapper over kairix._azure.
AnthropicBackend   — stub; raises NotImplementedError until Anthropic SDK is wired in.
"""

from __future__ import annotations


class AzureOpenAIBackend:
    """
    LLMBackend backed by Azure OpenAI via kairix._azure.

    Delegates to the existing ``chat_completion`` and ``embed_text``
    functions which handle Key Vault secret resolution, retry logic,
    and failure-safe return values.

    This class adds no extra logic — it is purely an adapter so callers
    can program against the ``LLMBackend`` protocol without importing
    ``kairix._azure`` directly.
    """

    def chat(self, messages: list[dict], max_tokens: int = 800) -> str:
        """Chat completion via Azure OpenAI (gpt-4o-mini by default)."""
        from kairix._azure import chat_completion

        return chat_completion(messages, max_tokens=max_tokens)

    def embed(self, text: str) -> list[float]:
        """Text embedding via Azure OpenAI (text-embedding-3-large)."""
        from kairix._azure import embed_text

        return embed_text(text)

    def embed_as_bytes(self, text: str) -> bytes | None:
        """Embed text and return as packed float32 bytes for sqlite-vec."""
        from kairix._azure import embed_text_as_bytes

        return embed_text_as_bytes(text)


class AnthropicBackend:
    """
    Stub LLMBackend for Anthropic Claude.

    Not yet implemented — raises ``NotImplementedError`` on all calls.
    Add ``anthropic`` to dependencies and implement when needed.
    """

    def chat(self, messages: list[dict], max_tokens: int = 800) -> str:
        raise NotImplementedError(
            "AnthropicBackend is not yet implemented. Install the anthropic SDK and implement this backend."
        )

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError(
            "AnthropicBackend does not support embedding. Use AzureOpenAIBackend for embedding operations."
        )

    def embed_as_bytes(self, text: str) -> bytes | None:
        raise NotImplementedError(
            "AnthropicBackend does not support embedding. Use AzureOpenAIBackend for embedding operations."
        )
