"""``kairix secrets`` — operator surface for the canonical credential map.

Two subcommands:

* ``kairix secrets verify`` — for every registered alias entry, try
  to resolve the secret via :class:`SecretsLoader`. Prints a per-row
  status table (``ok`` / ``missing`` / ``legacy``). Exits non-zero if
  any required secret is missing — suitable for a pre-deploy gate.
* ``kairix secrets migrate-list`` — TSV of every legacy env-var name +
  its canonical KV replacement. Pipe into ``az keyvault secret set``
  loops to bulk-provision a fresh KV.

Both subcommands accept ``--json`` for envelope-shape output so
agent-facing tooling can consume the same data the human surface
shows.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from kairix.secrets._legacy_aliases import LEGACY_ALIASES, legacy_to_canonical_map
from kairix.secrets.loader import SecretsLoader, SecretsResolver
from kairix.secrets.naming import (
    Scope,
    canonical_env_var,
    canonical_secret_name,
)

_VERIFY = "verify"
_MIGRATE_LIST = "migrate-list"


@dataclass(frozen=True)
class _VerifyRow:
    """One row in the ``secrets verify`` output."""

    scope: Scope
    area: str
    instance: str
    leaf: str
    status: str  # "present" | "MISSING" | "present-via-legacy"
    canonical_kv: str
    legacy_used: str  # alias that resolved, or empty


def _row(
    scope: Scope,
    area: str,
    instance: str | None,
    leaf: str,
    loader: SecretsResolver,
    env: dict[str, str],
) -> _VerifyRow:
    """Build one verify row by asking the loader to resolve."""
    canonical_kv = canonical_secret_name(scope, area, instance, leaf)
    canonical_env = canonical_env_var(scope, area, instance, leaf)

    value = loader.get(scope, area, instance, leaf)
    if value is None:
        status = "MISSING"
        legacy_used = ""
    elif canonical_env in env:
        status = "present"
        legacy_used = ""
    else:
        status = "present-via-legacy"
        # Inspect which legacy alias holds the value so the operator
        # sees the exact env var to rotate.
        legacy_used = _find_resolving_alias(scope, area, instance, leaf, env)

    return _VerifyRow(
        scope=scope,
        area=area,
        instance=instance or "-",
        leaf=leaf,
        status=status,
        canonical_kv=canonical_kv,
        legacy_used=legacy_used,
    )


def _find_resolving_alias(
    scope: Scope,
    area: str,
    instance: str | None,
    leaf: str,
    env: dict[str, str],
) -> str:
    """Return the first legacy alias env-var name that has a value, or ''."""
    for alias in LEGACY_ALIASES.get((scope, area, instance, leaf), []):
        if env.get(alias):
            return alias
    return ""


def _format_verify_table(rows: Iterable[_VerifyRow]) -> str:
    """Render the verify table as a fixed-width text block."""
    header = f"{'STATUS':<20}{'SCOPE':<12}{'AREA':<18}{'INSTANCE':<11}{'LEAF':<22}{'CANONICAL'}"
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row.status:<20}{row.scope:<12}{row.area:<18}{row.instance:<11}{row.leaf:<22}{row.canonical_kv}",
        )
        if row.legacy_used:
            lines.append(
                f"  (also via legacy alias {row.legacy_used} — please migrate)",
            )
    return "\n".join(lines)


def _format_verify_json(rows: Iterable[_VerifyRow]) -> str:
    """Render the verify table as a JSON envelope."""
    payload = {"secrets": [asdict(row) for row in rows]}
    return json.dumps(payload, indent=2, sort_keys=True)


def _default_aliases_provider() -> tuple[tuple[Scope, str, str | None, str], ...]:
    """Default alias provider — every registered LEGACY_ALIASES key.

    Production callers leave the seam untouched. Tests pass a synthetic
    tuple to scope the verify walk to a known subset.
    """
    return tuple(LEGACY_ALIASES.keys())


def _default_loader_factory() -> SecretsResolver:
    """Default loader factory — fresh :class:`SecretsLoader` reading os.environ."""
    return SecretsLoader()


def _default_env_provider() -> dict[str, str]:
    """Default env provider — a snapshot of ``os.environ``."""
    import os

    return dict(os.environ)


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
    aliases_provider: Callable[[], tuple[tuple[Scope, str, str | None, str], ...]],
    env_provider: Callable[[], dict[str, str]],
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
    env = env_provider()
    aliases = aliases_provider()
    rows = [_row(scope, area, instance, leaf, loader, env) for scope, area, instance, leaf in aliases]

    rendered = _format_verify_json(rows) if emit_json else _format_verify_table(rows)
    exit_code = 1 if any(row.status == "MISSING" for row in rows) else 0
    return rendered, exit_code


def _run_migrate_list(*, emit_json: bool) -> tuple[str, int]:
    """Render the legacy -> canonical mapping as TSV (default) or JSON."""
    mapping = legacy_to_canonical_map()

    if emit_json:
        payload = {
            "mapping": [
                {"legacy_env_var": legacy, "canonical_kv_name": canonical}
                for legacy, canonical in sorted(mapping.items())
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=True), 0

    lines = ["LEGACY_ENV_VAR\tCANONICAL_KV_NAME"]
    for legacy, canonical in sorted(mapping.items()):
        lines.append(f"{legacy}\t{canonical}")
    return "\n".join(lines), 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for ``kairix secrets``."""
    parser = argparse.ArgumentParser(
        prog="kairix secrets",
        description="Inspect the canonical credential naming + alias map.",
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

    migrate = sub.add_parser(
        _MIGRATE_LIST,
        help="Emit the legacy-env-var -> canonical-KV-name mapping as TSV (or JSON with --json).",
    )
    migrate.add_argument(
        "--json",
        action="store_true",
        dest="emit_json",
        help="Emit JSON instead of TSV.",
    )

    return parser


def main(
    argv: list[str] | None = None,
    *,
    loader_factory: Callable[[], SecretsResolver] = _default_loader_factory,
    aliases_provider: Callable[
        [],
        tuple[tuple[Scope, str, str | None, str], ...],
    ] = _default_aliases_provider,
    env_provider: Callable[[], dict[str, str]] = _default_env_provider,
    bundle_path: Path | None = None,
) -> int:
    """Entry point for ``kairix secrets``.

    Thin adapter — parse argv, route to the verify or migrate-list
    branch. The keyword-only seams (``loader_factory``,
    ``aliases_provider``, ``env_provider``, ``bundle_path``) are the
    DI surface for tests; production callers leave them at their
    defaults.
    """
    args = build_parser().parse_args(argv if argv is not None else sys.argv[2:])

    if args.action == _VERIFY:
        rendered, exit_code = _run_verify(
            emit_json=args.emit_json,
            loader_factory=loader_factory,
            aliases_provider=aliases_provider,
            env_provider=env_provider,
            bundle_path=bundle_path,
        )
    else:
        rendered, exit_code = _run_migrate_list(emit_json=args.emit_json)

    print(rendered)
    return exit_code


__all__ = ["build_parser", "main"]
