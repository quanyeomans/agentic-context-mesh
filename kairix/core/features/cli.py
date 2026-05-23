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
import sys
from collections.abc import Callable
from dataclasses import asdict

from kairix.core.features.resolver import FlagStatus, status


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


def main(
    argv: list[str] | None = None,
    *,
    status_provider: Callable[[], tuple[FlagStatus, ...]] = _default_status_provider,
) -> int:
    """Entry point for ``kairix features``.

    Thin adapter — parse argv, ask ``status_provider`` for the live
    snapshot, render. The ``status_provider`` kwarg is the DI seam:
    tests pass a fake provider that returns a synthetic tuple of
    :class:`FlagStatus` rows, avoiding the need to monkey-patch the
    resolver module.
    """
    args = build_parser().parse_args(argv if argv is not None else sys.argv[2:])
    entries = status_provider()

    if args.emit_json:
        print(format_json_envelope(entries))
    else:
        print(format_table(entries))
    return 0
