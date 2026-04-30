"""Embedding providers — SDK-based clients with built-in retry and rate limiting.

Turns your text into numbers that the search engine uses to find similar
content (vector embeddings). Uses the openai SDK for automatic retry,
rate-limit handling, and backoff — no manual retry logic needed.

Two providers available:
  AzureEmbedProvider — for Azure OpenAI endpoints
  OpenAIEmbedProvider — for standard OpenAI endpoints (including OpenRouter)
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
    """Azure OpenAI embeddings via the openai SDK."""

    def __init__(self, endpoint: str, api_key: str, max_retries: int = 5) -> None:
        from kairix.credentials import make_openai_client

        self.client = make_openai_client(api_key, endpoint, max_retries=max_retries)

    def embed_batch(self, texts: list[str], *, model: str, dims: int) -> list[list[float]]:
        response = self.client.embeddings.create(input=texts, model=model, dimensions=dims)
        return [item.embedding for item in response.data]


class OpenAIEmbedProvider:
    """Standard OpenAI / OpenRouter embeddings via the openai SDK."""

    def __init__(self, api_key: str, endpoint: str = "https://api.openai.com/v1", max_retries: int = 5) -> None:
        from kairix.credentials import make_openai_client

        self.client = make_openai_client(api_key, endpoint, max_retries=max_retries)

    def embed_batch(self, texts: list[str], *, model: str, dims: int) -> list[list[float]]:
        response = self.client.embeddings.create(input=texts, model=model, dimensions=dims)
        return [item.embedding for item in response.data]


def get_embed_provider() -> EmbedProvider:
    """Get the configured embed provider via ``get_credentials("embed")``.

    Uses ``kairix.credentials.get_credentials()`` for credential resolution,
    which checks: env vars (KAIRIX_EMBED_* / KAIRIX_LLM_*) -> secrets file
    -> Azure Key Vault.

    Selects AzureEmbedProvider when the endpoint is an Azure URL, otherwise
    falls back to OpenAIEmbedProvider.

    Raises OSError if no credentials are available.
    """
    from kairix.credentials import get_credentials

    creds = get_credentials("embed")

    if creds and creds.api_key and creds.endpoint:
        if creds.is_azure:
            logger.debug("embed_provider: using AzureEmbedProvider")
            return AzureEmbedProvider(endpoint=creds.endpoint, api_key=creds.api_key)
        else:
            logger.debug("embed_provider: using OpenAIEmbedProvider")
            return OpenAIEmbedProvider(api_key=creds.api_key, endpoint=creds.endpoint)

    # Fall back to OPENAI_API_KEY for backwards compatibility
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        logger.debug("embed_provider: using OpenAIEmbedProvider (OPENAI_API_KEY fallback)")
        return OpenAIEmbedProvider(api_key=openai_key)

    raise OSError(
        "No embedding provider configured. Set KAIRIX_LLM_API_KEY + KAIRIX_LLM_ENDPOINT "
        "(or KAIRIX_EMBED_API_KEY + KAIRIX_EMBED_ENDPOINT for a separate embed provider), "
        "or OPENAI_API_KEY for OpenAI."
    )
