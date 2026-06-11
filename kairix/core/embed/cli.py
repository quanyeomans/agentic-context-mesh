"""
CLI entrypoint for kairix embed.

Usage:
  kairix embed [--force] [--limit N] [--batch-size N] [--skip-recall-check]
  kairix embed recall-check
  kairix embed status
"""

import argparse
import fcntl
import logging
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from kairix.core.db import get_db_path, open_db

from .embed import DEFAULT_BATCH_SIZE, DEFAULT_PARALLEL_BATCHES, MAX_PARALLEL_BATCHES
from .recall_check import run_recall_gate

# F17 — argparse action keyword repeated across boolean-flag declarations; one
# constant keeps the well-known sentinel in a single edit site.
_STORE_TRUE = "store_true"


def _default_pipeline_runner() -> Callable[..., Any]:
    """Lazy-import the pipeline runner so cmd_embed stays cheap to import."""
    from kairix.core.embed.use_cases import run_incremental_embed_pipeline

    return run_incremental_embed_pipeline


def default_run_recall_gate() -> tuple[bool, dict[str, Any]]:  # pragma: no cover  # lazy-import DI-default delegation
    """Public wrapper around :func:`run_recall_gate` for ``EmbedCliDeps``.

    Named ``default_*`` (not ``_default_*``) so tests can import it
    through the public surface (F5).
    """
    return run_recall_gate()


def default_get_db_path() -> Path:  # pragma: no cover  # lazy-import DI-default delegation
    """Public wrapper around :func:`kairix.core.db.get_db_path`."""
    return get_db_path()


def default_open_db(path: Path) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    """Public wrapper around :func:`kairix.core.db.open_db`."""
    return open_db(path)


def default_get_pending_chunks(db: Any) -> list[Any]:  # pragma: no cover  # lazy-import DI-default delegation
    """Lazy-import wrapper around ``kairix.core.embed.schema.get_pending_chunks``."""
    from .schema import get_pending_chunks

    return get_pending_chunks(db)


def default_check_fts_available(db: Any) -> Any:  # pragma: no cover  # lazy-import DI-default delegation
    """Lazy-import wrapper around ``kairix.core.db.fts.check_fts_available``."""
    from kairix.core.db.fts import check_fts_available

    return check_fts_available(db)


def default_rebuild_fts(db: Any) -> int:  # pragma: no cover  # lazy-import DI-default delegation
    """Lazy-import wrapper around ``kairix.core.db.fts.rebuild_fts``."""
    from kairix.core.db.fts import rebuild_fts

    return rebuild_fts(db)


def default_document_root() -> Path:  # pragma: no cover  # lazy-import DI-default delegation
    """Lazy-import wrapper around :func:`kairix.paths.document_root`."""
    from kairix.paths import document_root

    return document_root()


def default_summaries_db_path() -> Path:  # pragma: no cover  # lazy-import DI-default delegation
    """Lazy-import wrapper around :func:`kairix.paths.summaries_db_path`."""
    from kairix.paths import summaries_db_path

    return summaries_db_path()


def default_init_summaries_db(db: sqlite3.Connection) -> None:  # pragma: no cover  # lazy-import DI-default delegation
    """Lazy-import wrapper around ``kairix.knowledge.summaries.staleness.init_summaries_db``."""
    from kairix.knowledge.summaries.staleness import init_summaries_db

    init_summaries_db(db)


def default_get_stale_paths(
    all_docs: list[str], db: sqlite3.Connection
) -> list[str]:  # pragma: no cover  # lazy-import DI-default delegation
    """Lazy-import wrapper around ``kairix.knowledge.summaries.staleness.get_stale_paths``."""
    from kairix.knowledge.summaries.staleness import get_stale_paths

    return get_stale_paths(all_docs, db)


def default_write_summary(
    result: Any, db: sqlite3.Connection
) -> None:  # pragma: no cover  # lazy-import DI-default delegation
    """Lazy-import wrapper around ``kairix.knowledge.summaries.staleness.write_summary``."""
    from kairix.knowledge.summaries.staleness import write_summary

    write_summary(result, db)


def default_generate_summaries(**kw: Any) -> list[Any]:  # pragma: no cover  # lazy-import DI-default delegation
    """Lazy-import wrapper around ``kairix.knowledge.summaries.generate.generate_summaries``."""
    from kairix.knowledge.summaries.generate import generate_summaries

    return generate_summaries(**kw)


@dataclass(frozen=True)
class EmbedCliDeps:
    """Injection seam for the embed CLI.

    Production callers leave this as the default — every helper is
    wired by ``default_factory`` to the canonical production function
    (lazy-imported on first call). Tests construct
    ``EmbedCliDeps(get_db_path_fn=lambda: tmp_path, ...)`` and pass
    ``deps=`` to ``main`` / ``cmd_*`` to drive the CLI without
    monkey-patching kairix internals.
    """

    # cmd_embed
    pipeline_runner_factory: Callable[[], Callable[..., Any]] = field(default_factory=lambda: _default_pipeline_runner)
    post_embed_summarise: Callable[[], None] = field(default_factory=lambda: run_post_embed_summarise)
    # cmd_recall
    run_recall_gate_fn: Callable[[], tuple[bool, dict[str, Any]]] = field(
        default_factory=lambda: default_run_recall_gate
    )
    # cmd_status + cmd_rebuild_fts share DB seams
    get_db_path_fn: Callable[[], Path] = field(default_factory=lambda: default_get_db_path)
    open_db_fn: Callable[[Path], Any] = field(default_factory=lambda: default_open_db)
    get_pending_chunks_fn: Callable[[Any], list[Any]] = field(default_factory=lambda: default_get_pending_chunks)
    # cmd_rebuild_fts
    check_fts_available_fn: Callable[[Any], Any] = field(default_factory=lambda: default_check_fts_available)
    rebuild_fts_fn: Callable[[Any], int] = field(default_factory=lambda: default_rebuild_fts)
    # run_post_embed_summarise sub-helpers
    document_root_fn: Callable[[], Path] = field(default_factory=lambda: default_document_root)
    summaries_db_path_fn: Callable[[], Path] = field(default_factory=lambda: default_summaries_db_path)
    init_summaries_db_fn: Callable[[sqlite3.Connection], None] = field(
        default_factory=lambda: default_init_summaries_db
    )
    get_stale_paths_fn: Callable[[list[str], sqlite3.Connection], list[str]] = field(
        default_factory=lambda: default_get_stale_paths
    )
    write_summary_fn: Callable[[Any, sqlite3.Connection], None] = field(default_factory=lambda: default_write_summary)
    generate_summaries_fn: Callable[..., list[Any]] = field(default_factory=lambda: default_generate_summaries)


LOG_FILE = Path(
    os.environ.get(
        "KAIRIX_EMBED_LOG",
        str(Path.home() / ".cache" / "kairix" / "logs" / "embed.log"),
    )
)


def _default_lockfile() -> Path:
    """Lockfile in user cache dir — avoids world-writable /tmp on multi-user systems."""
    from kairix.core.db import get_db_path

    return get_db_path().parent / "embed.lock"


LOCKFILE = _default_lockfile()
LOCK_WAIT_SECS = 60


def setup_logging(verbose: bool = False, *, log_file: Path | None = None) -> None:
    """Configure logging for the embed CLI.

    ``log_file`` is a test seam — when supplied, the file handler writes
    there instead of the module-level :data:`LOG_FILE`. Production
    callers leave it ``None``.
    """
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    log_path = log_file if log_file is not None else LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers.append(logging.FileHandler(log_path))

    logging.basicConfig(level=level, format=fmt, handlers=handlers)


def acquire_lock(*, lockfile: Path | None = None, wait_secs: float | None = None) -> IO[str]:
    """
    Acquire exclusive lock using the same lockfile as kairix-maintenance.sh.

    Waits up to ``LOCK_WAIT_SECS`` retrying ``LOCK_EX | LOCK_NB``. The kernel
    releases ``flock`` automatically when the holding process exits (clean or
    crash), so a "stale lockfile" is self-healing — the next worker simply
    succeeds at LOCK_NB once the holder is gone. No PID inspection needed.

    Exits with code 3 if the wait window expires while the holder is still
    actively running.

    ``lockfile`` and ``wait_secs`` are test seams — when supplied, they
    override the module-level :data:`LOCKFILE` / :data:`LOCK_WAIT_SECS`
    so tests can pin a tmp lock path + short wait without reassigning
    module constants. Production callers leave both ``None``.
    """
    lock_path = lockfile if lockfile is not None else LOCKFILE
    timeout = wait_secs if wait_secs is not None else LOCK_WAIT_SECS

    lock_fh = open(lock_path, "w")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_fh.write(str(os.getpid()))
            lock_fh.flush()
            return lock_fh
        except BlockingIOError:
            logging.info("Waiting for embed lock...")
            time.sleep(5)

    # Wait window exhausted and we still couldn't acquire — the holder is
    # genuinely alive and working. Exit cleanly.
    lock_fh.close()
    logging.error(
        "Could not acquire lock after %ds — another embed is still running. "
        "fix: another embed (usually the background worker) holds the lock. "
        "next: kairix worker status shows the active phase; re-run when idle or pause first. "
        "run: kairix worker pause && kairix embed && kairix worker resume",
        timeout,
    )
    sys.exit(3)


def release_lock(lock_fh: IO[str], *, lockfile: Path | None = None) -> None:
    """Release a lock acquired by :func:`acquire_lock`.

    ``lockfile`` mirrors the :func:`acquire_lock` seam — tests can pin
    the same tmp lock path so the unlink targets the test's tmpfile,
    not the production lockfile.
    """
    lock_path = lockfile if lockfile is not None else LOCKFILE
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()
        lock_path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def _run_embed_pipeline(
    args: argparse.Namespace,
    deps: EmbedCliDeps,
) -> Any:
    """Invoke the use case via deps; return the result or raise."""
    runner = deps.pipeline_runner_factory()
    return runner(
        force=args.force,
        batch_size=args.batch_size,
        limit=args.limit,
        skip_recall_check=args.skip_recall_check,
        rebuild_canaries=getattr(args, "rebuild_canaries", False),
        parallel=getattr(args, "parallel", DEFAULT_PARALLEL_BATCHES),
        force_rebuild_cache=getattr(args, "force_rebuild_cache", False),
    )


def _log_recall_outcome(args: argparse.Namespace, result: Any) -> None:
    """Emit the recall-gate summary lines.

    Kept out of ``cmd_embed`` to keep the dispatcher's cognitive
    complexity under F16's ceiling.
    """
    if args.skip_recall_check:
        logging.info("Skipping recall check (--skip-recall-check)")
        return
    if result.recall_score is None:
        return
    logging.info(
        "Recall: %.0f%% (gate %s)",
        result.recall_score * 100,
        "passed" if result.recall_passed else "FAILED",
    )
    if result.recall_passed is False:
        logging.error("Recall gate FAILED — search quality degraded. Check logs.")


def _invoke_post_summarise(deps: EmbedCliDeps) -> None:
    """Run the post-embed summarise step.

    When ``post_embed_summarise`` is the production helper we re-pass
    the outer Deps so its sub-helpers inject from the same source.
    Custom callables (tests pinning a counter, e.g.) are invoked as-is.
    """
    post = deps.post_embed_summarise
    if post is run_post_embed_summarise:
        run_post_embed_summarise(deps=deps)
    else:
        post()


def cmd_embed(args: argparse.Namespace, *, deps: EmbedCliDeps | None = None) -> int:
    """Run the embedding pipeline.

    Thin shim over ``run_incremental_embed_pipeline``. The use case
    encapsulates schema/scan/embed/gate; this function only maps its
    structured result to a process exit code so other CLI semantics
    (e.g. logging behaviour) stay testable in isolation.

    Exit codes:
      0  — embed succeeded and recall gate passed (or was skipped)
      1  — embed had failed chunks OR recall gate fired an alert
      2  — pipeline raised (DB unreachable, schema migration, etc.)
    """
    deps = deps or EmbedCliDeps()

    try:
        result = _run_embed_pipeline(args, deps)
    except ValueError as exc:
        # --parallel out of range surfaces here as an F21-shaped affordance
        # from run_embed. Print to stderr so operators see the rationale +
        # runbook pointer without scrolling through the exception traceback.
        logging.error("%s", exc)
        return 2
    except Exception:
        logging.exception("Embed failed")
        return 2

    logging.info(
        f"Done — embedded={result.embedded} failed={result.failed} "
        f"duration={result.duration_s}s cost=${result.cost_usd:.4f}"
    )
    if result.failed > 0:
        logging.warning(f"{result.failed} chunks failed. Re-run without --force to retry failed chunks.")

    _log_recall_outcome(args, result)

    # Post-embed summarise — non-critical; failures only logged.
    if not args.skip_summarise:
        _invoke_post_summarise(deps)

    if not result.success or result.recall_passed is False:
        return 1
    return 0


def run_post_embed_summarise(*, deps: EmbedCliDeps | None = None) -> None:
    """Generate L0 summaries for documents that don't have them yet.

    Non-critical: failures are logged but don't block the embed return code.

    ``deps`` is the F1-clean injection seam — every collaborator
    (document_root, summaries_db_path, staleness helpers, generate_summaries)
    is reached through ``EmbedCliDeps`` so tests can construct a
    ``EmbedCliDeps(document_root_fn=..., ...)`` with stand-ins. Production
    callers leave it ``None`` and the dataclass' ``default_factory`` slots
    wire the real implementations.
    """
    d = deps if deps is not None else EmbedCliDeps()
    try:
        droot = d.document_root_fn()
        all_docs = [str(p) for p in droot.rglob("*.md") if p.is_file()]
        if not all_docs:
            return

        # F77-allow: summaries DB (separate file from worker DB); CLI-only writer.
        db = sqlite3.connect(str(d.summaries_db_path_fn()))
        d.init_summaries_db_fn(db)

        stale = d.get_stale_paths_fn(all_docs, db)
        if not stale:
            logging.info("Summarise: all %d docs have current summaries", len(all_docs))
            db.close()
            return

        # Cap at 100 docs per embed run to limit API cost
        batch = stale[:100]
        logging.info(
            "Summarise: generating L0 for %d of %d stale docs (capped at 100)",
            len(batch),
            len(stale),
        )

        results = d.generate_summaries_fn(paths=batch, api_key="", endpoint="", deployment="gpt-4o-mini")
        for r in results:
            d.write_summary_fn(r, db)

        logging.info("Summarise: %d L0 summaries generated", len(results))
        db.close()

    except Exception:
        logging.warning("Post-embed summarise failed (non-critical)", exc_info=True)


def cmd_recall(_args: argparse.Namespace, *, deps: EmbedCliDeps | None = None) -> int:
    """Run the recall check standalone."""
    d = deps if deps is not None else EmbedCliDeps()
    passed, result = d.run_recall_gate_fn()
    print(f"Recall: {result['passed']}/{result['total']} ({result['score']:.0%})")
    for det in result["detail"]:
        status = "✓" if det["hit"] else "✗"
        print(f"  {status} [{det['id']}] {det['query'][:60]}")
    return 0 if passed else 1


def _print_last_run_line() -> None:
    """Read ~/.cache/kairix/azure-embed-runs.json and print the last entry.

    Broken-out helper so ``cmd_status`` stays under F16's cognitive
    complexity ceiling. Best-effort: missing or corrupt log is silently
    ignored — logging isn't yet initialised when status runs.
    """
    log_path = Path.home() / ".cache" / "kairix" / "azure-embed-runs.json"
    if not log_path.exists():
        return
    import json

    try:
        runs = json.loads(log_path.read_text())
        if not runs:
            return
        last = runs[-1]
        import datetime

        ts = datetime.datetime.fromtimestamp(last.get("timestamp", 0))
        print(
            f"Last run:  {ts.strftime('%Y-%m-%d %H:%M')} — "
            f"embedded={last.get('embedded')} cost=${last.get('estimated_cost_usd'):.4f}"
        )
    except Exception:  # nosec B110 — NOSONAR S110 — status display failure is non-critical, logging not yet initialised
        pass  # non-critical: status display failed


def cmd_status(args: argparse.Namespace, *, deps: EmbedCliDeps | None = None) -> int:
    """Show current embedding status.

    ``--db-path`` is the F30 subprocess seam — when supplied, status
    reads from that DB path instead of resolving the platform default.
    Matches the ``--document-root`` convention used by other CLIs
    (``kairix store crawl``, ``kairix bootstrap``) so subprocess
    outcome tests can drive a tmp index without touching the process
    environment (F2-clean).
    """
    d = deps if deps is not None else EmbedCliDeps()
    db_path = getattr(args, "db_path", None) or d.get_db_path_fn()
    db = d.open_db_fn(Path(db_path))
    try:
        pending = d.get_pending_chunks_fn(db)
        total_vecs = db.execute("SELECT COUNT(*) FROM content_vectors").fetchone()[0]
        total_docs = db.execute("SELECT COUNT(*) FROM documents WHERE active=1").fetchone()[0]

        print(f"Kairix index: {db_path}")
        print(f"Documents: {total_docs}")
        print(f"Vectors:   {total_vecs}")
        print(f"Pending:   {len(pending)} documents need embedding")

        _print_last_run_line()
    finally:
        db.close()
    return 0


def cmd_rebuild_fts(_args: argparse.Namespace, *, deps: EmbedCliDeps | None = None) -> int:
    """Rebuild the documents_fts BM25 index in isolation. Self-heal for #223.

    Reads from the same documents + content tables that the embed pipeline
    populates — does NOT touch the embed pipeline, vector index, or
    recall canaries. Cheap (~30s on a 50k-doc corpus); use after the
    BM25 leg silently went offline.

    The ``_args`` parameter is required by the CLI dispatch signature but
    carries no rebuild-fts-specific flags (F19: underscore-prefixed).
    """
    d = deps if deps is not None else EmbedCliDeps()
    db = d.open_db_fn(Path(d.get_db_path_fn()))
    try:
        before = d.check_fts_available_fn(db)
        print(f"FTS state before rebuild: available={before.available} reason={before.reason} rows={before.row_count}")

        count = d.rebuild_fts_fn(db)

        after = d.check_fts_available_fn(db)
        print(f"FTS state after rebuild:  available={after.available} reason={after.reason} rows={after.row_count}")
        print(f"Rebuilt: {count} documents indexed")
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None, *, deps: EmbedCliDeps | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="kairix embed",
        description="Embed documents into the kairix vector index",
    )
    parser.add_argument("--verbose", "-v", action=_STORE_TRUE)
    sub = parser.add_subparsers(dest="command")

    # embed (default)
    embed_p = sub.add_parser("embed", help="Run embedding pipeline (default)")
    embed_p.add_argument(
        "--force",
        action=_STORE_TRUE,
        help="Re-embed all chunks (clears existing vectors)",
    )
    embed_p.add_argument("--limit", type=int, default=None, help="Cap total chunks (for validation)")
    embed_p.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Chunks per Azure API call",
    )
    embed_p.add_argument(
        "--parallel",
        type=int,
        default=DEFAULT_PARALLEL_BATCHES,
        help=(
            f"Run up to N batches concurrently (1..{MAX_PARALLEL_BATCHES}, default 1 = serial). "
            "Speeds up catch-up cycles 3-5x at the same Azure API cost. "
            "Recommended N=3 for the default VM size; see "
            "docs/operations/runbooks/worker-memory-and-swap.md for sizing per corpus."
        ),
    )
    embed_p.add_argument("--skip-recall-check", action=_STORE_TRUE, help="Skip post-embed quality gate")
    embed_p.add_argument(
        "--rebuild-canaries",
        action=_STORE_TRUE,
        help=(
            "Discard the persisted recall canary suite and sample fresh from "
            "the corpus. Use after a major index rebuild."
        ),
    )
    embed_p.add_argument(
        "--force-rebuild-cache",
        action=_STORE_TRUE,
        help=(
            "Discard the persistent embedding_cache.sqlite before the run. "
            "Use only when the cache itself is suspected wrong (e.g. corrupted "
            "rows). Rare — --force alone rebuilds the vec index from the cache "
            "for $0 when the cache is intact."
        ),
    )
    embed_p.add_argument(
        "--skip-summarise",
        action=_STORE_TRUE,
        help="Skip post-embed L0 summary generation",
    )

    # recall-check
    sub.add_parser("recall-check", help="Run recall quality check standalone")

    # status
    status_p = sub.add_parser("status", help="Show embedding status")
    status_p.add_argument(
        "--db-path",
        default=None,
        help=(
            "Read status from this SQLite index instead of the default "
            "resolution chain (KAIRIX_DB_PATH env / kairix.config.yaml / "
            "platform default). F30 subprocess seam — keeps tmp-DB "
            "injection out of monkeypatch.setenv (F2-clean)."
        ),
    )

    # rebuild-fts — self-heal entry for #223
    sub.add_parser(
        "rebuild-fts",
        help="Rebuild the documents_fts BM25 index from scratch (self-heal for the BM25 leg).",
    )

    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if args.command is None or args.command == "embed":
        if not hasattr(args, "force"):
            # Default subcommand
            args.force = False
            args.limit = None
            args.batch_size = DEFAULT_BATCH_SIZE
            args.parallel = DEFAULT_PARALLEL_BATCHES
            args.skip_recall_check = False
            args.rebuild_canaries = False
            args.force_rebuild_cache = False
            args.skip_summarise = False
        sys.exit(cmd_embed(args, deps=deps))
    elif args.command == "recall-check":
        sys.exit(cmd_recall(args, deps=deps))
    elif args.command == "status":
        sys.exit(cmd_status(args, deps=deps))
    elif args.command == "rebuild-fts":
        sys.exit(cmd_rebuild_fts(args, deps=deps))


if __name__ == "__main__":
    main()
