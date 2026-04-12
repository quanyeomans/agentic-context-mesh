"""
kairix.llm — LLM backend abstraction layer.

Provides a ``LLMBackend`` protocol so Mnemosyne components can call
chat/embed APIs without a hard dependency on any specific provider.

Built-in backends:
  AzureOpenAIBackend — wraps kairix._azure (Azure OpenAI, Key Vault secrets)
  AnthropicBackend  — stub (raises NotImplementedError; add SDK when needed)

Usage::

    from kairix.llm import get_default_backend

    backend = get_default_backend()
    reply = backend.chat(messages=[{"role": "user", "content": "Hello"}])

Or inject a specific backend for testing::

    from kairix.llm.backends import AzureOpenAIBackend
    backend = AzureOpenAIBackend()
"""

from kairix.llm.backends import AnthropicBackend, AzureOpenAIBackend
from kairix.llm.protocol import LLMBackend

__all__ = ["AnthropicBackend", "AzureOpenAIBackend", "LLMBackend", "get_default_backend"]


def get_default_backend() -> LLMBackend:
    """Return the default backend (Azure OpenAI via _azure.py)."""
    return AzureOpenAIBackend()
