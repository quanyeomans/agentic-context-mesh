"""``kairix dead-letter`` — operator-facing CLI for dead-letter triage.

Today operators have to drop to raw SQL to triage the
``connector_deadletter`` table during incidents (see #337 / #351).
This CLI surfaces the same breakdown via a single command:

* ``kairix dead-letter status`` — human-readable summary.
* ``kairix dead-letter status --json`` — same data as a JSON envelope
  for scripts + the MCP tool parity.
* ``kairix dead-letter status --source-name <name>`` — slice to one
  connector.
* ``kairix dead-letter drain [SOURCE]`` — clear the permanently-
  unprocessable backlog for ONE source (named), or EVERY distinct
  source (no name). Works for an ORPHANED source whose connector is no
  longer active — the gap the per-connector auto-drain leaves open.
* ``kairix dead-letter drain --dry-run`` — report what WOULD drain
  (counts per source + per failure class) WITHOUT mutating.

Thin adapter — analysis + rendering lives in
:mod:`kairix.core.observability.dead_letter_status`; the drain core lives
in :mod:`kairix.core.connectors.deadletter_drain`. ``main()`` only parses
argv, opens the connection, and writes the rendered result.

The ``--db-path`` flag is the F30 subprocess seam (matches
``--state-path`` / ``--flag-path`` on ``kairix worker``). The
``db_path=`` kwarg on :func:`status` is the in-process injection seam.
Tests prefer the kwarg so they never need ``KAIRIX_*`` env vars.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from kairix.core.connectors.deadletter_drain import _DEFAULT_PER_TICK_MAX_ITEMS
from kairix.core.observability.dead_letter_status import (
    build_status,
    render_human,
    render_json,
)

if TYPE_CHECKING:
    from kairix.core.connectors.deadletter_drain import DrainSummary

_STORE_TRUE = "store_true"

# The argparse dest for the source-name argument — held once (F17) so the
# ``add_argument`` registration and every ``getattr(args, ...)`` read of it
# reference a single literal rather than repeating the string.
_ARG_SOURCE_NAME = "source_name"

# Default per-source scan cap for the ``drain`` verb — re-exported from the
# drain core so the CLI flag default and the core stay in lock-step (F17:
# the magic number lives in one place).
_DEFAULT_MAX_ITEMS = _DEFAULT_PER_TICK_MAX_ITEMS


def default_db_provider(db_path: Path | None) -> sqlite3.Connection:
    """Default SQLite connection provider for the dead-letter status surface.

    Production callers pass ``db_path=None`` and the resolver delegates
    to :func:`kairix.paths.db_path`. Operator override via ``--db-path``
    flows through as an explicit ``Path``. The function is exposed as
    the public DI seam so unit tests can pass an in-process provider
    pointing at a tmp DB without monkeypatching ``kairix.paths``.
    """
    if db_path is not None:
        # F77-allow: out-of-process diagnostic CLI (`kairix dead-letter status`)
        return sqlite3.connect(str(db_path))
    from kairix.paths import db_path as resolve_db

    # F77-allow: same out-of-process diagnostic CLI, default-path branch
    return sqlite3.connect(str(resolve_db()))


DbProvider = Callable[[Path | None], sqlite3.Connection]


def status(
    *,
    db_path: Path | None = None,
    source_name: str | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    as_json: bool = False,
    db_provider: DbProvider = default_db_provider,
) -> int:
    """``kairix dead-letter status`` — emit the triage snapshot.

    Always exits 0 unless the DB read itself fails (in which case an
    F21-shaped failure line lands on stderr and the exit code is 1).
    Empty dead-letter state is a happy path and exits 0.

    ``db_provider`` is the in-process DI seam: production callers leave
    it at :func:`default_db_provider`; unit tests pass an in-process
    provider returning a tmp-scoped connection so the test exercises
    every code path without monkeypatching :mod:`kairix.paths`.
    """
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr

    try:
        conn = db_provider(db_path)
    except sqlite3.Error as exc:
        err.write(
            "kairix dead-letter status: could not open SQLite db. "
            f"fix: check the --db-path argument or KAIRIX_DB_PATH config. "
            f"next: confirm the db exists and is readable. "
            f"run: ls -l {db_path or '<configured-default>'}\n"
            f"underlying error: {type(exc).__name__}: {exc}\n"
        )
        return 1

    with closing(conn):
        try:
            report = build_status(conn, source_name=source_name)
        except sqlite3.Error as exc:
            err.write(
                "kairix dead-letter status: SELECT failed against connector_deadletter. "
                "fix: confirm the kairix schema is applied (run `kairix worker preflight`). "
                "next: if the schema is current, share the underlying error with the kairix team. "
                "run: kairix worker preflight --db-path <path>\n"
                f"underlying error: {type(exc).__name__}: {exc}\n"
            )
            return 1

    if as_json:
        out.write(json.dumps(render_json(report), indent=2) + "\n")
    else:
        out.write(render_human(report))
    return 0


_DRAIN_HEADER = "kairix dead-letter drain"


def _render_drain_summary(summary: DrainSummary, *, dry_run: bool) -> str:
    """One human-readable line per drained (or previewed) source."""
    verb = "would drain" if dry_run else "drained"
    return (
        f"  {summary.connector_name}: {verb} {summary.drained} "
        f"(corrupt_zip={summary.corrupt_zip}, unsupported_mime={summary.unsupported_mime}) "
        f"left={summary.left}\n"
    )


def _write_drain_report(summaries: tuple[DrainSummary, ...], *, dry_run: bool, out: TextIO) -> None:
    """Print the per-source summary block + a totals footer."""
    mode = " (dry-run — nothing was mutated)" if dry_run else ""
    out.write(f"{_DRAIN_HEADER}{mode}\n")
    if not summaries:
        out.write("  no drainable dead-letter state found.\n")
        return
    total = 0
    for summary in summaries:
        out.write(_render_drain_summary(summary, dry_run=dry_run))
        total += summary.drained
    verb = "would drain" if dry_run else "drained"
    out.write(f"  total: {verb} {total} across {len(summaries)} source(s).\n")


def _run_drain(
    conn: sqlite3.Connection, *, source_name: str | None, dry_run: bool, max_items: int
) -> tuple[DrainSummary, ...]:
    """Dispatch one or all sources through the drain core.

    Builds the silver processor on the SAME connection the core commits
    on. When ``source_name`` is given, drains just that source (works for
    orphaned/inactive sources); otherwise sweeps every distinct source.
    Always returns a tuple of :class:`DrainSummary` so the renderer is
    uniform.
    """
    from kairix.core.connectors.deadletter_drain import (
        drain_all_source_deadletters,
        drain_source_deadletters,
    )
    from kairix.core.connectors.silver import (
        DefaultSilverProcessor,
        SqliteDocumentsMediaWriter,
        SqliteSilverSourceWriter,
    )

    silver = DefaultSilverProcessor(
        documents_media_writer=SqliteDocumentsMediaWriter(conn),
        silver_source_writer=SqliteSilverSourceWriter(conn),
    )
    if source_name is not None:
        one = drain_source_deadletters(
            conn, source_name=source_name, silver=silver, dry_run=dry_run, max_items=max_items
        )
        return (one,)
    return drain_all_source_deadletters(conn, silver=silver, dry_run=dry_run, max_items=max_items)


def drain(
    *,
    db_path: Path | None = None,
    source_name: str | None = None,
    dry_run: bool = False,
    max_items: int = _DEFAULT_MAX_ITEMS,
    out: TextIO | None = None,
    err: TextIO | None = None,
    db_provider: DbProvider = default_db_provider,
) -> int:
    """``kairix dead-letter drain [SOURCE]`` — clear the poisoned backlog.

    Mutating one-shot for the orphaned-source backlog the periodic worker
    sweep also covers. With ``source_name`` drains that one source (an
    inactive/orphaned source drains just as well as an active one); without
    it, drains EVERY distinct ``source_name`` in ``connector_deadletter``.
    Eligibility is the EXISTING narrow rule — corrupt_zip OR a
    known-unsupported MIME — never broadened here. ``dry_run`` reports what
    WOULD drain without mutating.

    Exits 0 on success (an empty backlog is a happy path); exits 1 only
    when the DB cannot be opened. ``db_provider`` is the in-process DI
    seam mirroring :func:`status`.
    """
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    try:
        conn = db_provider(db_path)
    except sqlite3.Error as exc:
        err.write(
            "kairix dead-letter drain: could not open SQLite db. "
            "fix: check the --db-path argument or KAIRIX_DB_PATH config. "
            "next: confirm the db exists and is readable. "
            f"run: ls -l {db_path or '<configured-default>'}\n"
            f"underlying error: {type(exc).__name__}: {exc}\n"
        )
        return 1

    with closing(conn):
        summaries = _run_drain(conn, source_name=source_name, dry_run=dry_run, max_items=max_items)
    _write_drain_report(summaries, dry_run=dry_run, out=out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Argparse for ``kairix dead-letter [status|drain]``."""
    parser = argparse.ArgumentParser(
        prog="kairix dead-letter",
        description="Operator triage view over the connector_deadletter table.",
    )
    sub = parser.add_subparsers(dest="cmd")
    _add_status_parser(sub)
    _add_drain_parser(sub)
    return parser


def _add_db_path_arg(subparser: argparse.ArgumentParser) -> None:
    """Attach the shared ``--db-path`` F30 subprocess seam to a subparser."""
    subparser.add_argument(
        "--db-path",
        default=None,
        help=(
            "Audit this SQLite index instead of the default resolution chain "
            "(KAIRIX_DB_PATH env / kairix.config.yaml / platform default). "
            "F30 subprocess seam — keeps tmp-DB injection out of "
            "monkeypatch.setenv (F2-clean)."
        ),
    )


def _add_status_parser(sub: Any) -> None:
    """Register the ``status`` subcommand."""
    status_p = sub.add_parser(
        "status",
        help="Show per-source dead-letter breakdown (failure_count, class, MIME, oldest).",
    )
    _add_db_path_arg(status_p)
    status_p.add_argument(
        "--source-name",
        default=None,
        help="Restrict the report to a single connector (e.g. sharepoint, github, notion).",
    )
    status_p.add_argument(
        "--json",
        dest="as_json",
        action=_STORE_TRUE,
        help="Emit the full report as JSON on stdout (machine-readable, parity with the MCP tool).",
    )


def _add_drain_parser(sub: Any) -> None:
    """Register the ``drain`` subcommand."""
    drain_p = sub.add_parser(
        "drain",
        help="Clear permanently-unprocessable dead-letters for one source (or every source).",
    )
    _add_db_path_arg(drain_p)
    drain_p.add_argument(
        _ARG_SOURCE_NAME,
        nargs="?",
        default=None,
        help="Drain just this source (e.g. sharepoint); omit to drain every distinct source.",
    )
    drain_p.add_argument(
        "--dry-run",
        dest="dry_run",
        action=_STORE_TRUE,
        help="Report what WOULD drain (per source + per failure class) WITHOUT mutating.",
    )
    drain_p.add_argument(
        "--max",
        dest="max_items",
        type=int,
        default=_DEFAULT_MAX_ITEMS,
        help=f"Per-source scan cap (default {_DEFAULT_MAX_ITEMS}); a deeper backlog drains over multiple runs.",
    )


def _resolve_db_path_arg(arg: str | None, injected: Path | None) -> Path | None:
    """In-process ``db_path=`` kwarg wins; otherwise use ``--db-path``."""
    if injected is not None:
        return injected
    return Path(arg) if arg else None


def main(
    argv: list[str] | None = None,
    *,
    db_path: Path | None = None,
    db_provider: DbProvider = default_db_provider,
) -> int | None:
    """CLI entry point. Routes to the ``status`` or ``drain`` subcommand.

    ``db_path`` is the in-process seam; the CLI flag is the subprocess
    seam. ``db_provider`` is the in-process DI seam for the underlying
    connection — leaving it at the default routes through
    :func:`kairix.paths.db_path`. Default subcommand is ``status`` —
    typing ``kairix dead-letter`` without an action runs the read-only
    triage view rather than the mutating drain.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    cmd = args.cmd or "status"
    resolved_db = _resolve_db_path_arg(getattr(args, "db_path", None), db_path)
    if cmd == "status":
        return status(
            db_path=resolved_db,
            source_name=getattr(args, _ARG_SOURCE_NAME, None),
            as_json=getattr(args, "as_json", False),
            db_provider=db_provider,
        )
    if cmd == "drain":
        return drain(
            db_path=resolved_db,
            source_name=getattr(args, _ARG_SOURCE_NAME, None),
            dry_run=getattr(args, "dry_run", False),
            max_items=getattr(args, "max_items", _DEFAULT_MAX_ITEMS),
            db_provider=db_provider,
        )

    # Defensive — argparse should reject unknown subcommands first.
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
