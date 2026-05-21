"""Mode-agnostic request/result shapes for the unified benchmark dispatcher.

Per ``/tmp/spike-C2-mode-integration.md`` §3, every mode (single-shot,
concurrent, soak) consumes the same ``ModeRunRequest`` and emits the
same ``ModeRunResult``. The mode-specific knobs live on the request as
optional fields; modes ignore the ones they don't use. The result
carries per-query evidence (``per_query_runs``) plus mode-aggregate
metrics (``mode_metrics``) plus any raw envelope a mode wants to expose
for diagnostics.

The per-query record ``QueryRunResult`` lives under
``kairix.quality.scoring.types`` (owned by the P2 scoring slice). We
import it here so dispatchers can build the per-query list directly —
no second copy of the dataclass.

Layering. This file lives in ``kairix/quality/benchmark/modes/`` and
imports from ``kairix.quality.scoring`` (peer quality package) — no
``kairix/core/**`` ↔ ``providers/`` cross-traffic (F26).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kairix.quality.benchmark.suite import BenchmarkSuite
    from kairix.quality.probe.runner import SampledQuery

from kairix.quality.scoring.types import QueryRunResult

__all__ = [
    "ModeRunRequest",
    "ModeRunResult",
    "QueryRunResult",
]


@dataclass(frozen=True)
class ModeRunRequest:
    """Inputs to any mode dispatcher.

    The dispatcher knows nothing about retrieval, scoring, or transport
    choice — those collapse into ``query_executor``. The unified CLI
    builds the closure once per invocation and hands it to whichever
    mode the operator picked.

    Per-mode knobs (``queries``, ``concurrency``, ``repeat``, …) are
    optional; modes that don't consume them ignore them silently. This
    keeps the request type stable across all three modes (single-shot
    + concurrent + soak land incrementally per C2 §4).

    The ``query_executor`` contract: MUST NOT raise. On failure, return a
    ``QueryRunResult`` with ``succeeded=False`` and ``error="..."``.
    Modes assume this — violating it is a bug in the caller.
    """

    suite: BenchmarkSuite
    query_executor: Callable[[SampledQuery], QueryRunResult]
    seed: int = 0

    # Concurrent-mode knobs (ignored by single-shot + soak):
    queries: int | None = None
    concurrency: int | None = None

    # Soak-mode knobs (ignored by single-shot + concurrent):
    repeat: int | None = None
    max_memory_growth_mb: float | None = None
    max_log_volume_mb: float | None = None
    max_time_drift_pct: float | None = None


@dataclass(frozen=True)
class ModeRunResult:
    """Output of any mode dispatcher.

    Three concerns:
      - ``per_query_runs``: per-case evidence (the scorer registry's input).
      - ``mode_metrics``: mode-specific aggregates (mean latency for
        single-shot; p95 + mean_concurrency for concurrent; memory
        growth + log volume for soak). Floats only — string-valued
        signals like bottleneck hints belong under ``raw``.
      - ``errors``: human-readable error strings (one per failed task /
        iteration). Empty tuple on a clean run.
      - ``raw``: the underlying ``ProbeResult`` / ``BurstResult`` /
        ``SoakResult`` envelope (or whatever the mode chose to expose)
        for operators that want full diagnostics.
    """

    per_query_runs: tuple[QueryRunResult, ...]
    mode_metrics: dict[str, float]
    errors: tuple[str, ...]
    raw: dict[str, Any] = field(default_factory=dict)
