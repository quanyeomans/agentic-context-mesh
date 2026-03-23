"""
Shared Azure OpenAI client for the Mnemosyne pipeline.

Provides:
  - embed_text(text: str) -> list[float]
        Embeds text via Azure OpenAI text-embedding-3-large.
        Returns [] on any failure — callers treat [] as "no embedding available".

Secrets are fetched at runtime from Key Vault `kv-tc-exp` using the Azure CLI.
They are cached in-process for the process lifetime (never written to disk or logs).

Key Vault secret names:
  azure-openai-api-key
  azure-openai-endpoint
  azure-openai-embedding-deployment  (default: text-embedding-3-large)

Override secrets via environment variables for testing:
  AZURE_OPENAI_API_KEY
  AZURE_OPENAI_ENDPOINT
  AZURE_OPENAI_EMBED_DEPLOYMENT

Failure modes:
  - Key Vault unavailable: returns []
  - Network error: returns []
  - Azure API error (rate limit, auth failure, etc.): returns []
  - Malformed response: returns []
  Never raises.
"""

import logging
import os
import struct
import subprocess
from functools import lru_cache

import requests

logger = logging.getLogger(__name__)

# Azure OpenAI embedding dimensions
EMBED_DIMS = 1536

# Key Vault name
_KEY_VAULT_NAME = "kv-tc-exp"

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
    """
    # Environment variable overrides (useful for tests and local dev)
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    deployment = os.environ.get("AZURE_OPENAI_EMBED_DEPLOYMENT", _DEFAULT_EMBED_DEPLOYMENT)

    if api_key and endpoint:
        return {
            "api_key": api_key,
            "endpoint": endpoint.rstrip("/"),
            "deployment": deployment,
        }

    # Fetch from Key Vault
    secrets: dict[str, str] = {}

    secret_map = {
        "api_key": "azure-openai-api-key",
        "endpoint": "azure-openai-endpoint",
        "deployment": "azure-openai-embedding-deployment",
    }

    for key, secret_name in secret_map.items():
        try:
            result = subprocess.run(
                [
                    "az",
                    "keyvault",
                    "secret",
                    "show",
                    "--vault-name",
                    _KEY_VAULT_NAME,
                    "--name",
                    secret_name,
                    "--query",
                    "value",
                    "-o",
                    "tsv",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                secrets[key] = result.stdout.strip()
            else:
                logger.warning("_azure: failed to fetch KV secret %r (exit=%d)", secret_name, result.returncode)
        except Exception as e:
            logger.warning("_azure: error fetching KV secret %r — %s", secret_name, e)

    if "deployment" not in secrets:
        secrets["deployment"] = _DEFAULT_EMBED_DEPLOYMENT

    if secrets.get("endpoint"):
        secrets["endpoint"] = secrets["endpoint"].rstrip("/")

    return secrets


def embed_text(text: str) -> list[float]:
    """
    Embed a text string via Azure OpenAI text-embedding-3-large.

    Returns a list of 1536 floats. Returns [] on any failure.
    Never raises.
    """
    if not text or not text.strip():
        return []

    try:
        secrets = _get_secrets()
    except Exception as e:
        logger.warning("embed_text: failed to get secrets — %s", e)
        return []

    api_key = secrets.get("api_key")
    endpoint = secrets.get("endpoint")
    deployment = secrets.get("deployment", _DEFAULT_EMBED_DEPLOYMENT)

    if not api_key or not endpoint:
        logger.warning("embed_text: missing API key or endpoint — returning []")
        return []

    url = f"{endpoint}/openai/deployments/{deployment}/embeddings?api-version=2024-02-01"

    try:
        resp = requests.post(
            url,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json={"input": [text], "dimensions": EMBED_DIMS},
            timeout=_EMBED_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        vec: list[float] = data["data"][0]["embedding"]
        return vec
    except requests.exceptions.Timeout:
        logger.warning("embed_text: Azure API timed out")
        return []
    except requests.exceptions.RequestException as e:
        logger.warning("embed_text: Azure API request failed — %s", e)
        return []
    except (KeyError, IndexError, TypeError, ValueError) as e:
        logger.warning("embed_text: unexpected Azure API response format — %s", e)
        return []
    except Exception as e:
        logger.warning("embed_text: unexpected error — %s", e)
        return []


def chat_completion(messages: list[dict], max_tokens: int = 800) -> str:
    """
    Call GPT-4o-mini for synthesis via Azure OpenAI chat completions.

    Uses the azure-openai-gpt4o-mini-deployment KV secret for the deployment name.
    Same endpoint and API key as embeddings.

    Args:
        messages:   List of chat message dicts (role/content).
        max_tokens: Maximum tokens in the response.

    Returns:
        Generated text string. Returns empty string on any failure. Never raises.
    """
    try:
        secrets = _get_secrets()
    except Exception as e:
        logger.warning("chat_completion: failed to get secrets — %s", e)
        return ""

    api_key = secrets.get("api_key")
    endpoint = secrets.get("endpoint")

    if not api_key or not endpoint:
        logger.warning("chat_completion: missing API key or endpoint — returning ''")
        return ""

    # Fetch GPT-4o-mini deployment name separately (different KV secret)
    deployment = os.environ.get("AZURE_OPENAI_GPT4O_MINI_DEPLOYMENT", "")
    if not deployment:
        try:
            result = subprocess.run(
                [
                    "az",
                    "keyvault",
                    "secret",
                    "show",
                    "--vault-name",
                    _KEY_VAULT_NAME,
                    "--name",
                    "azure-openai-gpt4o-mini-deployment",
                    "--query",
                    "value",
                    "-o",
                    "tsv",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                deployment = result.stdout.strip()
        except Exception as e:
            logger.warning("chat_completion: error fetching GPT-4o-mini deployment secret — %s", e)

    if not deployment:
        # Fall back to a common default
        deployment = "gpt-4o-mini"
        logger.warning("chat_completion: using fallback deployment name %r", deployment)

    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-02-01"

    try:
        resp = requests.post(
            url,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json={
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.3,
            },
            timeout=_EMBED_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        content: str = data["choices"][0]["message"]["content"]
        return content
    except requests.exceptions.Timeout:
        logger.warning("chat_completion: Azure API timed out")
        return ""
    except requests.exceptions.RequestException as e:
        logger.warning("chat_completion: Azure API request failed — %s", e)
        return ""
    except (KeyError, IndexError, TypeError, ValueError) as e:
        logger.warning("chat_completion: unexpected response format — %s", e)
        return ""
    except Exception as e:
        logger.warning("chat_completion: unexpected error — %s", e)
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
