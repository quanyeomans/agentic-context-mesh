"""`kairix features` — operator surface for the feature-flag pattern.

See ``docs/architecture/feature-flag-architecture.md`` §3.5. Two
subcommands today; both emit the same envelope so the operator never
has to learn two shapes:

* ``kairix features status`` — text table of every registered flag.
* ``kairix features status --json`` — same data as a JSON envelope.

CLI/MCP parity: the ``tool_features_status`` MCP tool returns the same
JSON envelope so agents can self-introspect what's enabled.

Thin adapter pattern: all business logic lives in
:mod:`kairix.core.features.resolver`. ``main()`` only parses argv and
renders the result.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict

from kairix.core.features.resolver import FlagStatus, status
from kairix.core.features.topology_v2_status import (
    TopologyV2Diagnostics,
    build_topology_v2_diagnostics,
    render_topology_v2_human,
    render_topology_v2_json,
)

# F17 — flag string duplicated across the parser + arg lookup.
_FLAG_TOPOLOGY_V2 = "--topology-v2"

DiagnosticsProvider = Callable[[], TopologyV2Diagnostics | None]

# Path-aware variant — production default takes the parsed --db-path arg
# (or None) and returns the matching topology v2 diagnostics, or None
# when the read fails. Decoupling from the args.db_path string keeps the
# seam composable; the args.db_path is the operator's CLI surface, not
# part of the DI surface.
PathAwareDiagnosticsProvider = Callable[[str | None], TopologyV2Diagnostics | None]


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser used by :func:`main`."""
    parser = argparse.ArgumentParser(
        prog="kairix features",
        description="Inspect the registered feature flags + their effective state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")

    status_parser = sub.add_parser(
        "status",
        help="Show every registered flag + its default / effective value.",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        dest="emit_json",
        help="Emit a JSON envelope on stdout instead of the human-readable table.",
    )
    status_parser.add_argument(
        _FLAG_TOPOLOGY_V2,
        action="store_true",
        dest="emit_topology_v2",
        help=(
            "Include topology v2 diagnostics (declared cc_pairs + per-actor "
            "scope-profile resolution) in the output. Backward-compatible — "
            "absent the flag, output is unchanged from pre-Wave-D."
        ),
    )
    status_parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="SQLite path for the topology v2 read (default: platform default via kairix.paths.db_path).",
    )
    return parser


def format_table(entries: tuple[FlagStatus, ...]) -> str:
    """Render the status entries as a fixed-width table.

    Empty registry → operator-friendly "no flags registered" line so
    the surface degrades cleanly during the PR-2 → PR-6 window when
    the registry is empty.
    """
    if not entries:
        return "No feature flags registered."

    header = f"{'NAME':<34}{'DEFAULT':<9}{'EFFECTIVE':<11}{'STAGE':<12}{'RETIRE-BY':<14}{'SOURCE':<8}"
    rows = [header]
    for entry in entries:
        rows.append(
            f"{entry.name:<34}"
            f"{str(entry.default).lower():<9}"
            f"{str(entry.effective).lower():<11}"
            f"{entry.stage:<12}"
            f"{entry.target_retire_in:<14}"
            f"{entry.source:<8}"
        )
    return "\n".join(rows)


def format_json_envelope(entries: tuple[FlagStatus, ...]) -> str:
    """Render the status entries as the JSON envelope shape (§3.5)."""
    payload = {"flags": [asdict(entry) for entry in entries]}
    return json.dumps(payload, indent=2, sort_keys=True)


def _default_status_provider() -> tuple[FlagStatus, ...]:
    """Production status provider — calls into the resolver."""
    return status()


def _build_default_diagnostics_provider(db_path: str | None) -> DiagnosticsProvider:
    """Build the production diagnostics provider that opens the SQLite db.

    Factored so :func:`main` and the MCP tool can both call into a
    one-liner that handles the connection lifecycle. Returns ``None``
    on read failure (e.g. missing topology v2 tables on an old schema)
    so the surface degrades to the legacy view rather than crashing.
    """

    def _provider() -> TopologyV2Diagnostics | None:
        try:
            if db_path is not None:
                conn = sqlite3.connect(db_path)
            else:
                from kairix.paths import db_path as resolve_db

                conn = sqlite3.connect(str(resolve_db()))
            with closing(conn):
                return build_topology_v2_diagnostics(conn)
        except sqlite3.Error:
            return None

    return _provider


def _default_path_aware_diagnostics(db_path: str | None) -> TopologyV2Diagnostics | None:
    """Production path-aware diagnostics resolver — composes the provider."""
    return _build_default_diagnostics_provider(db_path)()


def format_json_envelope_with_topology(
    entries: tuple[FlagStatus, ...],
    diag: TopologyV2Diagnostics | None,
) -> str:
    """Render the JSON envelope with the topology v2 diagnostics merged in.

    Backward-compatible: when ``diag is None`` (e.g. operator omitted
    ``--topology-v2`` or read failed), output matches
    :func:`format_json_envelope`.
    """
    payload: dict[str, object] = {"flags": [asdict(entry) for entry in entries]}
    if diag is not None:
        payload["topology_v2"] = render_topology_v2_json(diag)
    return json.dumps(payload, indent=2, sort_keys=True)


def format_table_with_topology(
    entries: tuple[FlagStatus, ...],
    diag: TopologyV2Diagnostics | None,
) -> str:
    """Render the table with the topology v2 diagnostics appended.

    Backward-compatible: when ``diag is None``, output matches
    :func:`format_table`.
    """
    base = format_table(entries)
    if diag is None:
        return base
    return base + "\n\n" + render_topology_v2_human(diag)


def main(
    argv: list[str] | None = None,
    *,
    status_provider: Callable[[], tuple[FlagStatus, ...]] = _default_status_provider,
    read_topology_v2: PathAwareDiagnosticsProvider = _default_path_aware_diagnostics,
) -> int:
    """Entry point for ``kairix features``.

    Thin adapter — parse argv, ask ``status_provider`` for the live
    snapshot, render. The ``status_provider`` kwarg is the DI seam:
    tests pass a fake provider that returns a synthetic tuple of
    :class:`FlagStatus` rows, avoiding the need to monkey-patch the
    resolver module.

    ``read_topology_v2`` is the topology v2 DI seam: takes the
    operator's ``--db-path`` arg (or None) and returns the matching
    :class:`TopologyV2Diagnostics` (or None on read failure). The
    production default delegates to :func:`_default_path_aware_diagnostics`
    so callers get the configured SQLite db without seam plumbing.
    """
    args = build_parser().parse_args(argv if argv is not None else sys.argv[2:])
    entries = status_provider()

    diag: TopologyV2Diagnostics | None = None
    if getattr(args, "emit_topology_v2", False):
        diag = read_topology_v2(args.db_path)

    if args.emit_json:
        print(format_json_envelope_with_topology(entries, diag))
    else:
        print(format_table_with_topology(entries, diag))
    return 0
