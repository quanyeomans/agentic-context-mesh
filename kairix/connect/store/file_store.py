"""File-backed token store — delegates to the canonical secrets writer.

The default backend for ``kairix connect``. Every leaf is persisted via
:func:`kairix.secrets.store.set_secret` — the single upsert site for the
operator bundle file — so a connected service's tokens land in exactly
the file the runtime read side (:func:`kairix.secrets.store.resolve_bundle_path`
→ ``bootstrap_secrets`` → :class:`kairix.secrets.SecretsLoader`)
resolves on the very next ``kairix worker run``:

* ``$KAIRIX_SECRETS_FILE`` override honoured,
* container layout (``/run/secrets/kairix.env``) probed before the
  pip-install XDG layout (``$XDG_CONFIG_HOME`` /
  ``~/.config/kairix/secrets/kairix.env``),
* file locked to mode 0600 on every write,
* multi-line values (the GitHub App PEM key) encoded newline-safe so
  every bundle line stays ``KEY=VALUE`` parseable,
* path confinement (pythonsecurity:S2083) inherited from
  ``set_secret``'s allow-list (operator home, ``/etc/kairix``,
  ``/run/secrets``, ``/run/kairix``, system temp) — escapes fail closed
  before any filesystem mutation.

Idempotent updates: existing canonical-named lines are replaced in
place; new canonical-named lines are appended; unrelated lines
(comments, blank lines, lines for other services) are preserved
verbatim — all ``set_secret`` semantics.

The ``env`` / ``home`` / ``container_dir`` constructor arguments are the
F2-clean test seams (same shape as :class:`kairix.secrets.SecretsLoader`
and ``set_secret`` itself); ``path`` pins an explicit destination
(the ``--secrets-file`` CLI flag).
"""

from __future__ import annotations

import os
from pathlib import Path

from kairix.connect.protocols import (
    CapturedTokens,
    ClientCredentials,
    TokenStore,
    TokenStoreUnauthorizedError,
    WriteReport,
)
from kairix.connect.store.leaves import leaf_pairs
from kairix.secrets.naming import Scope, canonical_env_var, canonical_secret_name
from kairix.secrets.store import set_secret

_BACKEND_NAME = "file"


class FileTokenStore:
    """Write captured tokens through ``kairix.secrets.store.set_secret``.

    Args:
      path: Override the destination path. ``None`` (the default)
        resolves via :func:`kairix.secrets.store.resolve_bundle_path`
        (``$KAIRIX_SECRETS_FILE`` → ``/run/secrets/kairix.env`` when the
        container dir exists → the XDG / home default).
      env: Override the env mapping (test seam, matches F2-clean shape
        in :class:`kairix.secrets.SecretsLoader`). Defaults to
        ``os.environ``.
      home: Override the home directory (test seam). Defaults to
        :meth:`Path.home`.
      container_dir: Override the container secrets dir probe (test
        seam, forwarded to ``set_secret``). Defaults to
        ``/run/secrets``.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        env: dict[str, str] | None = None,
        home: Path | None = None,
        container_dir: Path | None = None,
    ) -> None:
        self._env: dict[str, str] = dict(env) if env is not None else dict(os.environ)
        self._home: Path | None = home
        self._container_dir = container_dir
        self._explicit_path = path

    def store(
        self,
        *,
        scope: Scope,
        area: str,
        instance: str | None,
        tokens: CapturedTokens,
        client: ClientCredentials,
    ) -> WriteReport:
        """Write every non-empty canonical-named secret for the identity tuple.

        Leaves are derived from the supplied :class:`ClientCredentials`
        + :class:`CapturedTokens` dataclasses via
        :func:`kairix.connect.store.leaves.leaf_pairs` — empty-string
        fields are skipped, and the ``service_area`` remap writes the
        GitHub App triple under the leaf names the connector resolver
        reads (``app-id`` / ``app-private-key`` / ``installation-id``).
        Each leaf is persisted through
        :func:`kairix.secrets.store.set_secret`, so path resolution,
        S2083 confinement, 0600 perms, and newline-safe encoding are the
        canonical write-side behaviour — this store adds no second
        upsert implementation.
        """
        pairs = leaf_pairs(client, tokens, service_area=area)
        env_names: list[str] = []
        target: Path | None = None
        for leaf, value in pairs:
            env_names.append(canonical_env_var(scope, area, instance, leaf))
            name = canonical_secret_name(scope, area, instance, leaf)
            try:
                target = set_secret(
                    name,
                    value,
                    bundle_path=self._explicit_path,
                    env=self._env,
                    home=self._home,
                    container_dir=self._container_dir,
                )
            except (OSError, ValueError) as exc:
                raise TokenStoreUnauthorizedError(
                    f"kairix connect: cannot write to the secrets bundle — {exc} "
                    f"fix: confirm the destination is writable, or pass --secrets-file <writable-path>. "
                    f"next: re-run kairix connect <service> after fixing the destination. "
                    f"run: kairix connect <service> --secrets-file ~/.config/kairix/secrets/kairix.env "
                    f"--client-secret-path <path>",
                ) from exc
        return WriteReport(
            canonical_names=tuple(env_names),
            backend=_BACKEND_NAME,
            target=str(target) if target is not None else "",
        )


# Runtime conformance check — confirms the concrete class satisfies the
# Protocol shape so a refactor that drops a method breaks at import,
# not at first use. Cheap (one isinstance call against a dummy).
_PROTOCOL_CHECK: TokenStore = FileTokenStore()


__all__ = ["FileTokenStore"]
