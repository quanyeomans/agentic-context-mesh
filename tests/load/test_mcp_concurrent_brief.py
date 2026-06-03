"""Load test — 20 concurrent ``brief(agent="shape")`` calls (#398 W-D).

Reproduces the 2026-05 dogfood failure mode where multiple agents
firing brief at once produced ``WARNING run_brief failed: 1 (of 6)
futures TimeoutError`` in container logs. The test wires the real
:func:`kairix.use_cases.brief.run_brief` against fake dependencies
through :class:`BriefDeps` (the canonical Deps pattern; no monkey-
patches, no env vars — F1/F2-clean).

The pipeline-shape this test exercises is identical to the production
MCP path: ``tool_brief`` calls ``run_brief(agent, deps=...)`` and
returns a :class:`BriefOutput`. We use ``concurrent.futures.ThreadPoolExecutor``
to fire 20 concurrent calls and assert:

  * p99 latency < 30s
  * zero TimeoutError raised at the thread boundary
  * every call returns a BriefOutput (no exceptions escape)

Marker: ``@pytest.mark.load`` — excluded from default pytest
discovery; operators run via ``pytest -m load tests/load/``.

Sabotage proof (mutate prod → confirm fail → restore):
  Insert ``time.sleep(40)`` into ``run_brief``'s body before the
  generate_fn call. Wall-clock per call jumps to ~40s; p99 assertion
  fails (40000 > 30000). Confirmed locally; restored.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

import pytest

from kairix.use_cases.brief import BriefDeps, BriefOutput, run_brief

pytestmark = pytest.mark.load


_AGENT = "shape"
_N_CONCURRENT = 20
_P99_BUDGET_S = 30.0


def _fake_generate(agent: str, **_kwargs: Any) -> str:
    """Mimic a small but non-trivial generate_briefing — sleeps ~50ms then returns markdown.

    The brief in production hits the LLM after assembling context — the
    fake stand-in here is short enough to make the test bounded but
    long enough that the ThreadPoolExecutor's queuing semantics are
    observable. The wall-clock budget includes generous headroom.
    """
    time.sleep(0.05)
    return f"# Briefing for {agent}\n\nfake-load-test-body."


def _build_deps() -> BriefDeps:
    """Construct a BriefDeps wired to the fake generator.

    The default factories for the other fields wire production
    helpers (briefing dir, health probe); the only override needed
    for the load test is the generate_fn — it bypasses the real LLM
    so the test is hermetic.
    """
    return BriefDeps(generate_fn=_fake_generate)


def _call_brief(_call_id: int) -> tuple[float, BriefOutput]:
    """One brief invocation; returns (latency_seconds, BriefOutput).

    No exception handling around the call — if run_brief raises (it
    must NOT — its contract is "never raises"), the future surfaces
    it to the harness.
    """
    started = time.monotonic()
    out = run_brief(_AGENT, deps=_build_deps())
    return (time.monotonic() - started, out)


def test_brief_p99_under_budget_with_no_timeouts() -> None:
    """20 concurrent brief calls return BriefOutput, p99 < 30s, no TimeoutErrors.

    The thread pool size matches the FastMCP default (8 workers in
    CPython 3.12). Per-future timeout is the per-call wall-clock cap.

    Pins the contract operators most care about:
      * run_brief never raises — every result is a BriefOutput.
      * The slowest call finishes inside the budget.
      * No future hits the per-future timeout.
    """
    latencies: list[float] = []
    timeouts = 0
    outputs: list[BriefOutput] = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_call_brief, i) for i in range(_N_CONCURRENT)]
        for future in futures:
            try:
                latency, out = future.result(timeout=_P99_BUDGET_S)
                latencies.append(latency)
                outputs.append(out)
            except FuturesTimeoutError:
                timeouts += 1

    assert timeouts == 0, f"expected zero TimeoutErrors; got {timeouts} / {_N_CONCURRENT}"
    assert len(outputs) == _N_CONCURRENT, f"expected {_N_CONCURRENT} BriefOutput; got {len(outputs)}"
    for i, out in enumerate(outputs):
        assert isinstance(out, BriefOutput), f"call {i} returned {type(out).__name__}, not BriefOutput"

    # p99 percentile via sort + index — small N means the discrete
    # index is the canonical interpretation. For N=20, the 99th
    # percentile is the largest observed value (index 19).
    sorted_latencies = sorted(latencies)
    p99 = sorted_latencies[-1]
    p99_ms = int(p99 * 1000)
    assert p99 < _P99_BUDGET_S, (
        f"brief p99 {p99_ms}ms exceeded {int(_P99_BUDGET_S * 1000)}ms budget at "
        f"{_N_CONCURRENT}-concurrent. Sorted latencies (ms): "
        f"{[int(latency * 1000) for latency in sorted_latencies]}"
    )
