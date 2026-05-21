"""Concurrent mode — sampled queries across a thread pool. STUB for P3.a.

Per ``/tmp/spike-C2-mode-integration.md`` §3.2, the concurrent
dispatcher composes three probe primitives:

  1. ``kairix.quality.probe.sampler.sample_weighted(cases, n, seed,
     weights=None)`` — deterministic CATEGORY_WEIGHTS-aware sample of
     ``request.queries`` cases.
  2. ``kairix.quality.probe.executor.run_concurrent(tasks, concurrency)``
     — fan out per-query callables across a worker pool of size
     ``request.concurrency``. Captures per-task latency.
  3. ``kairix.quality.probe.stats.latency_stats(durations) ->
     LatencyStats`` — produce p50/p95/p99 + mean for ``mode_metrics``.

The dispatcher then projects each ``TimedResult`` back onto a
``QueryRunResult`` using the ``task_index`` carried by the executor
(submission-order → completion-order remap).

This stub exists so the public surface (``run_concurrent`` symbol) is
discoverable from day one of the unified CLI; the body lands in the
P3.b slice. Single-shot mode ships standalone in P3.a.
"""

from __future__ import annotations

from kairix.quality.benchmark.modes.types import ModeRunRequest, ModeRunResult


def run_concurrent(request: ModeRunRequest) -> ModeRunResult:
    """Compose sample_weighted + run_concurrent + latency_stats — NOT YET IMPLEMENTED.

    See module docstring for the planned composition. Raises
    :class:`NotImplementedError` until the P3.b slice lands the body.
    """
    _ = request  # explicit drop documents intent — body wires this in P3.b
    raise NotImplementedError(
        "concurrent mode is deferred to the P3.b slice. "
        "next: compose kairix.quality.probe.sample_weighted + "
        "kairix.quality.probe.executor.run_concurrent + "
        "kairix.quality.probe.stats.latency_stats per C2 §3.2. "
        "fix: pin --mode single-shot until P3.b lands."
    )
