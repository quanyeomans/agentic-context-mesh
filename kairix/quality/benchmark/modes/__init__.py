"""Mode dispatchers for the unified ``kairix benchmark run`` CLI.

Per ``/tmp/spike-C2-mode-integration.md``, the unified benchmark
runner picks one of three execution modes for any given invocation:

* ``single-shot`` — every case in the suite runs exactly once, in
  suite order. Matches historical ``kairix benchmark run`` behaviour.
  Implemented in :mod:`kairix.quality.benchmark.modes.single_shot`.
  Ships first in the P3.a slice (this slice).
* ``concurrent`` — sample of cases weighted by ``CATEGORY_WEIGHTS``,
  fanned out across a thread pool. Composes
  :mod:`kairix.quality.probe.executor.run_concurrent` and friends.
  STUB in P3.a; body lands in P3.b.
* ``soak`` — repeated workload with stability instrumentation
  (memory growth, fd leaks, signature drift). Composes
  :func:`kairix.quality.soak.run_soak` with a custom workload closure.
  STUB in P3.a; body lands in P3.c.

All three modes share the :class:`ModeRunRequest` /
:class:`ModeRunResult` contract from
:mod:`kairix.quality.benchmark.modes.types` and produce a uniform
``tuple[QueryRunResult, ...]`` the scorer registry (P2) consumes.

Per-mode case selection shape (encoded in the existing primitives, no
gap to fix):

* ``single-shot``: ALL cases, suite order, exactly once.
* ``concurrent``: SAMPLE of cases (weighted, deterministic-shuffled).
* ``soak``: ALL cases, suite order, repeated N iterations.
"""

from __future__ import annotations

from kairix.quality.benchmark.modes.concurrent import run_concurrent
from kairix.quality.benchmark.modes.single_shot import run_single_shot
from kairix.quality.benchmark.modes.soak import run_soak
from kairix.quality.benchmark.modes.types import (
    ModeRunRequest,
    ModeRunResult,
    QueryRunResult,
)

__all__ = [
    "ModeRunRequest",
    "ModeRunResult",
    "QueryRunResult",
    "run_concurrent",
    "run_single_shot",
    "run_soak",
]
