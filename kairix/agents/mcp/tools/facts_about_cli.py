"""CLI entry point for ``kairix facts-about`` — MCP/CLI parity (PLA-263).

The operator-facing twin of the ``facts_about`` MCP tool. Both surfaces
call the SAME :func:`kairix.agents.mcp.tools.facts_about.tool_facts_about`
use case, so "what does kairix know about X?" answers identically whether
an agent asks over MCP or a human asks at the shell.

Like ``facts_about``, this reads only local SQLite (the fact store + the
synthetic ``entity-summaries`` collection) — no embedding model, no
network — so it answers even on a cold container.

``--document-root`` / ``--db-path`` are the F30 subprocess seams: they
let an outcome test point the command at a tmp knowledge store without
touching the process environment (F2-clean). Production callers omit them
and the command resolves the configured ``KairixPaths``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from kairix.agents.mcp.tools.facts_about import tool_facts_about
from kairix.paths import KairixPaths

# Single-source the program label so the parser, the rendered output, and the
# stderr line stay in lockstep (Sonar S1192 / F17 — no ≥10-char literal x3).
_PROG = "kairix facts-about"


def _build_parser() -> argparse.ArgumentParser:
    """Argparse for ``kairix facts-about <entity> [--namespace] [--top-k] [--json]``."""
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description=(
            "Look up what kairix knows about an entity: the current "
            "entity-attribute-value facts plus any indexed entity summary."
        ),
    )
    parser.add_argument("entity", help="The entity name to look up.")
    parser.add_argument(
        "--namespace",
        default=None,
        help="Restrict facts to a single engagement-scope namespace (default: all namespaces).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=20,
        dest="top_k",
        help="Maximum hits to return from each leg (default: 20).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the raw envelope as JSON instead of the human-readable summary.",
    )
    parser.add_argument(
        "--document-root",
        default=None,
        help=(
            "Override the document root for this invocation. Matches the canonical "
            "pattern in ``kairix bootstrap --document-root``; enables F30 subprocess "
            "outcome tests to drive a tmp knowledge store (F2-clean)."
        ),
    )
    parser.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        help="Override the index database path for this invocation (F30 subprocess seam).",
    )
    return parser


def _resolve_paths(document_root: str | None, db_path: str | None) -> KairixPaths:
    """Resolve ``KairixPaths``, applying the F30 subprocess overrides if given.

    Only ``db_path`` is load-bearing for facts_about (both legs read the
    SQLite index); ``document_root`` is carried for parity with the
    canonical CLI-outcome-test convention.
    """
    base = KairixPaths.resolve()
    overrides: dict[str, Path] = {}
    if document_root:
        overrides["document_root"] = Path(document_root)
    if db_path:
        overrides["db_path"] = Path(db_path)
    return dataclasses.replace(base, **overrides) if overrides else base


def _format_human(envelope: dict[str, Any]) -> str:
    """Human-readable summary for the default (non-``--json``) output."""
    if envelope.get("error"):
        return f"{_PROG}: {envelope['error']}: {envelope.get('detail', '')}"

    lines = [f"{_PROG}: {envelope['entity']}"]
    hits = envelope.get("hits", [])
    lines.append(f"  facts ({len(hits)}):")
    for hit in hits:
        ns = hit.get("namespace") or "—"
        lines.append(f"    - {hit['attribute']}: {hit['value']}  (confidence {hit['confidence']}, ns {ns})")

    summaries = envelope.get("entity_summaries", [])
    lines.append(f"  entity summaries ({len(summaries)}):")
    for summary in summaries:
        lines.append(f"    - {summary['summary']}  [{summary['source']}]")
    return "\n".join(lines) + "\n"


def main(
    argv: list[str] | None = None,
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """CLI entry point for ``kairix facts-about``. Returns 0 on success, 1 on error.

    ``out`` / ``err`` are the test sinks (default stdout / stderr); the
    ``--document-root`` / ``--db-path`` flags are the F30 subprocess seams.
    """
    args = _build_parser().parse_args(argv)
    out_sink = out if out is not None else sys.stdout
    err_sink = err if err is not None else sys.stderr

    paths = _resolve_paths(args.document_root, args.db_path)
    envelope = tool_facts_about(
        entity=args.entity,
        namespace=args.namespace,
        top_k=args.top_k,
        paths=paths,
    )

    if args.as_json:
        out_sink.write(json.dumps(envelope, indent=2) + "\n")
    else:
        out_sink.write(_format_human(envelope))

    if envelope.get("error"):
        err_sink.write(f"{_PROG}: {envelope['error']}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
