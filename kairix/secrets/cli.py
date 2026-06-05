"""``kairix secrets`` — operator surface for the canonical credential map.

One subcommand:

* ``kairix secrets verify`` — for every registered credential identity,
  try to resolve the secret via :class:`SecretsLoader`. Prints a per-row
  status table (``present`` / ``MISSING``). Exits non-zero if any
  required secret is missing — suitable for a pre-deploy gate.

The ``verify`` subcommand accepts ``--json`` for envelope-shape output
so agent-facing tooling can consume the same data the human surface
shows.

The legacy alias chain (``LEGACY_ALIASES`` + ``migrate-list``) was
retired in #369; operators with pre-canonical env-var names must
rotate to the ``KAIRIX_<SCOPE>_<AREA>[_<INSTANCE>]_<LEAF>`` form.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from kairix.secrets.loader import SecretsLoader, SecretsResolver
from kairix.secrets.naming import (
    Scope,
    canonical_secret_name,
)

_VERIFY = "verify"

# Hoisted leaf constant — F17 (no string literal ≥10 chars duplicated
# ≥3 times). Shared across M365, Slack, Gmail rows that all carry an
# OAuth-style client secret with the same leaf name.
_LEAF_CLIENT_SECRET = "client-secret"  # noqa: S105 — secret-SLOT name (the leaf identifier), not a value  # pragma: allowlist secret


# Canonical credential identities registered for verification. Each
# tuple is ``(scope, area, instance, leaf)`` — the same identity the
# loader resolves. Operators add a row here when shipping a new
# credential surface; the verify CLI then reports its status.
_REGISTERED_IDENTITIES: tuple[tuple[Scope, str, str | None, str], ...] = (
    # ── Infrastructure: Neo4j ──────────────────────────────────────
    ("infra", "neo4j", None, "password"),
    ("infra", "neo4j", None, "uri"),
    ("infra", "neo4j", None, "user"),
    # ── Providers: LLM (chat) ──────────────────────────────────────
    ("provider", "llm", None, "api-key"),
    ("provider", "llm", None, "endpoint"),
    ("provider", "llm", None, "model"),
    # ── Providers: embeddings ──────────────────────────────────────
    ("provider", "embed", None, "api-key"),
    ("provider", "embed", None, "endpoint"),
    ("provider", "embed", None, "model"),
    # ── Connectors: M365 ───────────────────────────────────────────
    ("connector", "m365", None, "tenant-id"),
    ("connector", "m365", None, "client-id"),
    ("connector", "m365", None, _LEAF_CLIENT_SECRET),
    # ── Connectors: Slack ──────────────────────────────────────────
    ("connector", "slack", None, "bot-token"),
    ("connector", "slack", None, "app-token"),
    ("connector", "slack", None, "client-id"),
    ("connector", "slack", None, _LEAF_CLIENT_SECRET),
    # ── Connectors: GitHub ─────────────────────────────────────────
    ("connector", "github", None, "pat"),
    ("connector", "github", None, "app-id"),
    ("connector", "github", None, "installation-id"),
    ("connector", "github", None, "app-private-key"),
    ("connector", "github", None, "webhook-secret"),
    # ── Connectors: Notion ─────────────────────────────────────────
    ("connector", "notion", None, "token"),
    # ── Connectors: Google Drive ───────────────────────────────────
    ("connector", "google-drive", None, "access-token"),
    # ── Connectors: Gmail ──────────────────────────────────────────
    ("connector", "gmail", None, "client-id"),
    ("connector", "gmail", None, _LEAF_CLIENT_SECRET),
    ("connector", "gmail", None, "refresh-token"),
    ("connector", "gmail", None, "access-token"),
    # ── Connectors: Apple CalDAV ───────────────────────────────────
    ("connector", "apple-caldav", None, "username"),
    ("connector", "apple-caldav", None, "access"),
    # ── Connectors: Dex CRM ────────────────────────────────────────
    ("connector", "dex", None, "api-key"),
)


@dataclass(frozen=True)
class _VerifyRow:
    """One row in the ``secrets verify`` output."""

    scope: Scope
    area: str
    instance: str
    leaf: str
    status: str  # "present" | "MISSING"
    canonical_kv: str


def _row(
    scope: Scope,
    area: str,
    instance: str | None,
    leaf: str,
    loader: SecretsResolver,
) -> _VerifyRow:
    """Build one verify row by asking the loader to resolve."""
    canonical_kv = canonical_secret_name(scope, area, instance, leaf)
    value = loader.get(scope, area, instance, leaf)
    status = "MISSING" if value is None else "present"
    return _VerifyRow(
        scope=scope,
        area=area,
        instance=instance or "-",
        leaf=leaf,
        status=status,
        canonical_kv=canonical_kv,
    )


def _format_verify_table(rows: Iterable[_VerifyRow]) -> str:
    """Render the verify table as a fixed-width text block."""
    header = f"{'STATUS':<10}{'SCOPE':<12}{'AREA':<18}{'INSTANCE':<11}{'LEAF':<22}{'CANONICAL'}"
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row.status:<10}{row.scope:<12}{row.area:<18}{row.instance:<11}{row.leaf:<22}{row.canonical_kv}",
        )
    return "\n".join(lines)


def _format_verify_json(rows: Iterable[_VerifyRow]) -> str:
    """Render the verify table as a JSON envelope."""
    payload = {"secrets": [asdict(row) for row in rows]}
    return json.dumps(payload, indent=2, sort_keys=True)


def _default_identities_provider() -> tuple[tuple[Scope, str, str | None, str], ...]:
    """Default identity provider — every registered canonical credential.

    Production callers leave the seam untouched. Tests pass a synthetic
    tuple to scope the verify walk to a known subset.
    """
    return _REGISTERED_IDENTITIES


def _default_loader_factory() -> SecretsResolver:
    """Default loader factory — fresh :class:`SecretsLoader` reading os.environ."""
    return SecretsLoader()


def _ensure_bundle_loaded(bundle_path: Path | None = None) -> None:
    """Hydrate the secrets bundle via the canonical bootstrap_secrets.

    Thin shim — kept for backwards-compat with existing tests that
    call it directly. New code should call
    ``kairix.secrets.bootstrap.bootstrap_secrets`` (or just let the
    CLI dispatcher's bootstrap call cover it).
    """
    from kairix.secrets.bootstrap import bootstrap_secrets

    bootstrap_secrets(bundle_path=bundle_path, force=True)


def _run_verify(
    *,
    emit_json: bool,
    loader_factory: Callable[[], SecretsResolver],
    identities_provider: Callable[[], tuple[tuple[Scope, str, str | None, str], ...]],
    bundle_path: Path | None = None,
) -> tuple[str, int]:
    """Build the verify table + return (rendered_output, exit_code).

    Exit code is 1 if any row is MISSING, 0 otherwise.

    ``bundle_path`` is the test seam for the bundle-hydration step;
    production callers leave it as None (load_secrets reads
    ``$KAIRIX_SECRETS_FILE`` / the default path).
    """
    _ensure_bundle_loaded(bundle_path)
    loader = loader_factory()
    identities = identities_provider()
    rows = [_row(scope, area, instance, leaf, loader) for scope, area, instance, leaf in identities]

    rendered = _format_verify_json(rows) if emit_json else _format_verify_table(rows)
    exit_code = 1 if any(row.status == "MISSING" for row in rows) else 0
    return rendered, exit_code


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for ``kairix secrets``."""
    parser = argparse.ArgumentParser(
        prog="kairix secrets",
        description="Inspect the canonical credential naming surface.",
    )
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")

    verify = sub.add_parser(
        _VERIFY,
        help="Resolve every registered credential via SecretsLoader; non-zero exit on any miss.",
    )
    verify.add_argument(
        "--json",
        action="store_true",
        dest="emit_json",
        help="Emit a JSON envelope instead of the human-readable table.",
    )

    return parser


def main(
    argv: list[str] | None = None,
    *,
    loader_factory: Callable[[], SecretsResolver] = _default_loader_factory,
    identities_provider: Callable[
        [],
        tuple[tuple[Scope, str, str | None, str], ...],
    ] = _default_identities_provider,
    bundle_path: Path | None = None,
) -> int:
    """Entry point for ``kairix secrets``.

    Thin adapter — parse argv, route to the verify branch. The
    keyword-only seams (``loader_factory``, ``identities_provider``,
    ``bundle_path``) are the DI surface for tests; production callers
    leave them at their defaults.
    """
    args = build_parser().parse_args(argv if argv is not None else sys.argv[2:])

    rendered, exit_code = _run_verify(
        emit_json=args.emit_json,
        loader_factory=loader_factory,
        identities_provider=identities_provider,
        bundle_path=bundle_path,
    )

    print(rendered)
    return exit_code


__all__ = ["build_parser", "main"]
