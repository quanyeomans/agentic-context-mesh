"""``kairix dead-letter`` — operator-facing CLI for dead-letter triage.

Today operators have to drop to raw SQL to triage the
``connector_deadletter`` table during incidents (see #337 / #351).
This CLI surfaces the same breakdown via a single command:

* ``kairix dead-letter status`` — human-readable summary.
* ``kairix dead-letter status --json`` — same data as a JSON envelope
  for scripts + the MCP tool parity.
* ``kairix dead-letter status --source-name <name>`` — slice to one
  connector.

Thin adapter — analysis + rendering lives in
:mod:`kairix.core.observability.dead_letter_status`. ``main()`` only
parses argv, opens the connection, and writes the rendered result.

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
from typing import TextIO

from kairix.core.observability.dead_letter_status import (
    build_status,
    render_human,
    render_json,
)

_STORE_TRUE = "store_true"


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


def build_parser() -> argparse.ArgumentParser:
    """Argparse for ``kairix dead-letter [status]``."""
    parser = argparse.ArgumentParser(
        prog="kairix dead-letter",
        description="Operator triage view over the connector_deadletter table.",
    )
    sub = parser.add_subparsers(dest="cmd")
    status_p = sub.add_parser(
        "status",
        help="Show per-source dead-letter breakdown (failure_count, class, MIME, oldest).",
    )
    status_p.add_argument(
        "--db-path",
        default=None,
        help=(
            "Audit this SQLite index instead of the default resolution chain "
            "(KAIRIX_DB_PATH env / kairix.config.yaml / platform default). "
            "F30 subprocess seam — keeps tmp-DB injection out of "
            "monkeypatch.setenv (F2-clean)."
        ),
    )
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
    return parser


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
    """CLI entry point. Routes to the ``status`` subcommand.

    ``db_path`` is the in-process seam; the CLI flag is the subprocess
    seam. ``db_provider`` is the in-process DI seam for the underlying
    connection — leaving it at the default routes through
    :func:`kairix.paths.db_path`. Default subcommand is ``status`` —
    typing ``kairix dead-letter`` without an action runs status because
    that's the only action today.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    cmd = args.cmd or "status"
    if cmd == "status":
        resolved_db = _resolve_db_path_arg(getattr(args, "db_path", None), db_path)
        return status(
            db_path=resolved_db,
            source_name=getattr(args, "source_name", None),
            as_json=getattr(args, "as_json", False),
            db_provider=db_provider,
        )

    # Defensive — argparse should reject unknown subcommands first.
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
