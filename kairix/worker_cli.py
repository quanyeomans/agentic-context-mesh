"""
kairix worker — operator CLI for the background worker (#224 phases 4 + 5).

Subcommands:
  run        Start the worker loop (default if no subcommand given).
  status     Print the worker's last-known state from the persisted JSON file.
             Exit 0 if present, 1 if missing — so a shell monitor (or Docker
             healthcheck) can detect a never-started worker. (#224 phase 5)
  pause      Touch the pause flag file. The running worker enters PAUSED phase
             at the next loop iteration (within ``PAUSE_POLL_INTERVAL_S``) and
             stops doing task work until the flag is removed. (#224 phase 4)
  resume     Remove the pause flag file. The running worker transitions back
             to IDLE at the next loop iteration. Idempotent. (#224 phase 4)
  preflight  Audit the persistence invariants (FTS rows match documents,
             vectors match content, no orphans). Exit 0 if healthy, 1 if any
             error-severity gap. Run before every deploy / cutover; the
             worker boot path runs the same check and logs the report at
             startup. ``--auto-heal`` invokes ``rebuild_fts`` for the
             documents-without-fts gap.

Pause/resume are deliberately decoupled from the worker process: they only
toggle a touch-file in the kairix data dir. A stuck/unresponsive worker can
still be paused (so it stops piling on a shared host), and an operator pause
survives worker restarts.

Tests inject ``state_path`` / ``flag_path`` directly so they don't need to
monkeypatch env vars or touch the user's real data dir. The F30 subprocess
seam is the ``--state-path`` / ``--flag-path`` argparse args (mirrors the
``--document-root`` pattern from ``kairix store crawl``), so an outcome
test can drive the binary against a tmp path without touching the process
environment (F2-clean).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from kairix.paths import worker_pause_flag_path, worker_state_path
from kairix.worker_state import WorkerState, read_state

if TYPE_CHECKING:
    import sqlite3

# argparse store-true action — extracted because the literal appears
# on every boolean flag and the no-duplicate-string rule trips at 3+.
_STORE_TRUE = "store_true"


def _format_age(seconds_ago: float) -> str:
    """Render an epoch-delta as a short human-readable duration."""
    if seconds_ago <= 0:
        return "never"
    if seconds_ago < 60:
        return f"{int(seconds_ago)}s ago"
    if seconds_ago < 3600:
        return f"{int(seconds_ago / 60)} min ago"
    return f"{seconds_ago / 3600:.1f} h ago"


def format_status(state: WorkerState, now: float | None = None) -> str:
    """Pure renderer: turn ``WorkerState`` into a multi-line status string.

    ``now`` is injectable so a unit test can pin the clock and assert
    deterministic age renderings without monkeypatching ``time.time``.
    """
    now = now if now is not None else time.time()
    last_embed = _format_age(now - state.last_embed_run_at) if state.last_embed_run_at > 0 else "never"
    uptime = _format_age(now - state.started_at) if state.started_at > 0 else "unknown"
    lines = [
        f"Phase: {state.current_phase.value.upper()}",
        f"Last embed: {last_embed} (did work: {state.last_embed_did_work})",
        f"Embedded total: {state.embedded_total}",
        f"Failed chunks total: {state.failed_chunks_total}",
        f"Recall alerts: {state.recall_alerts_total}",
        f"Consecutive no-ops: {state.consecutive_embed_noops}",
        f"Restart count: {state.restart_count}",
        f"Uptime: {uptime}",
    ]
    return "\n".join(lines)


def status(
    *,
    state_path: Path | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    as_json: bool = False,
) -> int:
    """``kairix worker status`` — exit 0 if state file present, 1 if missing.

    I/O sinks are injectable so unit tests capture stdout/stderr without
    monkeypatching ``sys``. ``as_json=True`` renders the structured state
    envelope (the same dict ``WorkerState.to_dict`` produces) for machine
    consumers and for the F30 subprocess outcome test.
    """
    state_path = state_path if state_path is not None else worker_state_path()
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr

    state = read_state(state_path)
    if state is None:
        err.write(f"kairix worker: no state file at {state_path} — worker not running or never started\n")
        return 1
    if as_json:
        out.write(json.dumps(state.to_dict(), indent=2) + "\n")
    else:
        out.write(format_status(state) + "\n")
    return 0


def _resolve_flag_path(flag_path: Path | None) -> Path:
    """Pick the path the pause/resume commands should toggle."""
    return flag_path if flag_path is not None else worker_pause_flag_path()


def pause(*, flag_path: Path | None = None) -> int:
    """Create the pause flag file. Idempotent."""
    path = _resolve_flag_path(flag_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    print("Worker paused. Run 'kairix worker resume' to continue.")
    return 0


def resume(*, flag_path: Path | None = None) -> int:
    """Remove the pause flag file. Idempotent (missing_ok=True)."""
    path = _resolve_flag_path(flag_path)
    path.unlink(missing_ok=True)
    print("Worker resume requested. May take up to 5s for the worker to pick up the change.")
    return 0


def _open_db_for_preflight(db_path: Path | None) -> sqlite3.Connection:
    """Open the SQLite connection preflight should audit.

    Production callers pass ``db_path=None`` and we resolve the default
    via :func:`kairix.core.db.open_db`; tests pass an explicit tmp
    path so preflight runs against a sandboxed DB.
    """
    from kairix.core.db import open_db

    return open_db(db_path) if db_path is not None else open_db()


def _format_gap_line(gap: object) -> str:
    """Render one integrity gap as a short operator-readable line."""
    # Local import to keep worker_cli importable without the integrity
    # module on the path (e.g. during partial-tree static analysis).
    from kairix.core.db.integrity import IntegrityGap

    if not isinstance(gap, IntegrityGap):
        return str(gap)
    sample_str = ", ".join(gap.sample[:3])
    sample_part = f" sample=[{sample_str}]" if sample_str else ""
    return f"[{gap.severity.upper()}] {gap.invariant}: count={gap.count}{sample_part} — {gap.remediation}"


def _auto_heal_gaps(db: sqlite3.Connection, gaps: tuple[object, ...], out: TextIO) -> None:
    """Attempt remediation for the gaps we know how to fix automatically.

    Today only ``documents-without-fts`` is auto-healable via
    ``rebuild_fts``; other gaps are surfaced for operator action. The
    helper writes a one-line summary per heal attempt to ``out`` so the
    operator sees what ran.
    """
    from kairix.core.db.fts import rebuild_fts
    from kairix.core.db.integrity import INVARIANT_DOCUMENTS_WITHOUT_FTS, IntegrityGap

    for gap in gaps:
        if not isinstance(gap, IntegrityGap):
            continue
        if gap.invariant == INVARIANT_DOCUMENTS_WITHOUT_FTS:
            out.write(f"auto-heal: rebuilding FTS5 index for {gap.count} documents...\n")
            count = rebuild_fts(db)
            out.write(f"auto-heal: rebuild_fts indexed {count} documents\n")


def preflight(
    *,
    db_path: Path | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    as_json: bool = False,
    auto_heal: bool = False,
) -> int:
    """``kairix worker preflight`` — audit persistence invariants.

    Exit 0 if healthy, 1 if any error-severity gap. Warn / info gaps
    do not flip the exit code but are surfaced for visibility.

    ``--auto-heal`` runs :func:`kairix.core.db.fts.rebuild_fts` for the
    ``documents-without-fts`` gap, then re-runs the audit and reports
    the post-heal state. Mirrors the existing ``kairix embed rebuild-fts``
    surface but folded into the preflight workflow so operators have a
    single "fix what's fixable" entry point.

    The ``db_path`` / ``out`` / ``err`` kwargs are the in-process test
    seam; the CLI binds them from argparse args.
    """
    from kairix.core.db.integrity import check_integrity, report_to_dict

    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr

    db = _open_db_for_preflight(db_path)
    try:
        report = check_integrity(db)
        if auto_heal and report.gaps:
            _auto_heal_gaps(db, report.gaps, out)
            db.commit()
            # Re-check post-heal so the operator sees the new state.
            report = check_integrity(db)
    finally:
        db.close()

    if as_json:
        out.write(json.dumps(report_to_dict(report), indent=2) + "\n")
    else:
        if report.healthy and not report.gaps:
            out.write("Preflight integrity check: PASSED (no gaps detected)\n")
        else:
            status_word = "PASSED" if report.healthy else "FAILED"
            out.write(f"Preflight integrity check: {status_word} ({len(report.gaps)} gap(s))\n")
            for gap in report.gaps:
                out.write(_format_gap_line(gap) + "\n")
    return 0 if report.healthy else 1


def build_parser() -> argparse.ArgumentParser:
    """Argparse for ``kairix worker [run|status|pause|resume]``."""
    parser = argparse.ArgumentParser(
        prog="kairix worker",
        description="Background worker — observable state + operator pause/resume.",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="Start the worker loop (default).")
    status_p = sub.add_parser("status", help="Print the worker's last-known phase and counters.")
    status_p.add_argument(
        "--state-path",
        default=None,
        help=(
            "Override the worker state JSON path for this invocation. When "
            "omitted, defaults to ``kairix.paths.worker_state_path()`` (the "
            "production data dir). Mirrors the ``--document-root`` pattern "
            "from ``kairix store crawl`` so F30 subprocess outcome tests "
            "can drive a tmp state file without touching the process "
            "environment (F2-clean)."
        ),
    )
    status_p.add_argument(
        "--json",
        dest="as_json",
        action=_STORE_TRUE,
        help="Emit the full WorkerState dict as JSON on stdout (machine-readable).",
    )
    pause_p = sub.add_parser("pause", help="Pause the running worker by creating a flag file.")
    pause_p.add_argument(
        "--flag-path",
        default=None,
        help=(
            "Override the pause-flag path for this invocation. When omitted, "
            "defaults to ``kairix.paths.worker_pause_flag_path()``. Subprocess "
            "seam for F30 outcome tests; matches ``--state-path`` on status."
        ),
    )
    resume_p = sub.add_parser("resume", help="Resume the running worker by removing the flag file.")
    resume_p.add_argument(
        "--flag-path",
        default=None,
        help=(
            "Override the pause-flag path for this invocation. When omitted, "
            "defaults to ``kairix.paths.worker_pause_flag_path()``. Subprocess "
            "seam for F30 outcome tests; matches ``--state-path`` on status."
        ),
    )
    preflight_p = sub.add_parser(
        "preflight",
        help="Audit persistence invariants; exit 1 if any error-severity gap.",
    )
    preflight_p.add_argument(
        "--db-path",
        default=None,
        help=(
            "Audit this SQLite index instead of the default resolution chain "
            "(``KAIRIX_DB_PATH`` env / kairix.config.yaml / platform default). "
            "F30 subprocess seam — keeps tmp-DB injection out of "
            "monkeypatch.setenv (F2-clean)."
        ),
    )
    preflight_p.add_argument(
        "--json",
        dest="as_json",
        action=_STORE_TRUE,
        help="Emit the full IntegrityReport as JSON on stdout (machine-readable).",
    )
    preflight_p.add_argument(
        "--auto-heal",
        action=_STORE_TRUE,
        help=(
            "Run rebuild_fts for documents-without-fts gaps, then re-audit. "
            "Other gaps require operator action via the per-gap remediation."
        ),
    )
    return parser


def _resolve_state_path(arg: str | None, injected: Path | None) -> Path | None:
    """In-process ``state_path=`` kwarg wins; otherwise use ``--state-path``."""
    if injected is not None:
        return injected
    return Path(arg) if arg else None


def _resolve_flag_path_arg(arg: str | None, injected: Path | None) -> Path | None:
    """In-process ``flag_path=`` kwarg wins; otherwise use ``--flag-path``."""
    if injected is not None:
        return injected
    return Path(arg) if arg else None


def _resolve_db_path_arg(arg: str | None, injected: Path | None) -> Path | None:
    """In-process ``db_path=`` kwarg wins; otherwise use ``--db-path``."""
    if injected is not None:
        return injected
    return Path(arg) if arg else None


def main(
    argv: list[str] | None = None,
    *,
    state_path: Path | None = None,
    flag_path: Path | None = None,
    db_path: Path | None = None,
) -> int | None:
    """CLI entry point. Routes to the right subcommand.

    ``state_path`` / ``flag_path`` / ``db_path`` are the in-process
    injection seams used by unit tests. The ``--state-path`` /
    ``--flag-path`` / ``--db-path`` CLI flags are the subprocess (F30)
    seams. Precedence: in-process kwarg wins over CLI flag wins over
    ``kairix.paths`` defaults.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "status":
        resolved_state = _resolve_state_path(getattr(args, "state_path", None), state_path)
        return status(state_path=resolved_state, as_json=getattr(args, "as_json", False))
    if args.cmd == "pause":
        resolved_flag = _resolve_flag_path_arg(getattr(args, "flag_path", None), flag_path)
        return pause(flag_path=resolved_flag)
    if args.cmd == "resume":
        resolved_flag = _resolve_flag_path_arg(getattr(args, "flag_path", None), flag_path)
        return resume(flag_path=resolved_flag)
    if args.cmd == "preflight":
        resolved_db = _resolve_db_path_arg(getattr(args, "db_path", None), db_path)
        return preflight(
            db_path=resolved_db,
            as_json=getattr(args, "as_json", False),
            auto_heal=getattr(args, "auto_heal", False),
        )

    # Default (``None`` or ``run``): start the worker loop.
    from kairix.worker import main as worker_main

    worker_main()
    return None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
