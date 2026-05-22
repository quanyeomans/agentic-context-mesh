"""
kairix bootstrap — orientation envelope CLI (#246 W1).

Usage:
    kairix bootstrap <agent>           # structured markdown to stdout
    kairix bootstrap <agent> --json    # JSON envelope to stdout
    kairix bootstrap <agent> --max-memory-days N

Mirrors the ``kairix worker`` CLI dispatch pattern (``kairix/worker_cli.py``):
the top-level ``kairix/cli.py`` resolves the subcommand and calls
:func:`main` here. This module owns argparse, output rendering, and
exit codes; the underlying use case lives in
``kairix.use_cases.bootstrap``.

Both stdout/stderr sinks are injectable for unit testing — the CLI
never monkeypatches ``sys.stdout`` in production code paths.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from kairix.use_cases.bootstrap import (
    BootstrapDeps,
    bootstrap_output_to_envelope,
    bootstrap_output_to_markdown,
    run_bootstrap,
)


def build_parser() -> argparse.ArgumentParser:
    """Argparse for ``kairix bootstrap <agent> [--json] [--max-memory-days N]``."""
    parser = argparse.ArgumentParser(
        prog="kairix bootstrap",
        description="Return the agent orientation envelope: role, board, recent memory, goals, health.",
    )
    parser.add_argument(
        "agent",
        help="Agent name — used as the directory slug under ${KAIRIX_DOCUMENT_ROOT}/04-Agent-Knowledge/.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit a JSON envelope instead of structured markdown.",
    )
    parser.add_argument(
        "--max-memory-days",
        type=int,
        default=3,
        help="Number of newest daily memory files to include (default: 3, 0 for none).",
    )
    parser.add_argument(
        "--document-root",
        default=None,
        help=(
            "Override the document root for this invocation. When omitted, "
            "the default resolution chain (KAIRIX_DOCUMENT_ROOT env / "
            "kairix.config.yaml / platform default) runs. Matches the "
            "canonical pattern in ``kairix store crawl --document-root``; "
            "enables F30 subprocess outcome tests to drive a tmp vault "
            "without touching the process environment (F2-clean)."
        ),
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
    deps: BootstrapDeps | None = None,
) -> int:
    """CLI entry point. Returns 0 on success, 1 when bootstrap errored.

    The ``error`` field on the envelope drives the exit code so shell
    monitors (and the future docker-compose healthcheck) get a clean
    signal even when ``--json`` is used.

    ``deps`` is the test injection seam — production callers leave it
    None and the use case's default factory wires the real probes.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    out_sink = out if out is not None else sys.stdout
    err_sink = err if err is not None else sys.stderr

    # ``--document-root`` is the F30 subprocess seam: when supplied (and no
    # explicit ``deps`` was injected by an in-process caller) we build a
    # ``BootstrapDeps`` whose ``document_root_fn`` points at the arg, so the
    # downstream resolution skips ``kairix.paths.document_root()`` and its
    # env-var read. In-process callers (``deps`` non-None) win — same
    # precedence as the existing ``deps`` seam.
    effective_deps = deps
    if effective_deps is None and args.document_root:
        root = Path(args.document_root)
        effective_deps = BootstrapDeps(document_root_fn=lambda: root)

    result = run_bootstrap(args.agent, deps=effective_deps, max_memory_days=args.max_memory_days)

    if args.as_json:
        out_sink.write(json.dumps(bootstrap_output_to_envelope(result), indent=2) + "\n")
    else:
        out_sink.write(bootstrap_output_to_markdown(result))

    if result.error:
        err_sink.write(f"kairix bootstrap: {result.error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
