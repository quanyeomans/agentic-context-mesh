"""kairix.secrets.probe — lightweight credential-availability probes.

Health surfaces need "is the LLM credential resolvable?" as a boolean,
without raising and without copying the secret value into any message.
This module owns that question so callers (``kairix.core.health``) stay
out of the resolution details.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def _legacy_llm_api_key() -> str | None:
    """Default legacy fallback: the retired ``kairix-llm-api-key`` chain.

    Deployed secrets bundles still emit the legacy names (GH #479), so the
    fallback stays until the GH #369 retirement lands.
    """
    from kairix.secrets import get_secret

    return get_secret("kairix-llm-api-key", required=False)


def llm_credentials_available(
    *,
    env: dict[str, str] | None = None,
    kv_mount: Path | None = None,
    legacy_lookup: Callable[[], str | None] = _legacy_llm_api_key,
) -> bool:
    """True when an LLM API key resolves — canonical name first, then legacy.

    Canonical resolution walks the :class:`SecretsLoader` chain
    (``KAIRIX_PROVIDER_LLM_API_KEY`` env var, then the KV mount).
    ``env`` / ``kv_mount`` / ``legacy_lookup`` carry production defaults
    and follow the same strategy-injection shape ``kairix.core.health``
    already uses for its probes.
    """
    from kairix.secrets.loader import SecretsLoader

    loader = SecretsLoader(env=env, kv_mount=kv_mount)
    if loader.get("provider", "llm", None, "api-key"):
        return True
    return bool(legacy_lookup())
