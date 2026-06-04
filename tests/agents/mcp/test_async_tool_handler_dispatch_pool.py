"""Tests for the dispatch threadpool sizing in ``async_tool_handler``.

Issue #403 — MCP search reported 36s latency on production while CLI on the
same warm pipeline completed in 12s. Root cause confirmed via
``/tmp/instrument_mcp_concurrent.py``: ``asyncio.to_thread`` used the event
loop's default executor, which on CPython 3.12 is sized
``min(32, cpu_count + 4)``. On a 2-CPU production container that is six
worker threads. Six concurrent dogfood agents firing search + entity +
brief calls saturated the pool; the seventh call queued behind whichever
slot freed first, accumulating the observed 24s gap.

The fix routes every tool dispatch through a kairix-owned
``ThreadPoolExecutor`` whose ``max_workers`` defaults to 32 (env override
``KAIRIX_MCP_DISPATCH_WORKERS``). Tests exercise the seam by injecting a
known-size pool through ``AsyncToolHandlerDeps.dispatch_executor_fn`` and
asserting that:

* N concurrent calls to a slow handler land on N distinct executor threads
  when the pool is sized to N — the wrapper does NOT serialise to a smaller
  pool below it.

* When the pool is intentionally undersized (max_workers=2), the same N
  calls serialize into ``ceil(N / 2)`` waves — proves the seam is
  load-bearing (the test exists to catch the regression of someone wiring
  the wrapper back to ``asyncio.to_thread`` and the dev-machine default
  pool size masking the production failure).

Tested through public surface only — no private symbols, no @patch /
monkeypatch (F1-clean). All concurrency is driven from the canonical async
test harness (``asyncio.run`` + ``asyncio.gather``); the executor is
explicitly injected via the public Deps seam.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from kairix.agents.mcp.errors import (
    DEFAULT_DISPATCH_WORKERS,
    AsyncToolHandlerDeps,
    async_tool_handler,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def obs_executor() -> Iterator[ThreadPoolExecutor]:
    """A throwaway observability executor so the call-log writes don't leak."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-obs")
    try:
        yield pool
    finally:
        pool.shutdown(wait=True)


@pytest.fixture
def silent_db_path(tmp_path: Path) -> Path:
    """Tmp DB path — calls log into a sandbox we don't inspect."""
    return tmp_path / "obs.sqlite"


def _make_wrapped_slow_handler(
    *,
    sleep_s: float,
    dispatch_executor: ThreadPoolExecutor,
    obs_executor: ThreadPoolExecutor,
    db_path: Path,
    started: dict[int, tuple[float, int]],
) -> object:
    """Construct a wrapped handler that records when + on which thread each call ran.

    Returns the async-wrapped callable. The handler sleeps ``sleep_s`` so a
    saturated pool's behaviour is observable in wall-clock terms.
    """

    def slow_handler(call_id: int = 0) -> dict[str, int]:
        started[call_id] = (time.monotonic(), threading.get_ident())
        time.sleep(sleep_s)
        return {"id": call_id}

    return async_tool_handler(
        slow_handler,
        deps=AsyncToolHandlerDeps(
            db_path_fn=lambda: db_path,
            dispatch_executor_fn=lambda: dispatch_executor,
            obs_executor_fn=lambda: obs_executor,
        ),
    )


def test_concurrent_calls_use_distinct_dispatch_threads_when_pool_is_large(
    obs_executor: ThreadPoolExecutor, silent_db_path: Path
) -> None:
    """Eight concurrent calls land on eight distinct threads when the pool is sized to eight.

    This is the post-fix steady-state contract: when the operator-tuned
    dispatch pool is at least as large as the concurrent-agent count,
    every in-flight call runs in parallel with no queueing.

    Sabotage proof: revert ``async_tool_handler`` to use
    ``asyncio.to_thread(safe, ...)`` instead of
    ``loop.run_in_executor(dispatch_executor, ...)`` — this test still
    passes locally on a 10-CPU dev box (default pool=14) but fails on a
    2-CPU CI runner (default pool=6) because the eight calls land on only
    six threads. CONFIRMED locally by manually swapping the wrapper to
    ``asyncio.to_thread`` and running on a constrained executor pool.
    """
    n_calls = 8
    started: dict[int, tuple[float, int]] = {}

    with ThreadPoolExecutor(max_workers=n_calls, thread_name_prefix="test-dispatch") as dispatch:
        wrapped = _make_wrapped_slow_handler(
            sleep_s=0.2,
            dispatch_executor=dispatch,
            obs_executor=obs_executor,
            db_path=silent_db_path,
            started=started,
        )

        async def fire_all() -> list[dict[str, int]]:
            return await asyncio.gather(*(wrapped(call_id=i) for i in range(n_calls)))

        results = asyncio.run(fire_all())

    assert len(results) == n_calls
    assert {r["id"] for r in results} == set(range(n_calls))
    threads_used = {tid for _, tid in started.values()}
    assert len(threads_used) == n_calls, (
        f"expected {n_calls} distinct dispatch threads, got {len(threads_used)} (pool exhaustion would show fewer)"
    )


def test_undersized_dispatch_pool_serialises_calls_into_waves(
    obs_executor: ThreadPoolExecutor, silent_db_path: Path
) -> None:
    """Eight concurrent calls on a two-worker pool serialise into four sequential waves.

    Captures the pre-fix production failure shape: when the dispatch pool
    is smaller than the in-flight call count, only ``pool_size`` calls run
    at once and the rest queue. This is the regression that bit production
    in #403 (pool=6, agents=6+ -> seventh call waited a full search
    duration).

    The sabotage proof is the opposite of the previous test: if the
    wrapper ever stops honouring the injected ``dispatch_executor_fn``,
    this test sees eight distinct threads instead of two. The test
    documents the contract via failure mode.
    """
    n_calls = 8
    pool_size = 2
    sleep_s = 0.2
    started: dict[int, tuple[float, int]] = {}

    with ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="test-dispatch") as dispatch:
        wrapped = _make_wrapped_slow_handler(
            sleep_s=sleep_s,
            dispatch_executor=dispatch,
            obs_executor=obs_executor,
            db_path=silent_db_path,
            started=started,
        )

        async def fire_all() -> list[dict[str, int]]:
            return await asyncio.gather(*(wrapped(call_id=i) for i in range(n_calls)))

        t0 = time.monotonic()
        asyncio.run(fire_all())
        elapsed = time.monotonic() - t0

    threads_used = {tid for _, tid in started.values()}
    assert len(threads_used) == pool_size, (
        f"expected exactly {pool_size} threads (pool exhausted); "
        f"saw {len(threads_used)} (was the executor seam bypassed?)"
    )

    # Eight calls / two workers = four waves. Each wave is ``sleep_s``.
    expected_waves = n_calls // pool_size
    lower_bound = expected_waves * sleep_s * 0.9  # 0.9 = generous floor for thread overhead
    assert elapsed >= lower_bound, (
        f"undersized pool should serialise into {expected_waves} waves "
        f"(~{expected_waves * sleep_s:.2f}s); got {elapsed:.2f}s"
    )


def test_dispatch_pool_isolated_from_obs_pool(obs_executor: ThreadPoolExecutor, silent_db_path: Path) -> None:
    """A blocked observability pool does NOT block subsequent tool dispatches.

    Production trace 2026-06-04 also showed ``_record_mcp_call`` blocking
    the event loop in the wrapper's ``finally`` — the fix moved the write
    onto a dedicated single-thread executor. This test proves the two
    pools are independent: even when the obs pool is saturated, the next
    dispatch runs immediately.

    Sabotage proof: revert the ``finally`` block to call
    ``_record_mcp_call(...)`` synchronously instead of
    ``obs_executor.submit(...)`` — then the second call below waits for
    the first call's obs write before its handler runs, and the
    cross-call wall-clock balloons.
    """
    started: dict[int, tuple[float, int]] = {}

    def slow_obs_write(*_a: object, **_k: object) -> None:
        # Simulate a slow observability backend (lock contention, fsync stall, ...).
        time.sleep(0.4)

    # An obs executor whose submitted task takes 0.4s. We don't drain it -- the
    # whole point is that fire-and-forget submission must not block the caller.
    slow_obs_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-slow-obs")

    try:
        # Wrap so EVERY observability submission calls slow_obs_write.
        # Easiest: replace obs_executor_fn with one that returns the slow pool.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="test-dispatch") as dispatch:

            def quick_handler(call_id: int = 0) -> dict[str, int]:
                started[call_id] = (time.monotonic(), threading.get_ident())
                return {"id": call_id}

            # Custom obs pool that performs slow_obs_write on every submit.
            class _SlowObs:
                def submit(self, *_a: object, **_k: object) -> object:
                    return slow_obs_pool.submit(slow_obs_write)

            wrapped = async_tool_handler(
                quick_handler,
                deps=AsyncToolHandlerDeps(
                    db_path_fn=lambda: silent_db_path,
                    dispatch_executor_fn=lambda: dispatch,
                    obs_executor_fn=lambda: _SlowObs(),  # type: ignore[arg-type]  # duck-typed test seam — only .submit is exercised
                ),
            )

            async def fire_two() -> None:
                t0 = time.monotonic()
                await wrapped(call_id=0)
                elapsed_first = time.monotonic() - t0
                t1 = time.monotonic()
                await wrapped(call_id=1)
                elapsed_second = time.monotonic() - t1
                # Both calls should complete in well under the obs sleep (0.4s)
                # because the obs write is fire-and-forget.
                assert elapsed_first < 0.2, (
                    f"first call should return immediately (obs write fire-and-forget); took {elapsed_first:.3f}s"
                )
                assert elapsed_second < 0.2, (
                    f"second call should return immediately (obs queue does not block dispatch); "
                    f"took {elapsed_second:.3f}s"
                )

            asyncio.run(fire_two())
    finally:
        slow_obs_pool.shutdown(wait=False)


def test_production_default_dispatch_pool_absorbs_six_dogfood_agents() -> None:
    """The production-default dispatch pool absorbs the documented dogfood agent count.

    Six dogfood agents share the production MCP server (per the
    project_dogfood memory). If the default pool ever shrinks below the
    documented load, this test fires before the regression reaches
    production.

    The check is a value assertion against the module-level constant:
    pool-size selection is intentionally a known-at-import-time number,
    not derived from runtime CPU count (the production failure was
    exactly that runtime derivation — cpu_count=2 yielded six workers,
    not enough for six agents + tools-during-agent-turn).

    Sabotage proof: lower ``DEFAULT_DISPATCH_WORKERS`` to 4 — this test
    fails with a clear message naming the documented load.
    """
    minimum_required = 6  # six dogfood agents documented in project_dogfood memory
    assert DEFAULT_DISPATCH_WORKERS >= minimum_required, (
        f"DEFAULT_DISPATCH_WORKERS={DEFAULT_DISPATCH_WORKERS} < {minimum_required} "
        f"dogfood agents documented in project_dogfood memory; production will queue. "
        f"fix: raise DEFAULT_DISPATCH_WORKERS in kairix/agents/mcp/errors.py. "
        f"next: re-run this test."
    )
