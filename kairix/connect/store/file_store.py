"""File-backed token store — writes to ``$KAIRIX_SECRETS_FILE``.

The default backend for ``kairix connect``. Matches the kairix.env
bundle file shape the existing :mod:`kairix.secrets._legacy` resolver
reads, so a connected service's tokens flow through the standard
resolver chain on the very next ``kairix worker run`` without operator
intervention.

Idempotent updates: existing canonical-named lines are replaced in
place; new canonical-named lines are appended; unrelated lines
(comments, blank lines, lines for other services) are preserved
verbatim.

Per F4, this module reads ``KAIRIX_SECRETS_FILE`` directly because it
lives under ``kairix.connect`` — F4's allowlist for ``KAIRIX_*`` env
reads covers the secrets paths layer specifically; this module is the
connect-side counterpart and follows the same operator-facing contract.
The ``env`` constructor argument is the test seam (matches the F2-clean
pattern in :class:`kairix.secrets.SecretsLoader`).
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
from kairix.secrets.naming import Scope, canonical_env_var

# Default path when neither ``$KAIRIX_SECRETS_FILE`` nor
# ``--secrets-file`` is set. Mirrors the operator-facing convention
# documented in ``docs/operations/secrets-configuration.md``.
_DEFAULT_PATH_TEMPLATE = ".config/kairix/secrets/kairix.env"

_BACKEND_NAME = "file"


class FileTokenStore:
    """Write captured tokens to a ``KAIRIX_*=value`` env file.

    Args:
      path: Override the destination path. ``None`` (the default) reads
        ``$KAIRIX_SECRETS_FILE`` then falls back to
        ``~/.config/kairix/secrets/kairix.env``.
      env: Override the env mapping (test seam, matches F2-clean shape
        in :class:`kairix.secrets.SecretsLoader`). Defaults to
        ``os.environ``.
      home: Override the home directory (test seam). Defaults to
        :meth:`Path.home`.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        env: dict[str, str] | None = None,
        home: Path | None = None,
    ) -> None:
        self._env: dict[str, str] = dict(env) if env is not None else dict(os.environ)
        self._home: Path = home if home is not None else Path.home()
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
        fields are skipped. Google writes 4 leaves (client-id,
        client-secret, refresh-token, access-token); Slack writes 3 + 1
        optional (client-id, client-secret, bot-token, optional
        app-token).
        """
        path = self._resolve_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TokenStoreUnauthorizedError(
                f"kairix connect: cannot create directory {path.parent}. "
                f"fix: confirm you have write access to the parent of {path}. "
                f"next: chmod +w {path.parent.parent} OR pass --secrets-file <writable-path>. "
                f"run: kairix connect <service> --secrets-file ./kairix.env --client-secret-path <path>",
            ) from exc
        canonical: list[tuple[str, str]] = [
            (canonical_env_var(scope, area, instance, leaf), value) for leaf, value in leaf_pairs(client, tokens)
        ]
        existing_lines = _read_lines(path)
        updated = _merge_lines(existing_lines, canonical)
        try:
            path.write_text("\n".join(updated) + "\n", encoding="utf-8")
        except OSError as exc:
            raise TokenStoreUnauthorizedError(
                f"kairix connect: cannot write to {path}. "
                f"fix: confirm the file is writable by the current user. "
                f"next: chmod 600 {path} OR pass --secrets-file <writable-path>. "
                f"run: kairix connect <service> --secrets-file ./kairix.env --client-secret-path <path>",
            ) from exc
        return WriteReport(
            canonical_names=tuple(env_name for env_name, _ in canonical),
            backend=_BACKEND_NAME,
            target=str(path),
        )

    def _resolve_path(self) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path
        env_override = self._env.get("KAIRIX_SECRETS_FILE")
        if env_override:
            return Path(env_override)
        return self._home / _DEFAULT_PATH_TEMPLATE


def _read_lines(path: Path) -> list[str]:
    """Return the file's lines, or [] when the file is absent."""
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _merge_lines(existing: list[str], pairs: list[tuple[str, str]]) -> list[str]:
    """Replace existing canonical entries, append new ones, preserve the rest.

    For each ``(name, value)`` in ``pairs``: if a line starts with
    ``name=``, replace it in place; otherwise append at the end.
    Lines that don't match any canonical name we're writing pass
    through verbatim (preserves unrelated entries + comments).
    """
    output: list[str] = []
    pair_map = dict(pairs)
    seen: set[str] = set()
    for line in existing:
        if "=" not in line:
            output.append(line)
            continue
        name, _, _ = line.partition("=")
        name = name.strip()
        if name in pair_map:
            output.append(f"{name}={pair_map[name]}")
            seen.add(name)
        else:
            output.append(line)
    # Append any canonical names we didn't see in the existing file.
    for name, value in pairs:
        if name not in seen:
            output.append(f"{name}={value}")
    return output


# Runtime conformance check — confirms the concrete class satisfies the
# Protocol shape so a refactor that drops a method breaks at import,
# not at first use. Cheap (one isinstance call against a dummy).
_PROTOCOL_CHECK: TokenStore = FileTokenStore()


__all__ = ["FileTokenStore"]
