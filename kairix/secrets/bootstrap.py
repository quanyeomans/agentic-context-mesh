"""Single boot-time secret hydration for every kairix entry point.

Why this exists
---------------

Pre-ADR-031, ``kairix.secrets._legacy.get_secret(name)`` hydrated the
bundle file (``$KAIRIX_SECRETS_FILE``, default
``/run/secrets/kairix.env``) on its first call. Lazy + implicit.
Connectors that called ``get_secret`` got hydration for free.

ADR-031 (v2026.5.31) added ``SecretsLoader`` as the single canonical
resolution surface. The Wave-2 refactors moved connector + provider
credential reads onto ``SecretsLoader.require(...)`` (commits
``5b9344dd``, ``30cfc1b9``, ``fa3c3295``, ``c808e1b4``). But the
loader's resolution path checks ``os.environ`` BEFORE delegating to
the legacy chain — and ``SecretsLoader.__init__`` snapshotted env
at construction. So if any caller constructed a loader BEFORE the
bundle was hydrated, every ``get()`` returned None for bundle-only
secrets.

The 2026-06-01 production crash hit this: ``kairix.credentials.
_resolve_embed`` constructed a loader and called ``require("provider",
"llm", None, "api-key")`` — the canonical env-var
``KAIRIX_PROVIDER_LLM_API_KEY`` wasn't in env (only the legacy
``KAIRIX_LLM_API_KEY`` was in the bundle), the bundle hadn't been
hydrated yet (no caller had triggered it), and the loader raised
``SecretNotFoundError``.

The structural fix is two-part:

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
