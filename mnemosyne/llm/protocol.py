"""
LLMBackend protocol — provider-agnostic interface for chat and embedding.

All Mnemosyne code that calls an LLM should accept a ``LLMBackend`` rather
than importing ``mnemosyne._azure`` directly.  This decouples the engine
from the Azure-specific implementation and enables:

  - Swapping providers (Azure → Anthropic, local models, etc.)
  - Clean repo boundary: Azure credentials stay in the private repo
  - Easy test doubles (MockLLMBackend)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMBackend(Protocol):
    """
    Minimal interface for an LLM provider.

    Implementations must be safe to call from multiple threads (or at
    minimum from a single long-running process) and should never raise —
    they return empty strings / empty lists on failure.
    """

    def chat(
        self,
        messages: list[dict],
        max_tokens: int = 800,
    ) -> str:
        """
        Send a chat completion request.

        Args:
            messages:   OpenAI-compatible message list
                        (e.g. [{"role": "user", "content": "..."}])
            max_tokens: Maximum tokens in the response.

        Returns:
            The assistant reply string, or "" on failure.  Never raises.
        """
        ...

    def embed(self, text: str) -> list[float]:
        """
        Embed a text string.

        Args:
            text: The text to embed.

        Returns:
            Float vector, or [] on failure.  Never raises.
        """
        ...
