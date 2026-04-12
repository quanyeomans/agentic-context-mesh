"""
CLI entrypoint for qmd-azure-embed.

Usage:
  qmd-azure-embed [--force] [--limit N] [--batch-size N] [--skip-recall-check]
  qmd-azure-embed recall-check
  qmd-azure-embed status
"""

import argparse
import fcntl
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import IO

from .embed import run_embed
from .recall_check import run_recall_gate
from .schema import (
    get_qmd_db_path,
    load_sqlite_vec,
    save_run_log,
    validate_schema,
)

LOG_FILE = Path(os.environ.get("KAIRIX_EMBED_LOG", "/data/kairix/logs/azure-embed.log"))
LOCKFILE = Path("/tmp/qmd-embed.lock")  # nosec: S108 — intentional lockfile, documented in PRD §7.2
LOCK_WAIT_SECS = 60


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handlers.append(logging.FileHandler(LOG_FILE))

    logging.basicConfig(level=level, format=fmt, handlers=handlers)


def acquire_lock() -> IO[str]:
    """
    Acquire exclusive lock using the same lockfile as qmd-maintenance.sh.
    Waits up to LOCK_WAIT_SECS. Exits with code 3 if timeout.
    """
    lock_fh = open(LOCKFILE, "w")
    deadline = time.time() + LOCK_WAIT_SECS
    while time.time() < deadline:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_fh.write(str(os.getpid()))
            lock_fh.flush()
            return lock_fh
        except BlockingIOError:
            logging.info("Waiting for qmd lock...")
            time.sleep(5)
    logging.error(f"Could not acquire lock after {LOCK_WAIT_SECS}s — another embed may be running")
    sys.exit(3)


def release_lock(lock_fh: IO[str]) -> None:
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()
        LOCKFILE.unlink(missing_ok=True)
    except OSError:
        pass


def cmd_embed(args: argparse.Namespace) -> int:
    """Run the embedding pipeline."""
    logging.info(f"qmd-azure-embed starting — force={args.force} limit={args.limit} batch_size={args.batch_size}")

    lock_fh = acquire_lock()
    db_path = get_qmd_db_path()
    start = time.time()
    result = None

    try:
        db = sqlite3.connect(str(db_path))
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")

        # Note: load_sqlite_vec is called inside run_embed before any vec0 operations.
        # We still need it here for schema validation which checks the vectors_vec table.
        load_sqlite_vec(db)
        validate_schema(db)

        result = run_embed(
            db=db,
            force=args.force,
            batch_size=args.batch_size,
            limit=args.limit,
        )

        result["command"] = "embed"
        result["db_path"] = str(db_path)
        result["timestamp"] = int(start)
        save_run_log(result)

        logging.info(
            f"Done — embedded={result['embedded']} failed={result['failed']} "
            f"duration={result['duration_s']}s cost=${result['estimated_cost_usd']:.4f}"
        )

        if result["failed"] > 0:
            logging.warning(f"{result['failed']} chunks failed. Re-run without --force to retry failed chunks.")

        db.close()

    except Exception as e:
        logging.exception(f"Embed failed: {e}")
        release_lock(lock_fh)
        return 2
    finally:
        release_lock(lock_fh)

    if args.skip_recall_check:
        logging.info("Skipping recall check (--skip-recall-check)")
        return 0 if result["failed"] == 0 else 1

    # Recall gate
    logging.info("Running post-embed recall check...")
    gate_passed, recall_result = run_recall_gate()
    logging.info(f"Recall: {recall_result['passed']}/{recall_result['total']} ({recall_result['score']:.0%})")

    if not gate_passed:
        logging.error("Recall gate FAILED — search quality degraded. Check logs.")
        return 1

    return 0 if result["failed"] == 0 else 1


def cmd_recall(args: argparse.Namespace) -> int:
    """Run the recall check standalone."""
    passed, result = run_recall_gate()
    print(f"Recall: {result['passed']}/{result['total']} ({result['score']:.0%})")
    for d in result["detail"]:
        status = "✓" if d["hit"] else "✗"
        print(f"  {status} [{d['id']}] {d['query'][:60]}")
    return 0 if passed else 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show current embedding status."""
    from .schema import get_pending_chunks

    db_path = get_qmd_db_path()
    db = sqlite3.connect(str(db_path))

    pending = get_pending_chunks(db)
    total_vecs = db.execute("SELECT COUNT(*) FROM content_vectors").fetchone()[0]
    total_docs = db.execute("SELECT COUNT(*) FROM documents WHERE active=1").fetchone()[0]

    print(f"QMD index: {db_path}")
    print(f"Documents: {total_docs}")
    print(f"Vectors:   {total_vecs}")
    print(f"Pending:   {len(pending)} documents need embedding")

    # Last run
    log_path = Path.home() / ".cache" / "qmd" / "azure-embed-runs.json"
    if log_path.exists():
        import json

        try:
            runs = json.loads(log_path.read_text())
            if runs:
                last = runs[-1]
                import datetime

                ts = datetime.datetime.fromtimestamp(last.get("timestamp", 0))
                print(
                    f"Last run:  {ts.strftime('%Y-%m-%d %H:%M')} — "
                    f"embedded={last.get('embedded')} cost=${last.get('estimated_cost_usd'):.4f}"
                )
        except Exception:  # nosec S110 display failure is non-critical, logging not yet initialised
            pass  # non-critical: status display failed

    db.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="qmd-azure-embed",
        description="Azure OpenAI embedding backend for QMD",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    sub = parser.add_subparsers(dest="command")

    # embed (default)
    embed_p = sub.add_parser("embed", help="Run embedding pipeline (default)")
    embed_p.add_argument("--force", action="store_true", help="Re-embed all chunks (clears existing vectors)")
    embed_p.add_argument("--limit", type=int, default=None, help="Cap total chunks (for validation)")
    embed_p.add_argument("--batch-size", type=int, default=100, help="Chunks per Azure API call")
    embed_p.add_argument("--skip-recall-check", action="store_true", help="Skip post-embed quality gate")

    # recall-check
    sub.add_parser("recall-check", help="Run recall quality check standalone")

    # status
    sub.add_parser("status", help="Show embedding status")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command is None or args.command == "embed":
        if not hasattr(args, "force"):
            # Default subcommand
            args.force = False
            args.limit = None
            args.batch_size = 100
            args.skip_recall_check = False
        sys.exit(cmd_embed(args))
    elif args.command == "recall-check":
        sys.exit(cmd_recall(args))
    elif args.command == "status":
        sys.exit(cmd_status(args))


if __name__ == "__main__":
    main()
