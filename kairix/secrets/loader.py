"""SecretsLoader — single entry point for all kairix secret access.

Resolution order (first hit wins):

1. Process env via the canonical :func:`canonical_env_var` form.
2. KV-backed file mount at ``/run/kairix/secrets/<canonical-name>``
   (CSI driver path).
3. ``None`` (or :class:`SecretNotFoundError` from :meth:`require`).

The loader is a **constructor seam** — never a module-level singleton.
Production callers construct ``SecretsLoader()`` with no args and pass
the instance through their ``secrets=`` kwarg; tests pass
``FakeSecretsLoader(values={...})`` (see ``tests/fakes.py``).

The legacy env-var alias chain (``LEGACY_ALIASES`` + the historical
``kairix.secrets._legacy.get_secret`` fallback) was retired in #369
after the canonical-naming migration completed. Operators with old
``KAIRIX_*`` env vars must rotate to the canonical
``KAIRIX_<SCOPE>_<AREA>[_<INSTANCE>]_<LEAF>`` shape; the loader no
longer translates between the two.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from kairix.secrets.naming import (
    Scope,
    canonical_env_var,
    canonical_secret_name,
)

_KV_MOUNT_DIR = Path("/run/kairix/secrets")


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
    """Production secret resolver — canonical env var, then KV mount.

    Args:
      env: Override the env mapping (defaults to ``os.environ``).
        Tests pass an explicit dict to scope env-var visibility without
        touching the global ``os.environ`` (which would require
        ``monkeypatch.setenv`` and trip F2).
      kv_mount: Directory where the CSI driver writes one file per
        canonical KV secret name. Defaults to
        :data:`_KV_MOUNT_DIR` (``/run/kairix/secrets``).
    """

    def __init__(
        self,
        *,
        env: dict[str, str] | None = None,
        kv_mount: Path | None = None,
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

        return self._try_kv_mount(canonical_kv)

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
                f"fix: set the {canonical_env} env var, or write the value to "
                f"{self._kv_mount}/{canonical_kv}, or store it in your KV "
                f"under the name {canonical_kv}. "
                f"next: re-run the command; the loader walks env -> KV mount "
                f"in that order. "
                f"run: kairix secrets verify",
            )
        return value

    def _try_kv_mount(self, canonical_kv: str) -> str | None:
        """Look for a CSI-style per-file mount at ``<kv_mount>/<canonical_kv>``."""
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


__all__ = [
    "SecretNotFoundError",
    "SecretsLoader",
    "SecretsResolver",
]
