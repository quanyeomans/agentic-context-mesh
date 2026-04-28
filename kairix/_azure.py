"""
Shared Azure OpenAI client for the kairix pipeline.

Provides:
  - embed_text(text: str) -> list[float]
        Embeds text via Azure OpenAI text-embedding-3-large.
        Returns [] on any failure — callers treat [] as "no embedding available".

Secrets are fetched at runtime from Azure Key Vault using the Azure CLI.
The Key Vault name is read from the KAIRIX_KV_NAME environment variable.
They are cached in-process for the process lifetime (never written to disk or logs).

Key Vault secret names:
  azure-openai-api-key
  azure-openai-endpoint
  azure-openai-embedding-deployment  (default: text-embedding-3-large)

Override secrets via environment variables for testing (or when not using Key Vault):
  AZURE_OPENAI_API_KEY
  AZURE_OPENAI_ENDPOINT
  AZURE_OPENAI_EMBED_DEPLOYMENT
  KAIRIX_KV_NAME  — Key Vault name (required when using Key Vault auth)

Failure modes:
  - Key Vault unavailable: returns []
  - Network error: returns []
  - Azure API error (rate limit, auth failure, etc.): returns []
  - Malformed response: returns []
  Never raises.
"""

import logging
import struct
from functools import lru_cache
from typing import Any

from kairix.secrets import load_secrets as _load_secrets

# Load vault-agent sidecar secrets before any env-var reads.
# No-op when /run/secrets/kairix.env is absent (local dev, CI).
_load_secrets()

logger = logging.getLogger(__name__)

# Azure OpenAI embedding dimensions
EMBED_DIMS = 1536

# Default deployment
_DEFAULT_EMBED_DEPLOYMENT = "text-embedding-3-large"

# Embedding API timeout (seconds)
_EMBED_TIMEOUT_S = 30


@lru_cache(maxsize=1)
def _get_secrets() -> dict[str, str]:
    """
    Fetch Azure OpenAI secrets from Key Vault or environment.

    Cached for the process lifetime (lru_cache with maxsize=1).
    Returns {} on any failure — callers check for missing keys.
    Never raises.

    Delegates to kairix.secrets.get_secret() for single-source secret
    resolution (env var -> sidecar file -> Key Vault CLI).
    """
    from kairix.secrets import get_secret

    secrets: dict[str, str] = {}

    secret_map = {
        "api_key": "azure-openai-api-key",
        "endpoint": "azure-openai-endpoint",
        "deployment": "azure-openai-embedding-deployment",
    }

    for key, secret_name in secret_map.items():
        try:
            value = get_secret(secret_name, required=False)
            if value:
                secrets[key] = value
        except Exception:  # broad catch justified: Key Vault SDK can raise varied exceptions (network, auth, parse)
            logger.warning("_azure: secret resolution error")

    if "deployment" not in secrets:
        secrets["deployment"] = _DEFAULT_EMBED_DEPLOYMENT

    if secrets.get("endpoint"):
        secrets["endpoint"] = secrets["endpoint"].rstrip("/")

    return secrets


def _get_client() -> Any:
    """Return an AzureOpenAI client configured from secrets. Cached per-process."""
    from openai import AzureOpenAI

    secrets = _get_secrets()
    api_key = secrets.get("api_key")
    endpoint = secrets.get("endpoint")
    if not api_key or not endpoint:
        raise ValueError("Missing Azure OpenAI API key or endpoint")
    return AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version="2024-02-01",
        max_retries=5,
        timeout=float(_EMBED_TIMEOUT_S),
    )


def embed_text(text: str) -> list[float]:
    """
    Embed a text string via Azure OpenAI text-embedding-3-large.

    Returns a list of 1536 floats. Returns [] on any failure.
    Never raises. Uses the OpenAI SDK with built-in retry and backoff.
    """
    if not text or not text.strip():
        return []

    try:
        client = _get_client()
        secrets = _get_secrets()
        deployment = secrets.get("deployment", _DEFAULT_EMBED_DEPLOYMENT)
        response = client.embeddings.create(
            model=deployment,
            input=[text],
            dimensions=EMBED_DIMS,
        )
        return list(response.data[0].embedding)
    except Exception as e:
        logger.warning("embed_text: %s", e)
        return []


def chat_completion(messages: list[dict[str, str]], max_tokens: int = 800) -> str:
    """
    Call GPT-4o-mini for synthesis via Azure OpenAI chat completions.

    Uses the azure-openai-gpt4o-mini-deployment KV secret for the deployment name.
    Same endpoint and API key as embeddings.

    Returns empty string on any failure. Never raises.
    Uses the OpenAI SDK with built-in retry and backoff.
    """
    try:
        client = _get_client()
    except Exception as e:
        logger.warning("chat_completion: failed to get client — %s", e)
        return ""

    # Fetch GPT-4o-mini deployment name separately (different KV secret)
    try:
        from kairix.secrets import get_secret

        deployment = get_secret("azure-openai-gpt4o-mini-deployment", required=False) or ""
    except Exception as e:
        logger.warning("chat_completion: error resolving GPT-4o-mini deployment secret — %s", e)
        deployment = ""

    if not deployment:
        deployment = "gpt-4o-mini"
        logger.warning("chat_completion: using fallback deployment name %r", deployment)

    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,
        )
        content: str = response.choices[0].message.content or ""
        return content
    except Exception as e:
        logger.warning("chat_completion: %s", e)
        return ""


def embed_text_as_bytes(text: str) -> bytes | None:
    """
    Embed text and return as packed float32 bytes (for sqlite-vec).

    Returns None on any failure (when embed_text returns []).
    """
    vec = embed_text(text)
    if not vec:
        return None
    return struct.pack(f"<{len(vec)}f", *vec)
