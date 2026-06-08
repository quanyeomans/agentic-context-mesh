"""Error-envelope + async-offload wrappers for MCP tool handlers.

FastMCP's generic error mapper converts any exception escaping a tool
handler into a JSON-RPC ``-32602 Invalid request parameters`` response,
which masks the real cause. The 2026-05-02 dogfood report observed every
``mcp-kairix__*`` tool returning -32602 even though the underlying
errors were diverse (Neo4j unavailable, LLM rate-limited, transport
closed) — none of them parameter-validation failures.

This module exposes two callables:

  - ``wrap_tool_errors`` — sync handler → sync handler with error
    envelope. Catches every exception, logs it with traceback, and
    returns a structured ``{"error": "<class>: <message>"}`` dict —
    bypassing FastMCP's ``-32602`` mapper because the handler now
    returns successfully with an error payload.

  - ``async_tool_handler`` — sync handler → async handler that offloads
    the sync work to a kairix-owned ``ThreadPoolExecutor`` via
    ``loop.run_in_executor``. The error envelope is applied inside the
    threaded call. Concurrent ``/mcp`` requests no longer queue behind
    each other on the event loop — resolves #177. The dedicated pool
    (default 32 workers, env override ``KAIRIX_MCP_DISPATCH_WORKERS``)
    replaces the event loop's default executor, whose CPython 3.12 size
    of ``min(32, cpu_count + 4)`` shrank to six threads on the 2-CPU
    production container and serialised the dogfood agents — root
    cause of #403.

``async_tool_handler`` also records one row per call into the
``mcp_call_log`` SQLite table (issue #398 Workstream D). The write is
fire-and-forget — DB failures NEVER break the tool call. Operators
query the table via ``kairix mcp-calls``.

Tested through public surface only: register a handler that raises,
call it, observe the dict.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., dict[str, Any]])


# Resolver callable shape used by AsyncToolHandlerDeps.
DbPathFn = Callable[[], Path]
# Factory callable shape for the dispatch + observability executors.
ExecutorFn = Callable[[], ThreadPoolExecutor]


# ---------------------------------------------------------------------------
# Dispatch + observability executors (#403)
# ---------------------------------------------------------------------------
#
# Production trace 2026-06-04 showed MCP search latency 36s vs CLI 12s on the
# same warm pipeline. Two compounding causes:
#
# 1. ``asyncio.to_thread`` uses the running event loop's default executor,
#    which on CPython 3.12 is sized ``min(32, cpu_count + 4)`` — on a 2-CPU
#    production container that is six worker threads. Six concurrent dogfood
#    agents firing search + entity + brief calls saturate the pool, and the
#    seventh call queues behind whichever slot frees first. Observed in
#    /tmp/instrument_mcp_concurrent.py — 50 concurrent calls used only 14
#    threads and serialised into four waves on a 10-CPU dev box.
#
# 2. ``_record_mcp_call`` runs synchronously in the wrapper's ``finally``
#    block. ``finally`` runs back on the event loop thread (confirmed via
#    /tmp/instrument_mcp_finally.py — db_path_fn was called from the loop
#    tid on every call). The dedicated observability SQLite write is short
#    (~1-5 ms) but compounds across N concurrent tool returns on the same
#    loop and adds head-of-line blocking to other in-flight async work.
#
# Fix: own the dispatch threadpool with an explicit ``max_workers`` and
# route the observability writes onto a separate single-thread queue so
# the event loop never blocks on SQLite I/O.
#
# Pool sizing rationale: 32 dispatch workers absorbs steady-state load from
# the documented six dogfood agents PLUS the diagnostics calls (entity,
# bootstrap, prep, features) that fire during a single agent turn, with
# headroom for the brief tool's iterative-retrieval inner loop (max_turns=4
# nested searches). The previous ``min(32, cpu+4)`` cap meant a 2-CPU
# container ran with six workers — the dogfood agent count alone consumed
# the pool.
DEFAULT_DISPATCH_WORKERS = 32
DEFAULT_OBS_WORKERS = 1

_DISPATCH_EXECUTOR: ThreadPoolExecutor | None = None
_OBS_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()


def _resolve_dispatch_workers() -> int:
    """Resolve the dispatch pool size, with env-var override.

    F4-clean: env reads route through :mod:`kairix.paths`. Operators tune
    ``KAIRIX_MCP_DISPATCH_WORKERS`` if the default 32 is too small (large
    deployments with many concurrent agents) or too big (single-agent
    dev containers with limited memory).
    """
    from kairix.paths import read_int_env

    return read_int_env("KAIRIX_MCP_DISPATCH_WORKERS", default=DEFAULT_DISPATCH_WORKERS)


def _default_dispatch_executor() -> ThreadPoolExecutor:
    """Return the module-level dispatch pool, building it lazily.

    Process-lifetime singleton — built on first MCP tool call and reused
    for every subsequent dispatch. Threads are named ``mcp-dispatch-*``
    so operators can identify the pool in ``py-spy dump`` / ``faulthandler``
    output during a slow-tool-call investigation.
    """
    global _DISPATCH_EXECUTOR
    with _EXECUTOR_LOCK:
        if _DISPATCH_EXECUTOR is None:
            _DISPATCH_EXECUTOR = ThreadPoolExecutor(
                max_workers=_resolve_dispatch_workers(),
                thread_name_prefix="mcp-dispatch",
            )
        return _DISPATCH_EXECUTOR


def _default_obs_executor() -> ThreadPoolExecutor:
    """Return the module-level observability pool, building it lazily.

    Single-worker by design: ``mcp_call_log`` INSERTs are serial against
    one SQLite file anyway, so adding more workers buys nothing and
    spreads the connection cache. Threads named ``mcp-obs-*``.
    """
    global _OBS_EXECUTOR
    with _EXECUTOR_LOCK:
        if _OBS_EXECUTOR is None:
            _OBS_EXECUTOR = ThreadPoolExecutor(
                max_workers=DEFAULT_OBS_WORKERS,
                thread_name_prefix="mcp-obs",
            )
        return _OBS_EXECUTOR


def reset_executors() -> None:
    """Tear down the module-level executors. Tests use this between cases.

    Production callers never call this — the executors are process-lifetime
    singletons. Tests that swap in a fake :class:`AsyncToolHandlerDeps`
    don't need to call it either; only tests that exercise the default
    factories AND need a clean pool between runs.
    """
    global _DISPATCH_EXECUTOR, _OBS_EXECUTOR
    with _EXECUTOR_LOCK:
        if _DISPATCH_EXECUTOR is not None:
            _DISPATCH_EXECUTOR.shutdown(wait=False)
            _DISPATCH_EXECUTOR = None
        if _OBS_EXECUTOR is not None:
            _OBS_EXECUTOR.shutdown(wait=False)
            _OBS_EXECUTOR = None


def default_db_path() -> Path:
    """Production resolver — points at a DEDICATED observability SQLite file.

    Sibling to the main index DB but a separate file so per-MCP-call
    INSERT writes don't compete for the main DB's write lock against the
    worker's embed pipeline / neo4j drain / FTS rebuild. Production
    traces (2026-06-03 v2026.6.4a1 deploy) showed ~95% of mcp_call_log
    INSERTs lost the lock race and were silently dropped — moving the
    table to its own file eliminates the contention.

    Module-level so :class:`AsyncToolHandlerDeps`' default_factory can
    reference it. Tests construct a different ``AsyncToolHandlerDeps``
    instance with their own ``db_path_fn`` closure.
    """
    from kairix.paths import db_path as _db_path

    return _db_path().parent / "mcp_observability.sqlite"


@dataclass(frozen=True)
class AsyncToolHandlerDeps:
    """Injectable dependencies for :func:`async_tool_handler`.

    Mirrors :class:`kairix.worker.WorkerDeps` (the canonical Deps
    pattern): every field is non-Optional with a ``default_factory``
    that returns the production helper. Production callers leave
    ``deps=None`` and the default factory wires the real helpers.

    Tests construct ``AsyncToolHandlerDeps(db_path_fn=lambda: tmp_db)``
    to route the call-log INSERT to a tmp-path SQLite without going
    through ``kairix.paths.db_path()`` (F2-clean — no env-var monkey-
    patching of ``KAIRIX_DB_PATH``).

    The ``dispatch_executor_fn`` + ``obs_executor_fn`` seams (added for
    #403) let tests substitute small ``ThreadPoolExecutor`` instances
    with deterministic ``max_workers`` so the concurrent-dispatch test
    asserts pool-size behaviour without touching the process-level pool.
    """

    db_path_fn: DbPathFn = field(default_factory=lambda: default_db_path)
    dispatch_executor_fn: ExecutorFn = field(default_factory=lambda: _default_dispatch_executor)
    obs_executor_fn: ExecutorFn = field(default_factory=lambda: _default_obs_executor)


def wrap_tool_errors(handler: _F) -> _F:  # NOSONAR S6796 — PEP 695 needs 3.12+, kairix on 3.10+
    """Wrap an MCP tool handler so escaped exceptions become error dicts.

    The wrapped handler:
      - Returns the original handler's dict on success.
      - On any exception, logs at WARNING with traceback and returns
        ``{"error": "<ExceptionClass>: <message>"}``. The exception
        class name is preserved so observability can group by error type.

    The wrapper preserves the handler's name and docstring via functools.wraps
    so FastMCP's tool-registration machinery sees the original signature.
    """

    @functools.wraps(handler)
    def _wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return handler(*args, **kwargs)
        except Exception as exc:
            logger.warning(
                "mcp tool %s raised %s: %s",
                getattr(handler, "__name__", "<unknown>"),
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return {"error": f"{type(exc).__name__}: {exc}"}

    return _wrapped  # type: ignore[return-value]  # NOSONAR — wraps preserves signature; mypy can't narrow the decorator return type


def _payload_hash(kwargs: dict[str, Any]) -> str:
    """Build a stable short hash of the kwargs for the call log.

    Truncated to 16 hex chars — enough to disambiguate calls in
    practice without storing potentially sensitive payload content.
    The hash is over ``repr(sorted(kwargs.items()))`` so the same
    kwargs in any insertion order produce the same hash.
    """
    return hashlib.sha256(repr(sorted(kwargs.items())).encode()).hexdigest()[:16]


_MCP_CALL_LOG_DDL = """
CREATE TABLE IF NOT EXISTS mcp_call_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    tool            TEXT NOT NULL,
    agent           TEXT,
    latency_ms      INTEGER NOT NULL,
    success         INTEGER NOT NULL,
    error_class     TEXT,
    payload_hash    TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcp_call_log_tool_time
    ON mcp_call_log(tool, timestamp);
CREATE INDEX IF NOT EXISTS idx_mcp_call_log_time
    ON mcp_call_log(timestamp);
"""


def _record_mcp_call(
    *,
    db_path: Path,
    tool: str,
    agent: str | None,
    latency_ms: int,
    success: bool,
    error_class: str | None,
    payload_hash: str,
) -> None:
    """Insert one row into ``mcp_call_log``. Fire-and-forget — never raises.

    DB errors are logged at WARNING and swallowed. The contract is:
    observability MUST NOT break a tool call.

    The connection is opened with ``check_same_thread=False`` because
    ``async_tool_handler`` runs sync handlers in arbitrary worker
    threads from the asyncio threadpool — a per-call connection
    handed off across threads is the simplest correct shape for
    fire-and-forget observability writes. Short-lived: opened, INSERT,
    commit, closed in one function call.

    Production observability runs against a DEDICATED SQLite file
    (``db_path`` resolves via :func:`kairix.paths.data_dir` to
    ``mcp_observability.sqlite`` next to the main index — typically
    ``/var/lib/kairix/`` on FHS containers, ``/data/kairix/`` on legacy
    layouts — not the main index DB itself). This decouples per-call INSERT
    writes from the main DB's write-lock contention (embed pipeline,
    neo4j drain, FTS rebuild) — production traces showed ~95% of
    INSERTs lost the lock race against worker writes and were silently
    dropped. The dedicated file has zero contention. Table + indexes
    are created on first write via ``executescript(_MCP_CALL_LOG_DDL)``
    so no migration is required for fresh deploys.
    """
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        conn = sqlite3.connect(
            str(db_path),
            timeout=5,
            # check_same_thread=False — fire-and-forget log writes run on the
            # asyncio threadpool worker thread, not the thread that opened
            # the connection. The connection is short-lived (one INSERT then
            # close) so the cross-thread access is bounded.
            check_same_thread=False,
        )
        try:
            # Idempotent CREATE — the dedicated observability DB starts
            # empty on first deploy, no migration required.
            conn.executescript(_MCP_CALL_LOG_DDL)
            conn.execute(
                "INSERT INTO mcp_call_log "
                "(timestamp, tool, agent, latency_ms, success, error_class, payload_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    timestamp,
                    tool,
                    agent,
                    latency_ms,
                    1 if success else 0,
                    error_class,
                    payload_hash,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(
            "mcp_call_log INSERT failed (swallowed): tool=%s error=%s: %s",
            tool,
            type(exc).__name__,
            exc,
        )


def _extract_error_class(error_value: object) -> str | None:
    """Return the leading ``<ClassName>`` of a ``{"error": "<Class>: <msg>"}`` payload.

    ``safe = wrap_tool_errors(...)`` always emits the
    ``"<ExceptionClass>: <message>"`` shape on a swallowed handler
    exception. Slicing off the class prefix lets ``mcp_call_log``
    group failures by error type without storing the full message.
    Returns ``None`` for empty / falsy payloads (no error happened).
    """
    if not error_value:
        return None
    err_text = str(error_value)
    return err_text.split(":", 1)[0].strip() or None


def _submit_call_log(
    *,
    resolve_obs_executor: ExecutorFn,
    db_path_value: Path,
    handler_name: str,
    agent: str | None,
    elapsed_ms: int,
    success: bool,
    error_class: str | None,
    payload_hash: str,
) -> None:
    """Fire-and-forget the ``mcp_call_log`` INSERT onto the observability pool.

    Submission failure (executor shutdown, queue overflow) is logged at
    WARNING and swallowed — observability never breaks a tool call. The
    INSERT itself runs inside the obs pool's worker thread; per-row
    SQLite errors are handled inside :func:`_record_mcp_call`.
    """
    try:
        resolve_obs_executor().submit(
            _record_mcp_call,
            db_path=db_path_value,
            tool=handler_name,
            agent=agent,
            latency_ms=elapsed_ms,
            success=success,
            error_class=error_class,
            payload_hash=payload_hash,
        )
    except Exception as exc:
        logger.warning(
            "mcp_call_log: obs executor submit failed (swallowed): tool=%s error=%s: %s",
            handler_name,
            type(exc).__name__,
            exc,
        )


def _emit_call_log(
    *,
    resolve_db_path: DbPathFn,
    resolve_obs_executor: ExecutorFn,
    handler_name: str,
    kwargs: dict[str, Any],
    started: float,
    success: bool,
    error_class: str | None,
    payload_hash: str,
) -> None:
    """Resolve the obs DB path then dispatch the call-log row submission.

    The two steps are split so the outer ``finally`` block in the wrapper
    stays linear: this helper owns BOTH the db-path resolution failure
    branch AND the obs-executor submission branch. Each failure is
    logged + swallowed; observability never breaks the tool call.
    """
    elapsed_ms = int((time.monotonic() - started) * 1000)
    agent_kwarg = kwargs.get("agent")
    agent = str(agent_kwarg) if agent_kwarg else None
    try:
        db_path_value = resolve_db_path()
    except Exception as exc:
        logger.warning(
            "mcp_call_log: db_path_fn failed (swallowed): tool=%s error=%s: %s",
            handler_name,
            type(exc).__name__,
            exc,
        )
        return
    _submit_call_log(
        resolve_obs_executor=resolve_obs_executor,
        db_path_value=db_path_value,
        handler_name=handler_name,
        agent=agent,
        elapsed_ms=elapsed_ms,
        success=success,
        error_class=error_class,
        payload_hash=payload_hash,
    )


def async_tool_handler(
    handler: Callable[..., dict[str, Any]],
    *,
    deps: AsyncToolHandlerDeps | None = None,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Convert a sync MCP tool handler into an async one that offloads.

    The returned coroutine:
      1. Captures the call from FastMCP's tool dispatcher.
      2. Runs the sync handler in the kairix-owned dispatch
         ``ThreadPoolExecutor`` (default 32 workers, tunable via
         ``KAIRIX_MCP_DISPATCH_WORKERS``). Previously used
         ``asyncio.to_thread`` which routes through the event loop's
         default executor (``min(32, cpu_count + 4)``) — that yielded
         six worker threads on a 2-CPU production container and was the
         root cause of #403 (six dogfood agents saturated the pool).
      3. Returns the handler's dict, OR a structured error envelope
         when the handler raises (the ``wrap_tool_errors`` semantics
         are applied inside the thread, before re-entering the loop).
      4. Records one row in ``mcp_call_log`` (issue #398 Workstream D)
         with tool name, agent, latency, success flag, error class,
         and a short payload hash. The write fires onto a dedicated
         single-thread observability executor — fire-and-forget, the
         tool's response path never blocks on the SQLite write (#403).
         Observability failure never breaks the tool call.
      5. Injects ``latency_ms`` (integer ms of wall-clock through the
         wrapper) into the result envelope when the handler hasn't
         already surfaced one (issue #405). Search's use case
         publishes its own ``latency_ms`` float and is left alone via
         ``setdefault``; every other tool's envelope now carries the
         field so agents no longer need external wall-clock measurement.

    Concurrent ``/mcp`` requests are scheduled onto the event loop and
    each tool call's blocking work happens on its own thread, so a
    long-running search no longer blocks subsequent calls.

    Sync work in the handlers is still subject to the GIL — but threads
    interleave correctly when waiting on I/O (HTTP to OpenRouter,
    SQLite reads, Neo4j round-trips), which is most of any tool call's
    elapsed time. CPU-bound stages (BM25 ranking, vector similarity,
    rerank) still serialize per-call but no longer block the event
    loop. See #177.

    Args:
        handler: The sync tool handler to wrap.
        deps:    Optional :class:`AsyncToolHandlerDeps` for tests; the
                 canonical Deps pattern (mirrors
                 :class:`kairix.worker.WorkerDeps`). Production callers
                 leave None and the default factory wires
                 ``kairix.paths.db_path`` as the call-log destination.
    """
    safe = wrap_tool_errors(handler)
    resolved_deps = deps if deps is not None else AsyncToolHandlerDeps()
    resolve_db_path = resolved_deps.db_path_fn
    resolve_dispatch_executor = resolved_deps.dispatch_executor_fn
    resolve_obs_executor = resolved_deps.obs_executor_fn
    handler_name = getattr(handler, "__name__", "<unknown>")

    @functools.wraps(handler)
    async def _wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        started = time.monotonic()
        success = False
        error_class: str | None = None
        payload_hash = _payload_hash(kwargs)
        loop = asyncio.get_running_loop()
        result: dict[str, Any] = {}
        try:
            # #403 — dispatch onto the kairix-owned executor instead of
            # the event loop's default pool. ``asyncio.to_thread`` uses
            # ``loop.get_default_executor()`` which on CPython 3.12
            # defaults to ``min(32, cpu_count + 4)`` — six threads on a
            # 2-CPU production container. Six concurrent dogfood agents
            # saturated the pool (root cause of #403). The dedicated
            # dispatch pool has 32 workers by default (env override
            # ``KAIRIX_MCP_DISPATCH_WORKERS``).
            result = await loop.run_in_executor(resolve_dispatch_executor(), lambda: safe(*args, **kwargs))
            # Many tools return ``{"error": "", ...}`` on success (empty
            # string = "no error happened"); treat a falsy error value
            # as success, not just the key's absence (#401).
            error_value = result.get("error")
            success = not error_value
            error_class = _extract_error_class(error_value)
            # #405 — surface ``latency_ms`` in the result envelope so agents
            # don't need external wall-clock measurement. ``setdefault``
            # preserves search's existing float; every other tool's envelope
            # now carries the wrapper-measured integer. Done BEFORE return
            # so the field is visible to the caller.
            if isinstance(result, dict):
                result.setdefault("latency_ms", int((time.monotonic() - started) * 1000))
            return result
        except Exception as exc:
            # `safe` swallows handler exceptions, so reaching here means
            # the wrapper itself (run_in_executor, executor lifecycle)
            # raised. Record the class and re-raise.
            error_class = type(exc).__name__
            raise
        finally:
            # #403 — fire-and-forget the obs INSERT onto a dedicated
            # single-thread executor. The previous design called
            # ``_record_mcp_call`` synchronously, which runs on the
            # event loop thread (``finally`` of an async fn resumes in
            # the coroutine context). 1-5 ms loop-thread SQLite I/O
            # serialised with every other in-flight dispatch's return
            # path. ``executor.submit`` returns immediately; the row
            # lands on disk asynchronously from the response path.
            _emit_call_log(
                resolve_db_path=resolve_db_path,
                resolve_obs_executor=resolve_obs_executor,
                handler_name=handler_name,
                kwargs=kwargs,
                started=started,
                success=success,
                error_class=error_class,
                payload_hash=payload_hash,
            )

    return _wrapped
