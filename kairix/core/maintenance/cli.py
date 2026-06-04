"""`kairix maintenance analyze` — operator surface for ad-hoc ANALYZE.

Operators run this after large ingests, schema changes, or when query
plans look wrong. The subcommand:

  * Captures an EXPLAIN QUERY PLAN sample on the representative hot-path
    query (``documents.collection`` lookup) before the run.
  * Runs ANALYZE on the configured kairix index DB.
  * Captures the same EXPLAIN QUERY PLAN sample after the run.
  * Emits the row count + elapsed time + the before/after plan comparison.

MCP equivalent: ``tool_maintenance_analyze`` — same envelope shape so
agents can call the same capability programmatically.

F-rule positioning:
  * F4 — no ``KAIRIX_*`` env reads; ``--db-path`` is the subprocess seam
    and ``db_path=`` is the in-process seam.
  * F30 — outcome test in
    ``tests/integration/test_outcome_maintenance_analyze_cli.py``
    asserts on stdout (not just returncode).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from kairix.core.maintenance.periodic_analyze import run_periodic_analyze

__all__ = ["AnalyzeCommandDeps", "build_parser", "main", "run_analyze_command"]


# Representative hot-path query — the 2026-06-02 production audit found
# the planner regressed on this exact pattern (idx_documents_active vs
# idx_documents_collection). EXPLAIN QUERY PLAN over this query shows
# operators "before/after" the run.
_EXPLAIN_SAMPLE_QUERY = "SELECT id FROM documents WHERE collection=? AND active=1"
_EXPLAIN_SAMPLE_PARAMS = ("default",)


def _explain_plan(db: sqlite3.Connection) -> str:
    """Return a single-line EXPLAIN QUERY PLAN summary for the sample query.

    SQLite's EXPLAIN QUERY PLAN returns rows of (id, parent, notused,
    detail); we collapse them into a single ' | '-joined string so the
    CLI report fits one line. When the documents table doesn't exist
    (fresh DB without schema), returns "<schema missing>" so the report
    doesn't crash.
    """
    try:
        # F63-bounded: EXPLAIN QUERY PLAN output is bounded by the query
        # depth (typically <10 rows). Not a data scan — produces a fixed-size
        # plan-tree summary regardless of table size.
        rows = db.execute(f"EXPLAIN QUERY PLAN {_EXPLAIN_SAMPLE_QUERY}", _EXPLAIN_SAMPLE_PARAMS).fetchall()
    except sqlite3.OperationalError:
        return "<schema missing>"
    if not rows:
        return "<empty plan>"
    return " | ".join(str(row[-1]) for row in rows)


def _count_documents(db: sqlite3.Connection) -> int:
    """Return the documents row count, defensive on legacy schemas."""
    try:
        row = db.execute("SELECT COUNT(*) FROM documents").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def _open_db_for_analyze(db_path: Path | None) -> sqlite3.Connection:
    """Open a connection on the resolved index path.

    Defers ``kairix.paths`` import to call time so the module imports
    cleanly in test contexts that haven't yet resolved KairixPaths.
    """
    if db_path is None:
        from kairix.paths import db_path as resolved_db_path

        db_path = resolved_db_path()
    # F77-allow: out-of-process diagnostic CLI, sequential with the worker boot
    db = sqlite3.connect(str(db_path))
    return db


@dataclass
class AnalyzeCommandDeps:
    """Injectable dependencies for :func:`run_analyze_command`.

    F6-clean — every field has a ``default_factory`` so production
    callers omit the Deps and get the real boundary call. Tests
    construct an overridden Deps and pass it as a single argument.

    Fields:
      * ``open_db`` — takes a ``Path | None`` and returns an open
        ``sqlite3.Connection``. Default resolves via :func:`kairix.paths.db_path`
        when the path arg is ``None``; tests pass a callable that
        ignores the path and returns a tmp-path connection.
    """

    open_db: Callable[[Path | None], sqlite3.Connection] = field(default_factory=lambda: _open_db_for_analyze)


def run_analyze_command(
    *,
    db_path: Path | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    as_json: bool = False,
    deps: AnalyzeCommandDeps | None = None,
) -> int:
    """Run the analyze command. Returns process exit code.

    Parameters
    ----------
    db_path:
        Optional explicit path to the SQLite index. ``None`` resolves
        via :func:`kairix.paths.db_path`.
    out / err:
        Optional stdout / stderr replacements for in-process tests.
    as_json:
        When True, emit the result envelope as JSON on stdout.
    deps:
        Optional :class:`AnalyzeCommandDeps` — production callers omit;
        tests pass a Deps with the ``open_db`` field overridden.
    """
    out_stream = out if out is not None else sys.stdout
    _ = err if err is not None else sys.stderr  # currently unused; reserved.

    resolved_deps = deps if deps is not None else AnalyzeCommandDeps()
    db = resolved_deps.open_db(db_path)
    try:
        plan_before = _explain_plan(db)
        # Force ANALYZE to run by bypassing the periodic decision rule —
        # the operator surface always runs (ad-hoc remediation contract).
        # We still go through run_periodic_analyze so the kairix_meta
        # bookkeeping stays consistent with the scheduler step. Passing
        # ``stale_seconds=0`` means "always stale, run it".
        result = run_periodic_analyze(db, stale_seconds=0.0)
        plan_after = _explain_plan(db)
        rows_analyzed = _count_documents(db)
    finally:
        db.close()

    envelope = {
        "analyze_ran": result.ran,
        "reason": result.reason,
        "rows_analyzed": rows_analyzed,
        "previous_doc_count": result.previous_doc_count,
        "elapsed_ms": result.elapsed_ms,
        "plan_before": plan_before,
        "plan_after": plan_after,
        "sample_query": _EXPLAIN_SAMPLE_QUERY,
    }

    if as_json:
        out_stream.write(json.dumps(envelope, indent=2) + "\n")
    else:
        out_stream.write(
            f"maintenance analyze: ANALYZE complete\n"
            f"  rows_analyzed={rows_analyzed}\n"
            f"  elapsed_ms={result.elapsed_ms:.1f}\n"
            f"  reason={result.reason}\n"
            f"  plan before: {plan_before}\n"
            f"  plan after:  {plan_after}\n"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Argparse for ``kairix maintenance analyze``."""
    parser = argparse.ArgumentParser(
        prog="kairix maintenance",
        description=(
            "Maintenance operations on the kairix SQLite index. "
            "Currently exposes: analyze (refresh planner stats; #376)."
        ),
    )
    sub = parser.add_subparsers(dest="cmd")
    analyze_p = sub.add_parser(
        "analyze",
        help=("Run ANALYZE on the index DB; refreshes sqlite_stat1 so the query planner picks the right index."),
        description=(
            "Run ANALYZE on the kairix SQLite index. Refreshes "
            "sqlite_stat1 so the query planner picks the right index "
            "for hot-path queries like documents.collection lookups. "
            "Reports the EXPLAIN QUERY PLAN before/after on a "
            "representative query so operators see the plan switch. "
            "MCP equivalent: tool_maintenance_analyze — same envelope shape."
        ),
    )
    analyze_p.add_argument(
        "--db-path",
        default=None,
        help=(
            "Override the SQLite index path. When omitted, the default "
            "resolution chain (KAIRIX_DB_PATH env / kairix.config.yaml / "
            "platform default) runs. F30 subprocess seam — keeps tmp-DB "
            "injection out of monkeypatch.setenv (F2-clean)."
        ),
    )
    analyze_p.add_argument(
        "--document-root",
        default=None,
        help=(
            "Override the document root for this invocation. Pairs with "
            "--db-path so outcome tests can run against a tmp sandbox."
        ),
    )
    analyze_p.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit the result envelope as JSON on stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``kairix maintenance``."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd != "analyze":
        parser.print_help()
        return 2

    db_path = Path(args.db_path) if args.db_path else None
    return run_analyze_command(db_path=db_path, as_json=args.as_json)


if __name__ == "__main__":  # pragma: no cover - direct script entry
    sys.exit(main())
