"""
kairix.secrets — load secrets from the vault-agent sidecar secrets file.

In Docker deployments the vault-agent sidecar fetches secrets from Azure Key
Vault and writes them to /run/secrets/kairix.env (a tmpfs path). This module
reads that file and loads the KEY=VALUE pairs into os.environ so that the rest
of the application can find credentials via the standard env-var paths.

Usage (called at module level before env-var reads):
    from kairix.secrets import load_secrets
    load_secrets()

Semantics:
  - If the secrets file does not exist, this is a no-op (local dev / CI).
  - Environment variables that are already set are never overwritten. This
    preserves the existing priority: direct env overrides > sidecar secrets.
  - Comments (#) and blank lines in the file are ignored.
  - Multiline values are not supported — each secret must fit on one line.
  - Never raises.

The secrets file path can be overridden via KAIRIX_SECRETS_FILE for testing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_SECRETS_FILE = "/run/secrets/kairix.env"


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
        logger.warning("secrets: failed to load %s — %s", secrets_path, exc)
        return 0

    if count:
        logger.debug("secrets: loaded %d variable(s) from %s", count, secrets_path)
    return count
