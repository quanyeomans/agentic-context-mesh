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

Path-confinement (pythonsecurity:S2083). The destination path can flow
from operator-controlled inputs (the ``--secrets-file`` flag and
``$KAIRIX_SECRETS_FILE``). :meth:`FileTokenStore._resolve_path` runs
the resolved path through :func:`_confine_to_allowed_root` before
returning it; the helper canonicalises via
``Path.expanduser().resolve()`` and verifies the result sits under one
of the allowed roots (operator home, system temp, ``/etc/kairix``).
Escapes raise :class:`ValueError` (kept as ``ValueError`` rather than a
custom subclass so the F21-shaped ``except ValueError`` blocks at the
``kairix connect`` CLI layer catch the escape without code churn —
matches the canonical pattern in ``kairix/quality/eval/security.py``).
"""

from __future__ import annotations

import os
import tempfile
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

# System-level roots a kairix-connect-managed secrets file is allowed to
# sit under in addition to the operator's home directory. ``/etc/kairix``
# covers the packaged-deploy layout; the system temp dir covers CI runs
# and the operator's home covers dev sandbox + interactive use. Anything
# else — including an env value like ``../../etc/passwd`` that escapes
# to a system path — fails closed with :class:`ValueError`.
_SYSTEM_ALLOWED_ROOTS: tuple[Path, ...] = (
    Path("/etc/kairix"),
    Path(tempfile.gettempdir()),
)


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
            # ``path`` is the output of ``_resolve_path()`` →
            # ``_confine_to_allowed_root()``; the resolved + symlink-followed
            # path is verified to live under ``{home, /etc/kairix, tmp}`` and
            # raises ValueError on escape. Confinement contract pinned by
            # tests/unit/test_connect_store_file.py::test_*_escape_outside_allowed_roots_raises.
            # pythonsecurity:S2083 is excluded for this file in
            # sonar-project.properties (connect-store-paths block) because
            # Sonar's taint analyser doesn't recognise the allow-list pattern.
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
        """Resolve the destination, confining it to an allow-listed root.

        Source order: explicit ``path=`` constructor arg → ``$KAIRIX_SECRETS_FILE``
        env override → ``<home>/.config/kairix/secrets/kairix.env`` default.
        Every branch runs through :func:`_confine_to_allowed_root` so the
        canonicalised destination (after ``..`` collapse + symlink follow)
        sits under one of: operator's home directory, the system temp dir,
        or ``/etc/kairix``. Anything that escapes raises :class:`ValueError`
        — pythonsecurity:S2083 fix.
        """
        if self._explicit_path is not None:
            candidate = self._explicit_path
        else:
            env_override = self._env.get("KAIRIX_SECRETS_FILE")
            if env_override:
                candidate = Path(env_override)
            else:
                candidate = self._home / _DEFAULT_PATH_TEMPLATE
        return _confine_to_allowed_root(candidate, home=self._home)


def _allowed_roots(home: Path) -> tuple[Path, ...]:
    """Build the per-call allow-list, resolving each root once.

    The operator's home directory is the primary trust boundary; the
    system roots in :data:`_SYSTEM_ALLOWED_ROOTS` cover packaged-deploy
    and CI layouts. Each root is canonicalised via ``.resolve()`` so the
    ``commonpath`` comparison in :func:`_confine_to_allowed_root` sees
    the same shape on both sides.
    """
    return tuple(root.expanduser().resolve() for root in (home, *_SYSTEM_ALLOWED_ROOTS))


def _confine_to_allowed_root(candidate: Path, *, home: Path) -> Path:
    """Canonicalise ``candidate`` and verify it sits under an allowed root.

    Resolves ``candidate`` via ``Path.expanduser().resolve()`` so ``..``
    segments are collapsed and symlinks are followed before the
    allow-list check. Raises :class:`ValueError` if the resolved path
    does not sit inside any of :func:`_allowed_roots` — the helper fails
    closed (Sonar pythonsecurity:S2083 sanitiser shape).
    """
    resolved = Path(candidate).expanduser().resolve()
    allowed = _allowed_roots(home)
    for root in allowed:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved
    raise ValueError(
        f"kairix connect: secrets-file path {str(candidate)!r} escapes the allowed roots "
        f"{tuple(str(r) for r in allowed)}. "
        f"fix: pick a path under your home directory, the system temp dir, or /etc/kairix. "
        f"next: pass --secrets-file <writable-path> OR set KAIRIX_SECRETS_FILE to a path under those roots. "
        f"run: kairix connect <service> --secrets-file ~/.config/kairix/secrets/kairix.env --client-secret-path <path>",
    )


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
