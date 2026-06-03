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
    the sync work to the default asyncio threadpool via
    ``asyncio.to_thread``. The error envelope is applied inside the
    threaded call. Concurrent ``/mcp`` requests no longer queue behind
    each other on the event loop — resolves #177.

``async_tool_handler`` also records one row per call into the
``mcp_call_log`` SQLite table (issue #398 Workstream D). The write is
fire-and-forget — DB failures NEVER break the tool call. Operators
query the table via ``kairix probe mcp-calls``.

Tested through public surface only: register a handler that raises,
call it, observe the dict.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., dict[str, Any]])


# Resolver callable shape used by AsyncToolHandlerDeps.
DbPathFn = Callable[[], Path]


def _default_db_path() -> Path:
    """Production resolver — delegates to ``kairix.paths.db_path()``.

    Module-level so :class:`AsyncToolHandlerDeps`' default_factory can
    reference it. Tests construct a different ``AsyncToolHandlerDeps``
    instance with their own ``db_path_fn`` closure.
    """
    from kairix.paths import db_path as _db_path

    return _db_path()


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
    """

    db_path_fn: DbPathFn = field(default_factory=lambda: _default_db_path)


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
    observability MUST NOT break a tool call. If the mcp_call_log table
    is missing (legacy DB pre-migration), this function logs the
    OperationalError and returns silently.

    The connection is opened with ``check_same_thread=False`` because
    ``async_tool_handler`` runs sync handlers in arbitrary worker
    threads from the asyncio threadpool — a per-call connection
    handed off across threads is the simplest correct shape for
    fire-and-forget observability writes. Short-lived: opened, INSERT,
    commit, closed in one function call.
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


def async_tool_handler(
    handler: Callable[..., dict[str, Any]],
    *,
    deps: AsyncToolHandlerDeps | None = None,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Convert a sync MCP tool handler into an async one that offloads.

    The returned coroutine:
      1. Captures the call from FastMCP's tool dispatcher.
      2. Calls ``asyncio.to_thread`` to run the sync handler in the
         default ThreadPoolExecutor (8 workers in CPython 3.12 by
         default; configurable via ``loop.set_default_executor``).
      3. Returns the handler's dict, OR a structured error envelope
         when the handler raises (the ``wrap_tool_errors`` semantics
         are applied inside the thread, before re-entering the loop).
      4. Records one row in ``mcp_call_log`` (issue #398 Workstream D)
         with tool name, agent, latency, success flag, error class,
         and a short payload hash. The write is fire-and-forget;
         observability failure never breaks the tool call.

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
    handler_name = getattr(handler, "__name__", "<unknown>")

    @functools.wraps(handler)
    async def _wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        started = time.monotonic()
        success = False
        error_class: str | None = None
        payload_hash = _payload_hash(kwargs)
        try:
            result = await asyncio.to_thread(safe, *args, **kwargs)
            success = "error" not in result
            if not success:
                # safe always returns the {"error": "<Class>: <msg>"} envelope on
                # exception. Slice off the class prefix so the log groups by class.
                err_text = str(result.get("error", ""))
                error_class = err_text.split(":", 1)[0].strip() if err_text else None
            return result
        except Exception as exc:
            # `safe` swallows exceptions, so reaching here means the wrapper
            # itself (asyncio.to_thread, executor lifecycle) raised. Record
            # the class and re-raise.
            error_class = type(exc).__name__
            raise
        finally:
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
            else:
                _record_mcp_call(
                    db_path=db_path_value,
                    tool=handler_name,
                    agent=agent,
                    latency_ms=elapsed_ms,
                    success=success,
                    error_class=error_class,
                    payload_hash=payload_hash,
                )

    return _wrapped
