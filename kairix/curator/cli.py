"""
Curator agent CLI for Kairix.

Usage:
  kairix curator health [--format text|json] [--output FILE]
                        [--staleness-days N] [--no-neo4j]

Exit code is always 0 — health issues are surfaced via the report, not the
exit code, so callers (cron, OpenClaw, CI) do not see spurious failures.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _health_cmd(args: argparse.Namespace) -> None:
    from kairix.curator.health import (
        format_report_json,
        format_report_text,
        run_health_check,
    )
    from kairix.entities.schema import open_entities_db

    db = open_entities_db()

    neo4j_client = None
    if not args.no_neo4j:
        try:
            from kairix.graph.client import get_client

            client = get_client()
            if client.available:
                neo4j_client = client
        except Exception:
            pass  # Neo4j unavailable — health check proceeds without graph counts

    report = run_health_check(db, neo4j_client=neo4j_client, staleness_days=args.staleness_days)
    db.close()

    output = format_report_json(report) if args.format == "json" else format_report_text(report)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Health report written to {args.output}")
    else:
        print(output, end="")

    sys.exit(0)


def main(argv: list[str] | None = None) -> None:
    """Entry point for `kairix curator` subcommand."""
    parser = argparse.ArgumentParser(
        prog="kairix curator",
        description="Curator agent: entity graph health monitoring and enrichment.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # --- health ---
    health_parser = subparsers.add_parser(
        "health",
        help="Run entity graph health check (CA-1)",
    )
    health_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format: text (vault-ready Markdown) or json (default: text)",
    )
    health_parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Write report to FILE instead of stdout",
    )
    health_parser.add_argument(
        "--staleness-days",
        type=int,
        default=90,
        dest="staleness_days",
        metavar="N",
        help="Flag entities with no activity for N days as stale (default: 90)",
    )
    health_parser.add_argument(
        "--no-neo4j",
        action="store_true",
        default=False,
        dest="no_neo4j",
        help="Skip Neo4j availability check",
    )
    health_parser.set_defaults(func=_health_cmd)

    parsed = parser.parse_args(argv)
    parsed.func(parsed)


if __name__ == "__main__":
    main()
