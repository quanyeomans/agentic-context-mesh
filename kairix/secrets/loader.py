"""SecretsLoader — single entry point for all kairix secret access.

Resolution order (first hit wins):

1. Process env via the canonical :func:`canonical_env_var` form.
2. Legacy env aliases registered in
   :data:`kairix.secrets._legacy_aliases.LEGACY_ALIASES` —
   a hit emits ``DeprecationWarning`` so operators see what to rotate.
3. KV-backed file mount at ``/run/kairix/secrets/<canonical-name>``
   (CSI driver path; falls back through
   :func:`kairix.secrets._legacy._read_secret_file` so existing
   per-file deployments keep working).
4. Legacy bundle / az-keyvault paths via the existing
   :func:`kairix.secrets._legacy.get_secret` chain so operators on
   the bundle-file or KV-CLI deployment keep resolving.
5. ``None`` (or :class:`SecretNotFoundError` from :meth:`require`).

The loader is a **constructor seam** — never a module-level singleton.
Production callers construct ``SecretsLoader()`` with no args and pass
the instance through their ``secrets=`` kwarg; tests pass
``FakeSecretsLoader(values={...})`` (see ``tests/fakes.py``).
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from kairix.secrets._legacy_aliases import LEGACY_ALIASES
from kairix.secrets.naming import (
    Scope,
    canonical_env_var,
    canonical_secret_name,
)

_KV_MOUNT_DIR = Path("/run/kairix/secrets")

# Type alias for the legacy-chain callable — takes a canonical KV
# name and returns the resolved value or None. Defined at module
# level so the SecretsLoader signature can reference it cleanly.
_LegacyResolver = Callable[[str], str | None]


class SecretNotFoundError(LookupError):
    """Raised by :meth:`SecretsLoader.require` when no source resolves
    the requested identity. The message names the canonical KV secret
    so operators can paste it straight into ``az keyvault secret set``.
    """


@runtime_checkable
class SecretsResolver(Protocol):
    """Protocol every concrete loader (real + fake) implements.

    Defined as a Protocol so connectors can depend on the shape, not
    the concrete class, and tests substitute
    :class:`tests.fakes.FakeSecretsLoader` without inheritance.
    """

    def get(
        self,
        scope: Scope,
        area: str,
        instance: str | None,
        leaf: str,
    ) -> str | None:
        """Return the secret value or ``None`` if no source resolves."""

    def require(
        self,
        scope: Scope,
        area: str,
        instance: str | None,
        leaf: str,
    ) -> str:
        """Return the secret value or raise :class:`SecretNotFoundError`."""


class SecretsLoader:
    """Production secret resolver — env -> legacy aliases -> KV mount -> legacy chain.

    Args:
      env: Override the env mapping (defaults to ``os.environ``).
        Tests pass an explicit dict to scope env-var visibility without
        touching the global ``os.environ`` (which would require
        ``monkeypatch.setenv`` and trip F2).
      kv_mount: Directory where the CSI driver writes one file per
        canonical KV secret name. Defaults to
        :data:`_KV_MOUNT_DIR` (``/run/kairix/secrets``).
      legacy_chain: Callable that takes a logical legacy secret name
        (``"connector-m365-tenant-id"``) and returns the resolved
        value or ``None``. Defaults to the historical
        :func:`kairix.secrets._legacy.get_secret` chain.
    """

    def __init__(
        self,
        *,
        env: dict[str, str] | None = None,
        kv_mount: Path | None = None,
        legacy_chain: _LegacyResolver | None = None,
    ) -> None:
        # Store env by REFERENCE (not snapshot) when explicitly passed.
        # When env is None (production), read os.environ live on every
        # get(). Both paths are "live read of the mapping" — symmetric
        # by design so the bootstrap-then-resolve flow works whether
        # the caller passes its own dict (tests) or uses os.environ
        # (production). The 2026-06-01 production bug was caused by a
        # dict(env) snapshot at init that masked any subsequent
        # bundle hydration; live reads close that class of bug
        # structurally rather than via per-call-site hydration hacks.
        self._env_override = env  # ref, not snapshot
        self._kv_mount = kv_mount if kv_mount is not None else _KV_MOUNT_DIR
        self._legacy_chain = legacy_chain if legacy_chain is not None else _default_legacy_chain

    @property
    def _env(self) -> dict[str, str] | os._Environ[str]:
        """The live env mapping — either the explicit override or os.environ.

        Both paths return a live reference, NOT a snapshot. Any
        post-construction mutation (bootstrap_secrets, a test mutating
        its own dict) is picked up on the next get() call. The
        symmetric live-read shape is what closes the 2026-06-01
        production class of bug structurally.
        """
        if self._env_override is not None:
            return self._env_override
        return os.environ

    def get(
        self,
        scope: Scope,
        area: str,
        instance: str | None,
        leaf: str,
    ) -> str | None:
        """Resolve a secret. Returns ``None`` if no source matches."""
        canonical_env = canonical_env_var(scope, area, instance, leaf)
        canonical_kv = canonical_secret_name(scope, area, instance, leaf)

        value = self._env.get(canonical_env)
        if value:
            return value

        value = self._try_legacy_aliases(scope, area, instance, leaf, canonical_kv)
        if value:
            return value

        value = self._try_kv_mount(canonical_kv)
        if value:
            return value

        return self._legacy_chain(canonical_kv)

    def require(
        self,
        scope: Scope,
        area: str,
        instance: str | None,
        leaf: str,
    ) -> str:
        """Return the secret or raise :class:`SecretNotFoundError`.

        The error message names the canonical KV secret + the canonical
        env-var so the operator has actionable next steps without
        digging through code.
        """
        value = self.get(scope, area, instance, leaf)
        if value is None:
            canonical_kv = canonical_secret_name(scope, area, instance, leaf)
            canonical_env = canonical_env_var(scope, area, instance, leaf)
            raise SecretNotFoundError(
                f"Required secret not available: {canonical_kv}. "
                f"fix: set the {canonical_env} env var, write the value to "
                f"{self._kv_mount}/{canonical_kv}, or store it in your KV "
                f"under the name {canonical_kv}. "
                f"next: re-run the command; the loader walks env -> legacy "
                f"aliases -> KV mount -> KV CLI in that order. "
                f"run: kairix secrets verify",
            )
        return value

    def _try_legacy_aliases(
        self,
        scope: Scope,
        area: str,
        instance: str | None,
        leaf: str,
        canonical_kv: str,
    ) -> str | None:
        """Walk the LEGACY_ALIASES list for this identity. First hit
        emits DeprecationWarning naming the alias + canonical
        replacement.
        """
        aliases = LEGACY_ALIASES.get((scope, area, instance, leaf), [])
        for alias in aliases:
            alias_value = self._env.get(alias)
            if alias_value:
                warnings.warn(
                    f"Resolved secret {canonical_kv} via legacy env var "
                    f"{alias}; please migrate to {canonical_env_var(scope, area, instance, leaf)}.",
                    DeprecationWarning,
                    stacklevel=3,
                )
                return alias_value
        return None

    def _try_kv_mount(self, canonical_kv: str) -> str | None:
        """Look for a CSI-style per-file mount at ``<kv_mount>/<name>``."""
        candidate = self._kv_mount / canonical_kv
        try:
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8").strip()
                return value or None
        except OSError:
            # Unreadable mount falls through to the next resolver — same
            # behaviour as the historical per-file resolver.
            return None
        return None


def _default_legacy_chain(canonical_kv: str) -> str | None:
    """Default legacy chain: try the historical ``get_secret`` resolver.

    The historical chain (per-file secrets dir, bundle file, az
    keyvault CLI) is preserved by re-dispatching the canonical KV
    name through :func:`kairix.secrets._legacy.get_secret` with
    ``required=False``. The historic ``_SECRET_ENV_MAP`` only knows
    the pre-canonical names (``kairix-llm-api-key`` etc.) so the lookup
    will miss for new canonical names; the legacy file mount + KV CLI
    paths still resolve because they key on the canonical name itself.
    """
    from kairix.secrets._legacy import get_secret

    try:
        return get_secret(canonical_kv, required=False)
    except OSError:
        # get_secret(required=False) shouldn't raise, but if the future
        # chain grows a strict mode the loader degrades to None.
        return None


__all__ = [
    "SecretNotFoundError",
    "SecretsLoader",
    "SecretsResolver",
]
