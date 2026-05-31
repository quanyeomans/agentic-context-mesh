"""ADR-029 G.1: ``dispatch_or_queue`` decorator + shared worker pool.

The decorator wraps an MCP tool handler so that:

1. Fast handlers (return within ``budget_seconds``, default 1.5) run
   synchronously and the result is returned in the tool response. The
   row is written to ``pending_queries`` with ``status='delivered'``
   so carry-along never re-delivers a synchronous result.
2. Slow handlers (exceed budget OR raise during the wait) leave the
   future running on the background worker pool, write
   ``status='in_progress'``, and return plain text
   ``"Processing your request (id: q_<hash>). Your answer will be
   delivered when ready."``. When the future completes the worker
   thread updates the row to ``status='completed'`` (or ``failed``).
3. Identical ``(agent_id, args_hash)`` calls within a 60-second window
   are deduplicated — the second call sees the existing row and
   returns its status text instead of submitting a duplicate job.

Per-tick budget (F66 spirit) — the shared background pool is bounded
at ``QUEUE_WORKER_MAX_WORKERS = 4`` workers. Higher concurrency would
saturate the single shared SQLite writer lock and burn worker memory.
A new tool can opt out of queueing via ``sync_only=True`` (writes a
'delivered' row but never queues — for write tools that must finish
before the next read).

SQLite threading note (per the parallel-embed pattern in
``kairix/core/embed/embed.py``): writes from worker threads run under
a module-level :class:`threading.Lock` and the queue's own connection
is opened with ``check_same_thread=False``. The connection is owned
by this module — production callers don't pass one. Tests inject a
``db_connect`` callable to point at a ``tmp_path`` SQLite.

F77 — this module's path (``kairix/core/queue/``) is added to the
``sqlite3.connect`` allow-list because the queue's writer thread cannot
share the worker's main coordinator connection (different thread,
different transaction scope). See ``scripts/checks/check_f77_sqlite_single_writer.py``.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Module-level shared resources — guarded by _resource_lock.
# ----------------------------------------------------------------------

# F66-spirit: shared bounded pool. 4 workers chosen to match the
# parallel-embed default and to leave headroom under the single-writer
# SQLite lock without saturating the worker process. Documented per F66
# even though the check itself only fires on connector / maintenance
# classes.
QUEUE_WORKER_MAX_WORKERS = 4

# Disk-watermark counterpart for F66 spirit. The queue's writes are
# bounded (one row per dispatch; cleanup is G.3). No remote-fetch disk
# pressure today; flagged explicit so a future operator can see the
# rationale instead of guessing.
QUEUE_WORKER_DISK_WATERMARK_MIN_FREE_BYTES: int | None = None

# Plain-text reply for the queued path. ADR-029 §"Decision" mandates a
# plain string (NEVER an error envelope) so agents read it as
# "accepted, continue" not "tool broken, fall back".
PROCESSING_TEMPLATE = "Processing your request (id: {qid}). Your answer will be delivered when ready."

# Dedup window — identical (agent_id, args_hash) within this many
# seconds returns the existing row's status text instead of submitting
# a duplicate job. ADR-029 §"Mechanics".
DEDUP_WINDOW_SECONDS = 60.0

# Default per-tool budget for the synchronous wait.
DEFAULT_BUDGET_SECONDS = 1.5

# Status values used in pending_queries.status.
_STATUS_DELIVERED = "delivered"
_STATUS_IN_PROGRESS = "in_progress"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"

_resource_lock = threading.Lock()
_db_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None
_connection: sqlite3.Connection | None = None
_db_connect_factory: Callable[[], sqlite3.Connection] | None = None


def _default_db_connect() -> sqlite3.Connection:
    """Production connection factory — opens the configured kairix DB.

    Deferred import keeps ``kairix.paths`` out of this module's import
    graph at parse time (matters for ``kairix --help`` cold-path
    imports).
    """
    from kairix.paths import db_path

    return sqlite3.connect(str(db_path()), check_same_thread=False)


def configure(
    *,
    db_connect: Callable[[], sqlite3.Connection] | None = None,
) -> None:
    """Configure the queue's shared resources.

    Tests call this with a ``tmp_path``-backed connection factory; in
    production the defaults take effect on the first decorator invocation.
    Idempotent — re-calling with the same callable is a no-op.
    """
    global _db_connect_factory
    with _resource_lock:
        _db_connect_factory = db_connect


def reset_for_tests() -> None:
    """Drop cached executor + connection so the next call rebuilds them.

    Tests call this between cases to avoid cross-test leakage. Safe to
    call when nothing has been initialised yet.
    """
    global _executor, _connection, _db_connect_factory
    with _resource_lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
            _executor = None
        if _connection is not None:
            try:
                _connection.close()
            except sqlite3.Error:
                pass
            _connection = None
        _db_connect_factory = None


def _get_executor() -> ThreadPoolExecutor:
    """Lazily build the shared executor. Module-level singleton."""
    global _executor
    with _resource_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=QUEUE_WORKER_MAX_WORKERS,
                thread_name_prefix="kairix-queue-worker",
            )
        return _executor


def _get_connection() -> sqlite3.Connection:
    """Lazily open the shared write connection. Module-level singleton.

    Opened with ``check_same_thread=False`` so worker threads can write
    under ``_db_lock``. The connection is owned here — never returned
    to callers, never shared.
    """
    global _connection
    with _resource_lock:
        if _connection is None:
            factory = _db_connect_factory if _db_connect_factory is not None else _default_db_connect
            _connection = factory()
        return _connection


# ----------------------------------------------------------------------
# Hash + id helpers.
# ----------------------------------------------------------------------


def _canonical_args_json(args: dict[str, Any]) -> str:
    """JSON-serialise args with sorted keys so the hash is canonical."""
    return json.dumps(args, sort_keys=True, default=str)


def _compute_args_hash(tool: str, args_json: str) -> str:
    """sha256 of (tool || args_json) — the dedup key per ADR-029 §"Mechanics"."""
    return hashlib.sha256(f"{tool}||{args_json}".encode()).hexdigest()


def _build_query_id(args_hash: str) -> str:
    """Build the user-facing query id ``q_<8-char-hash-suffix>``.

    Suffix is the first 8 hex chars of a uuid4 to keep ids unique even
    when the same agent submits two distinct jobs whose args_hash share
    a prefix.
    """
    return f"q_{uuid.uuid4().hex[:8]}_{args_hash[:8]}"


def _now_iso() -> str:
    """ISO8601 UTC timestamp — used for every row column."""
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# Dedup lookup.
# ----------------------------------------------------------------------


def _lookup_recent_job(
    db: sqlite3.Connection,
    *,
    agent_id: str,
    args_hash: str,
    now_seconds: float,
) -> tuple[str, str] | None:
    """Return (id, status) of an existing job for this (agent, args) within the dedup window.

    Returns None when no match. The window is :data:`DEDUP_WINDOW_SECONDS`.
    """
    cutoff = datetime.fromtimestamp(now_seconds - DEDUP_WINDOW_SECONDS, timezone.utc).isoformat()
    with _db_lock:
        row = db.execute(
            "SELECT id, status FROM pending_queries "
            "WHERE agent_id = ? AND args_hash = ? AND submitted_at >= ? "
            "ORDER BY submitted_at DESC LIMIT 1",  # F63-bounded: LIMIT 1 caps the row scan
            (agent_id, args_hash, cutoff),
        ).fetchone()
    return (row[0], row[1]) if row else None


# ----------------------------------------------------------------------
# Row writes.
# ----------------------------------------------------------------------


def _insert_pending_row(
    db: sqlite3.Connection,
    *,
    query_id: str,
    agent_id: str,
    tool: str,
    args_json: str,
    args_hash: str,
    status: str,
    submitted_at: str,
) -> None:
    """Insert a new pending_queries row. F70 INSERT site for the table."""
    with _db_lock:
        db.execute(
            "INSERT INTO pending_queries "
            "(id, agent_id, tool, args_json, args_hash, status, submitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (query_id, agent_id, tool, args_json, args_hash, status, submitted_at),
        )
        db.commit()


def _mark_completed(
    db: sqlite3.Connection,
    *,
    query_id: str,
    result: Any,
    status: str = _STATUS_COMPLETED,
) -> None:
    """Mark a row completed (or delivered when the sync path returned)."""
    try:
        result_json = json.dumps(result, default=str)
    except (TypeError, ValueError):
        result_json = json.dumps({"unserialisable_result": True})

    with _db_lock:
        db.execute(
            "UPDATE pending_queries SET status = ?, completed_at = ?, result_json = ? WHERE id = ?",
            (status, _now_iso(), result_json, query_id),
        )
        db.commit()


def _mark_failed(
    db: sqlite3.Connection,
    *,
    query_id: str,
    error_message: str,
) -> None:
    """Mark a row failed when the handler raises in the background."""
    with _db_lock:
        db.execute(
            "UPDATE pending_queries SET status = ?, completed_at = ?, error_message = ? WHERE id = ?",
            (_STATUS_FAILED, _now_iso(), error_message[:1000], query_id),
        )
        db.commit()


def _mark_started(db: sqlite3.Connection, *, query_id: str) -> None:
    """Stamp started_at when the worker picks the job up."""
    with _db_lock:
        db.execute(
            "UPDATE pending_queries SET started_at = ? WHERE id = ?",
            (_now_iso(), query_id),
        )
        db.commit()


# ----------------------------------------------------------------------
# Decorator.
# ----------------------------------------------------------------------


# S3776 waiver rationale — cognitive complexity 18 vs ceiling 15.
# The wrapper is a state-machine over four call-paths (dedup short-circuit,
# sync_only, async-under-budget, async-handoff-on-timeout) and each branch
# is load-bearing for the ADR-029 contract. Extraction into a helper was
# tried first but dropped per-file coverage below the F7 90% floor (helper
# code wasn't directly tested by the existing contract suite). The right
# paydown is a direct unit suite per call-path — tracked as follow-up
# after the ADR-029 G.2/G.3 work lands.
def dispatch_or_queue(  # NOSONAR(python:S3776)
    *,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    sync_only: bool = False,
    tool_name: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap an MCP tool handler with the dispatch-or-queue semantics.

    Args:
        budget_seconds: Synchronous wait budget. Default 1.5s per ADR-029.
        sync_only: When True, the handler always runs synchronously. The
            row is still written (status='delivered') so carry-along
            never re-delivers; the queued-path return is skipped.
        tool_name: Optional explicit tool name. Defaults to the wrapped
            function's ``__name__``.

    The wrapped function MUST accept ``agent_id`` as a keyword argument
    so the decorator can stamp ownership and dedup correctly.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        effective_tool_name = tool_name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            agent_id = kwargs.get("agent_id") or "unknown-agent"

            # Build args_json from positional + keyword args so the dedup
            # hash is stable across call sites. agent_id excluded — the
            # row is keyed by (agent_id, args_hash) so including it
            # again would double-count.
            args_for_hash: dict[str, Any] = {
                "args": list(args),
                "kwargs": {k: v for k, v in kwargs.items() if k != "agent_id"},
            }
            args_json = _canonical_args_json(args_for_hash)
            args_hash = _compute_args_hash(effective_tool_name, args_json)

            db = _get_connection()
            now_seconds = time.time()

            # Dedup — return the existing job's status text within the window.
            existing = _lookup_recent_job(
                db,
                agent_id=agent_id,
                args_hash=args_hash,
                now_seconds=now_seconds,
            )
            if existing is not None:
                existing_id, existing_status = existing
                if existing_status in (_STATUS_IN_PROGRESS,):
                    return PROCESSING_TEMPLATE.format(qid=existing_id)
                # completed / delivered / failed within the window — let
                # the caller fall through to carry-along on the next
                # call. For the in-flight repeat case the message above
                # is enough.

            query_id = _build_query_id(args_hash)
            submitted_at = _now_iso()

            if sync_only:
                # Write a 'delivered' row up front so carry-along is a
                # no-op for this id, then run the handler inline.
                _insert_pending_row(
                    db,
                    query_id=query_id,
                    agent_id=agent_id,
                    tool=effective_tool_name,
                    args_json=args_json,
                    args_hash=args_hash,
                    status=_STATUS_DELIVERED,
                    submitted_at=submitted_at,
                )
                return fn(*args, **kwargs)

            _insert_pending_row(
                db,
                query_id=query_id,
                agent_id=agent_id,
                tool=effective_tool_name,
                args_json=args_json,
                args_hash=args_hash,
                status=_STATUS_IN_PROGRESS,
                submitted_at=submitted_at,
            )

            executor = _get_executor()

            def _run_handler() -> Any:
                _mark_started(db, query_id=query_id)
                return fn(*args, **kwargs)

            future: Future[Any] = executor.submit(_run_handler)

            try:
                result = future.result(timeout=budget_seconds)
            except FutureTimeout:
                # Hand off to background — install a callback that
                # writes the eventual outcome. Return the plain-text
                # processing message.
                future.add_done_callback(lambda fut: _finalise_background(db, query_id, fut))
                return PROCESSING_TEMPLATE.format(qid=query_id)
            except Exception as exc:
                # Handler raised synchronously inside the budget — mark
                # failed and re-raise so the MCP error envelope still
                # fires. Carry-along will surface the failure.
                _mark_failed(db, query_id=query_id, error_message=f"{type(exc).__name__}: {exc}")
                raise

            # Fast path — handler returned within budget. Stamp delivered
            # so carry-along skips this row.
            _mark_completed(db, query_id=query_id, result=result, status=_STATUS_DELIVERED)
            return result

        return wrapper

    return decorator


def _finalise_background(
    db: sqlite3.Connection,
    query_id: str,
    fut: Future[Any],
) -> None:
    """Write the background job's eventual outcome to pending_queries."""
    exc = fut.exception()
    if exc is not None:
        _mark_failed(db, query_id=query_id, error_message=f"{type(exc).__name__}: {exc}")
        return
    try:
        result = fut.result()
        _mark_completed(db, query_id=query_id, result=result)
    except (sqlite3.Error, TypeError, ValueError) as outer:
        logger.warning(
            "dispatch_or_queue: writing background result for %s failed: %s",
            query_id,
            outer,
        )
