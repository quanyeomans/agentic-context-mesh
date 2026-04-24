"""Embedding providers — SDK-based clients with built-in retry and rate limiting.

Turns your text into numbers that the search engine uses to find similar
content (vector embeddings). Uses the openai SDK for automatic retry,
rate-limit handling, and backoff — no manual retry logic needed.

Two providers available:
  AzureEmbedProvider — for Azure OpenAI endpoints
  OpenAIEmbedProvider — for standard OpenAI endpoints
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class EmbedProvider(Protocol):
    """Interface for embedding text into vectors.

    Implementations handle retry, rate limiting, and backoff internally
    via the openai SDK.
    """

    def embed_batch(self, texts: list[str], *, model: str, dims: int) -> list[list[float]]:
        """Embed a batch of texts into vectors.

        Args:
            texts: List of text strings to embed.
            model: Model deployment name (e.g. "text-embedding-3-large").
            dims:  Embedding dimensions (e.g. 1536).

        Returns:
            List of embedding vectors (same length as texts).
        """
        ...


class AzureEmbedProvider:
    """Azure OpenAI embeddings via the openai SDK.

    Uses the SDK's built-in retry with exponential backoff and
    rate-limit header respect. No manual retry needed.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        api_version: str = "2024-10-21",
        max_retries: int = 5,
    ) -> None:
        from openai import AzureOpenAI

        self.client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            max_retries=max_retries,
        )

    def embed_batch(self, texts: list[str], *, model: str, dims: int) -> list[list[float]]:
        response = self.client.embeddings.create(input=texts, model=model, dimensions=dims)
        return [item.embedding for item in response.data]


class OpenAIEmbedProvider:
    """Standard OpenAI embeddings via the openai SDK."""

    def __init__(self, api_key: str, max_retries: int = 5) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, max_retries=max_retries)

    def embed_batch(self, texts: list[str], *, model: str, dims: int) -> list[list[float]]:
        response = self.client.embeddings.create(input=texts, model=model, dimensions=dims)
        return [item.embedding for item in response.data]


def get_embed_provider() -> EmbedProvider:
    """Get the configured embed provider based on environment variables.

    Checks for Azure config first (AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY),
    then falls back to standard OpenAI (OPENAI_API_KEY).

    Uses kairix.secrets.get_secret() for credential resolution, which checks:
    env vars → secrets file → Azure Key Vault.

    Raises OSError if no credentials are available.
    """
    from kairix.secrets import get_secret

    # Try Azure first
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT") or get_secret("azure-openai-endpoint", required=False)
    api_key = os.environ.get("AZURE_OPENAI_API_KEY") or get_secret("azure-openai-api-key", required=False)

    if endpoint and api_key:
        logger.debug("embed_provider: using AzureEmbedProvider (endpoint=%s...)", endpoint[:30])
        return AzureEmbedProvider(endpoint=endpoint, api_key=api_key)

    # Fall back to OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY") or get_secret("openai-api-key", required=False)
    if openai_key:
        logger.debug("embed_provider: using OpenAIEmbedProvider")
        return OpenAIEmbedProvider(api_key=openai_key)

    raise OSError(
        "No embedding provider configured. Set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY "
        "for Azure, or OPENAI_API_KEY for OpenAI."
    )
