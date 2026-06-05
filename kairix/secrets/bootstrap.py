"""Single boot-time secret hydration for every kairix entry point.

Why this exists
---------------

ADR-031 (v2026.5.31) made ``SecretsLoader`` the single canonical
resolution surface. The Wave-2 refactors moved connector + provider
credential reads onto ``SecretsLoader.require(...)``. The loader's
resolution path checks ``os.environ`` first via the canonical
``KAIRIX_<SCOPE>_<AREA>[_<INSTANCE>]_<LEAF>`` form. Operators write
their canonical secrets either into env directly or into a bundle
file at ``$KAIRIX_SECRETS_FILE`` (default ``/run/secrets/kairix.env``)
that this module hydrates into ``os.environ`` at process boot.

The 2026-06-01 production crash that prompted this module's creation:
``kairix.credentials._resolve_embed`` constructed a loader and called
``require("provider", "llm", None, "api-key")`` before any caller
triggered bundle hydration — the canonical env-var wasn't in env yet
and the loader raised ``SecretNotFoundError``. The structural fix has
two parts:

1. ``SecretsLoader`` reads ``os.environ`` LIVE on each ``get()``
   call (no snapshot). Any hydration that happens between
   construction and ``get()`` is picked up.
2. **This module**: one canonical ``bootstrap_secrets()`` function
   called by every kairix process entry point (CLI dispatcher, worker
   ``main``, MCP server ``main``). Bundle is hydrated ONCE at boot,
   into ``os.environ``, where every subsequent loader (live-read)
   sees it.

After both parts land, secret resolution has ONE bundle-hydration
site (this function) and ONE resolution surface (``SecretsLoader``).
Caller code never has to think about "is the bundle loaded yet".

Tests
-----

Tests that construct ``SecretsLoader`` with an explicit ``env=...``
dict bypass bootstrap entirely — the test owns the env mapping
verbatim. Tests that simulate production process boot call
``bootstrap_secrets(bundle_path=tmp_path / "kairix.env")`` directly
with an injected path.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# One-shot guard. bootstrap_secrets() is idempotent at the underlying
# load_secrets() level (skips keys already in env), but this lock
# prevents redundant disk reads when multiple entry points construct
# their loaders in parallel during boot.
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAPPED: bool = False


def bootstrap_secrets(*, bundle_path: Path | None = None, force: bool = False) -> int:
    """Hydrate the kairix secrets bundle into ``os.environ`` once per process.

    Called from every kairix process entry point — the CLI dispatcher,
    ``kairix worker run``, ``kairix mcp serve``. After this returns,
    every ``SecretsLoader`` instance (live-read os.environ) sees the
    bundle's values via the canonical env-var resolution step.

    Args:
        bundle_path: Override the bundle path. Production callers pass
            ``None`` and the underlying ``load_secrets()`` resolves
            from ``$KAIRIX_SECRETS_FILE`` or the default
            ``/run/secrets/kairix.env``. Tests pass an explicit path.
        force: Re-hydrate even if a previous call already ran. Default
            False — the second call is a no-op. Force=True is for
            tests that need a clean per-test bootstrap.

    Returns:
        Number of env vars loaded from the bundle (0 if no bundle or
        already-bootstrapped). Never raises — bundle absent +
        unreadable cases log a WARNING and return 0.
    """
    global _BOOTSTRAPPED
    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED and not force:
            return 0
        from kairix.secrets._legacy import load_secrets

        count = load_secrets(bundle_path)
        _BOOTSTRAPPED = True
        if count:
            logger.info(
                "secrets: bootstrap hydrated %d secret(s) from bundle %s",
                count,
                bundle_path or "$KAIRIX_SECRETS_FILE (default)",
            )
        return count


def reset_for_tests() -> None:
    """Clear the bootstrap guard so the next test can re-bootstrap.

    Pytest tests that exercise the bootstrap path call this in a
    fixture teardown to keep tests independent.
    """
    global _BOOTSTRAPPED
    with _BOOTSTRAP_LOCK:
        _BOOTSTRAPPED = False


__all__ = ["bootstrap_secrets", "reset_for_tests"]
