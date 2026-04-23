"""
kairix.secrets — load secrets from the vault-agent sidecar secrets file,
and resolve individual secrets by name.

In Docker deployments the vault-agent sidecar fetches secrets from Azure Key
Vault and writes them to /run/secrets/kairix.env (a tmpfs path). This module
reads that file and loads the KEY=VALUE pairs into os.environ so that the rest
of the application can find credentials via the standard env-var paths.

Usage (called at module level before env-var reads):
    from kairix.secrets import load_secrets
    load_secrets()

Semantics for load_secrets():
  - If the secrets file does not exist, this is a no-op (local dev / CI).
  - Environment variables that are already set are never overwritten. This
    preserves the existing priority: direct env overrides > sidecar secrets.
  - Comments (#) and blank lines in the file are ignored.
  - Multiline values are not supported — each secret must fit on one line.
  - Never raises.

The secrets file path can be overridden via KAIRIX_SECRETS_FILE for testing.

Resolution order for get_secret():
  1. Direct env vars (AZURE_OPENAI_API_KEY etc.) — fastest, for tests and local dev
  2. KAIRIX_SECRETS_DIR/kairix.env — Docker sidecar pattern
  3. KAIRIX_KV_NAME env var → az keyvault secret show — VM fallback

Never returns None for a required secret — raises OSError with clear message.
"""

from __future__ import annotations

import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_SECRETS_FILE = "/run/secrets/kairix.env"

# Map of logical KV secret name → env var name
_SECRET_ENV_MAP = {
    "azure-openai-api-key": "AZURE_OPENAI_API_KEY",
    "azure-openai-endpoint": "AZURE_OPENAI_ENDPOINT",
    "azure-openai-embedding-deployment": "AZURE_OPENAI_EMBED_DEPLOYMENT",
    "azure-openai-gpt4o-mini-deployment": "AZURE_OPENAI_GPT4O_MINI_DEPLOYMENT",
    "kairix-neo4j-password": "KAIRIX_NEO4J_PASSWORD",
}


def load_secrets(path: str | Path | None = None) -> int:
    """
    Load KEY=VALUE pairs from the secrets file into os.environ.

    Args:
        path: Path to the secrets file. Defaults to KAIRIX_SECRETS_FILE env
              var, or /run/secrets/kairix.env if not set.

    Returns:
        Number of environment variables loaded (0 if file absent or empty).
        Never raises.
    """
    if path is None:
        path = os.environ.get("KAIRIX_SECRETS_FILE", _DEFAULT_SECRETS_FILE)
    secrets_path = Path(path)

    if not secrets_path.exists():
        return 0

    count = 0
    try:
        for lineno, line in enumerate(secrets_path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                logger.debug("secrets: skipping malformed line %d (no '=')", lineno)
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            if not key:
                continue
            if key in os.environ:
                # Existing env var takes priority — sidecar secrets are fallback
                continue
            os.environ[key] = value
            count += 1
    except Exception as exc:
        logger.warning("secrets: failed to load secrets file")
        return 0

    if count:
        logger.debug("secrets: loaded %d variable(s)", count)
    return count


@lru_cache(maxsize=1)
def _load_secrets_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from a secrets file. Cached per path."""
    result: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            if key:
                result[key] = value
    except Exception as exc:
        logger.warning("secrets: failed to parse secrets file")
    return result


def get_secret(name: str, required: bool = True) -> str | None:
    """
    Resolve a secret by KV name. Returns value or None (raises if required).

    Resolution order:
      1. Direct env vars (fastest, for tests and local dev)
      2. KAIRIX_SECRETS_DIR/kairix.env — Docker sidecar pattern
      3. KAIRIX_KV_NAME env var → az keyvault secret show — VM fallback

    Args:
        name:     KV secret name (e.g. "azure-openai-api-key").
        required: If True and no value found, raises OSError. Default True.

    Returns:
        Secret value string, or None if not found and required=False.

    Raises:
        OSError: When required=True and the secret cannot be resolved.
    """
    env_var = _SECRET_ENV_MAP.get(name)

    # Step 1: direct environment variable
    if env_var:
        value = os.environ.get(env_var)
        if value:
            return value

    # Step 2: sidecar secrets file
    secrets_dir = os.environ.get("KAIRIX_SECRETS_DIR", "/run/secrets")
    secrets_file = Path(secrets_dir) / "kairix.env"
    if secrets_file.exists():
        file_secrets = _load_secrets_file(secrets_file)
        if env_var and env_var in file_secrets:
            value = file_secrets[env_var]
            if value:
                return value

    # Step 3: Azure Key Vault CLI fallback
    kv_name = os.environ.get("KAIRIX_KV_NAME", "")
    if kv_name:
        try:
            result = subprocess.run(
                [
                    "az",
                    "keyvault",
                    "secret",
                    "show",
                    "--vault-name",
                    kv_name,
                    "--name",
                    name,
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
                return result.stdout.strip()
            else:
                logger.warning("get_secret: KV fetch failed for %r (exit=%d)", name, result.returncode)
        except Exception as exc:
            logger.warning("get_secret: error fetching KV secret %r — %s", name, exc)

    # Not found
    if required:
        logger.error(
            "get_secret: %r not found in env (%s), secrets file (%s), or Key Vault",
            name, env_var, secrets_file,
        )
        raise OSError(f"Secret {name!r} not available. Check environment, secrets file, or Key Vault configuration.")
    return None
