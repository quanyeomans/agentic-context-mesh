"""
Concrete LLMBackend implementations.

AzureOpenAIBackend — thin wrapper over kairix._azure.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class AzureOpenAIBackend:
    """
    LLMBackend backed by Azure OpenAI via kairix._azure.

    Delegates to the existing ``chat_completion`` and ``embed_text``
    functions which handle Key Vault secret resolution, retry logic,
    and failure-safe return values.

    This class adds no extra logic — it is purely an adapter so callers
    can program against the ``LLMBackend`` protocol without importing
    ``kairix._azure`` directly.

    # Adapter pattern: satisfies LLMBackend protocol by delegating to _azure module
    """

    def __init__(
        self,
        chat_fn: Callable[..., str] | None = None,
        embed_fn: Callable[..., list[float]] | None = None,
    ) -> None:
        """Construct with optional injectable callables for testing."""
        self._chat_fn = chat_fn
        self._embed_fn = embed_fn

    def chat(self, messages: list[dict[str, Any]], max_tokens: int = 800) -> str:
        """Chat completion via Azure OpenAI (gpt-4o-mini by default)."""
        if self._chat_fn is not None:
            return self._chat_fn(messages, max_tokens=max_tokens)
        from kairix._azure import chat_completion

        result: str = chat_completion(messages, max_tokens=max_tokens)
        return result

    def embed(self, text: str) -> list[float]:
        """Text embedding via Azure OpenAI (text-embedding-3-large)."""
        if self._embed_fn is not None:
            return self._embed_fn(text)
        from kairix._azure import embed_text

        result: list[float] = embed_text(text)
        return result
