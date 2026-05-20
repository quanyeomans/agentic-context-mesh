"""Per-capability perf budgets runner — ``kairix probe-config --perf``.

Plan B-parity Week 4 Stream B. Runs the six capability-level operations
listed in ``suites/perf/budgets.json``, records wall-clock per
iteration, and compares the observed p50/p99 against the pinned budget.

Design contract:

- **Singular perf surface.** Lives under ``kairix/quality/probe/`` per
  F29 — no parallel benchmark harness in transport/ or providers/.
- **Total dependency injection.** Every operation accepts its
  collaborators (FactStore, FactExtractor, KairixPaths, corpus path)
  via constructor seams so tests can pin behaviour with fakes from
  ``tests/fakes.py`` (F1/F5 clean).
- **No env var reads here.** The CLI layer (``config_cli``) is the
  only place that touches env; this module just consumes injected
  paths and stores.
- **Skip rather than fail.** Operations whose backing capability has
  not yet shipped (e.g. ``kairix_prep_facts_federated`` until Cap #5
  wires facts into the SearchPipeline) emit a ``skipped`` outcome
  with a one-line "capability not yet wired" diagnostic. The CLI's
  exit code only reflects operations that actually ran.
- **Stats reuse.** Percentile computation goes through
  ``kairix.quality.probe.stats.latency_stats`` — same nearest-rank
  method as the existing search-probe so dashboards line up.

The runner is intentionally pure-Python with no native deps; every
operation is single-process, single-thread, ``time.perf_counter``-
timed. That keeps the probe usable as a one-shot CLI any operator
can invoke without spinning up infrastructure.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairix.quality.probe.stats import LatencyStats, latency_stats

__all__ = [
    "DEFAULT_PERF_ITERATIONS",
    "OperationResult",
    "PerfReport",
    "build_default_operations",
    "load_budgets",
    "run_perf_probe",
]


DEFAULT_PERF_ITERATIONS = 50
"""Iterations per operation when ``--perf`` is invoked without a count."""


# Operation-name constants — F17 (no string literal of >=10 chars duplicated
# >=3 times within the module). The same labels appear in budgets.json,
# CLI output, and the JSON envelope.
OP_PREP_VAULT_ONLY = "kairix_prep_vault_only"
OP_PREP_FACTS_FEDERATED = "kairix_prep_facts_federated"
OP_INGEST_CHAT_PER_TURN = "kairix_ingest_chat_per_turn"
OP_INGEST_CHAT_100_TURN = "kairix_ingest_chat_100_turn"
OP_FACT_FIND_CONFLICTS = "fact_find_conflicts"
OP_FEDERATED_SEARCH_TOP_K_15 = "federated_search_top_k_15"


# Skip diagnostic shared by operations awaiting Cap #5. Surfaced verbatim
# in human + JSON output so the operator sees exactly which capability
# is the blocker.
_SKIP_REASON_CAP5 = "skipped - capability not yet wired (Cap #5 federation pending)"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperationResult:
    """One operation's outcome: latencies, budget verdict, optional skip.

    ``p50_ms`` / ``p99_ms`` are zero when ``skipped=True``; the budget
    fields stay populated so the human renderer can still show what
    the budget *would have been* alongside the skip diagnostic.
    """

    operation: str
    iterations: int
    p50_ms: float
    p99_ms: float
    budget_p50_ms: float
    budget_p99_ms: float
    within_budget: bool
    skipped: bool = False
    skip_reason: str = ""
    stats: LatencyStats | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view — matches the ``--json`` envelope shape."""
        payload: dict[str, Any] = {
            "operation": self.operation,
            "iterations": self.iterations,
            "p50_ms": self.p50_ms,
            "p99_ms": self.p99_ms,
            "budget_p50": self.budget_p50_ms,
            "budget_p99": self.budget_p99_ms,
            "within_budget": self.within_budget,
        }
        if self.skipped:
            payload["skipped"] = True
            payload["skip_reason"] = self.skip_reason
        return payload


@dataclass(frozen=True)
class PerfReport:
    """Aggregate report across every operation in the run."""

    iterations: int
    results: list[OperationResult] = field(default_factory=list)

    @property
    def any_violation(self) -> bool:
        """True iff at least one non-skipped operation breached its budget."""
        return any((not r.skipped) and (not r.within_budget) for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view of the full report."""
        return {
            "iterations": self.iterations,
            "results": [r.to_dict() for r in self.results],
            "any_violation": self.any_violation,
        }


# ---------------------------------------------------------------------------
# Budget loading
# ---------------------------------------------------------------------------


def load_budgets(path: Path) -> dict[str, dict[str, float]]:
    """Load and validate the budgets JSON.

    Returns a mapping of ``operation_name -> {"p50_ms": float, "p99_ms": float}``.
    Raises ``ValueError`` if the file is malformed (missing keys / wrong
    types) so the CLI can surface an actionable error rather than
    silently treating every op as ``within_budget=True``.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"perf budgets file is not a JSON object: {path}")
    budgets: dict[str, dict[str, float]] = {}
    for op_name, raw_entry in raw.items():
        if not isinstance(raw_entry, dict):
            raise ValueError(f"perf budgets[{op_name!r}] is not an object: {raw_entry!r}")
        try:
            p50 = float(raw_entry["p50_ms"])
            p99 = float(raw_entry["p99_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"perf budgets[{op_name!r}] missing/invalid p50_ms/p99_ms: {exc}") from exc
        budgets[op_name] = {"p50_ms": p50, "p99_ms": p99}
    return budgets


# ---------------------------------------------------------------------------
# Operation runners — each returns either a list of latencies (ran) or
# a string (skip reason).
# ---------------------------------------------------------------------------


# Callable shape: takes the iteration count and returns either a list
# of millisecond latencies (operation ran) or a string (skip reason).
OperationCallable = Callable[[int], "list[float] | str"]


def _time_calls(iterations: int, fn: Callable[[], Any]) -> list[float]:
    """Time ``iterations`` invocations of ``fn`` and return ms latencies.

    Uses ``time.perf_counter`` for monotonic, high-resolution wall-
    clock. The fn is invoked once per iteration — no warm-up, since
    the budgets are end-to-end including any per-call setup the
    capability would do in production.
    """
    out: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        out.append((time.perf_counter() - start) * 1000.0)
    return out


def _make_prep_vault_only_op(
    prep_callable: Callable[[], Any],
) -> OperationCallable:
    """Build the ``kairix_prep_vault_only`` operation runner.

    ``prep_callable`` is a zero-arg closure that invokes the prep
    pipeline against the synthetic engagement-alpha corpus. Tests wire
    a sub-millisecond fake; production wires :func:`kairix.use_cases.prep.run_prep`
    with synthesised dependencies pointed at the engagement-alpha
    corpus.
    """

    def runner(iterations: int) -> list[float]:
        return _time_calls(iterations, prep_callable)

    return runner


def _make_skip_op(reason: str) -> OperationCallable:
    """Build an operation runner that always reports the same skip reason."""

    def runner(iterations: int) -> str:  # noqa: ARG001 — signature mandated by OperationCallable
        return reason

    return runner


def _make_ingest_per_turn_op(
    ingest_one_turn: Callable[[int], Any],
) -> OperationCallable:
    """Build the ``kairix_ingest_chat_per_turn`` operation runner.

    ``ingest_one_turn`` is invoked with the iteration index so the
    closure can pick a deterministic-but-unique turn from the
    synthetic corpus each call (so we measure real ingest cost, not
    a cached no-op).
    """

    def runner(iterations: int) -> list[float]:
        latencies: list[float] = []
        for i in range(iterations):
            start = time.perf_counter()
            ingest_one_turn(i)
            latencies.append((time.perf_counter() - start) * 1000.0)
        return latencies

    return runner


def _make_ingest_100_turn_op(
    ingest_100_turn: Callable[[], Any],
) -> OperationCallable:
    """Build the ``kairix_ingest_chat_100_turn`` operation runner.

    The 100-turn ingest is wall-clock heavy; the CLI defaults to 50
    iterations of the per-turn probe but typically passes a smaller
    iteration count for this one via the runtime iteration argument.
    """

    def runner(iterations: int) -> list[float]:
        return _time_calls(iterations, ingest_100_turn)

    return runner


def _make_fact_find_conflicts_op(
    find_conflicts: Callable[[], Any],
) -> OperationCallable:
    """Build the ``fact_find_conflicts`` operation runner."""

    def runner(iterations: int) -> list[float]:
        return _time_calls(iterations, find_conflicts)

    return runner


# ---------------------------------------------------------------------------
# Public — build_default_operations
# ---------------------------------------------------------------------------


def build_default_operations(
    *,
    prep_vault_only: Callable[[], Any] | None = None,
    ingest_one_turn: Callable[[int], Any] | None = None,
    ingest_100_turn: Callable[[], Any] | None = None,
    fact_find_conflicts: Callable[[], Any] | None = None,
) -> dict[str, OperationCallable]:
    """Wire the six capability operations from injected callables.

    Operations whose callable is ``None`` are mapped to a skip runner
    so the report still emits an entry for them with the canonical
    ``capability not yet wired`` diagnostic. The two ``federated``
    operations always start in skip mode until Cap #5 lands.

    Returns a mapping ``operation_name -> OperationCallable``; callers
    pass it to :func:`run_perf_probe` along with the budgets dict.
    """
    skip = _make_skip_op(_SKIP_REASON_CAP5)
    return {
        OP_PREP_VAULT_ONLY: (
            _make_prep_vault_only_op(prep_vault_only) if prep_vault_only is not None else skip
        ),
        # Cap #5 — facts federation in SearchPipeline not yet wired.
        OP_PREP_FACTS_FEDERATED: skip,
        OP_INGEST_CHAT_PER_TURN: (
            _make_ingest_per_turn_op(ingest_one_turn) if ingest_one_turn is not None else skip
        ),
        OP_INGEST_CHAT_100_TURN: (
            _make_ingest_100_turn_op(ingest_100_turn) if ingest_100_turn is not None else skip
        ),
        OP_FACT_FIND_CONFLICTS: (
            _make_fact_find_conflicts_op(fact_find_conflicts) if fact_find_conflicts is not None else skip
        ),
        # Cap #5 — federated_search_top_k_15 needs the fused FTS+vector
        # surface that the SearchPipeline federation enhancement ships.
        OP_FEDERATED_SEARCH_TOP_K_15: skip,
    }


# ---------------------------------------------------------------------------
# Public — run_perf_probe
# ---------------------------------------------------------------------------


def _build_skip_result(
    op_name: str,
    iterations: int,
    budget: Mapping[str, float],
    reason: str,
) -> OperationResult:
    """Return an :class:`OperationResult` for a skipped operation."""
    return OperationResult(
        operation=op_name,
        iterations=iterations,
        p50_ms=0.0,
        p99_ms=0.0,
        budget_p50_ms=budget["p50_ms"],
        budget_p99_ms=budget["p99_ms"],
        within_budget=True,  # skipped ops don't violate budget
        skipped=True,
        skip_reason=reason,
    )


def _build_ran_result(
    op_name: str,
    iterations: int,
    latencies: list[float],
    budget: Mapping[str, float],
) -> OperationResult:
    """Return an :class:`OperationResult` for an operation that ran."""
    stats = latency_stats(latencies)
    within = stats.p50_ms <= budget["p50_ms"] and stats.p99_ms <= budget["p99_ms"]
    return OperationResult(
        operation=op_name,
        iterations=iterations,
        p50_ms=stats.p50_ms,
        p99_ms=stats.p99_ms,
        budget_p50_ms=budget["p50_ms"],
        budget_p99_ms=budget["p99_ms"],
        within_budget=within,
        stats=stats,
    )


def run_perf_probe(
    *,
    iterations: int,
    budgets: Mapping[str, Mapping[str, float]],
    operations: Mapping[str, OperationCallable],
) -> PerfReport:
    """Run every operation in ``budgets`` and emit a :class:`PerfReport`.

    Parameters:

    - ``iterations`` — count per operation (>=1). The CLI defaults
      to :data:`DEFAULT_PERF_ITERATIONS` (50) but can be overridden.
    - ``budgets`` — output of :func:`load_budgets`.
    - ``operations`` — output of :func:`build_default_operations`,
      possibly overridden in tests to inject deterministic latencies.

    Operations present in ``budgets`` but missing from ``operations``
    are reported as skipped with the "capability not yet wired"
    diagnostic so the report shape stays stable across releases.
    """
    if iterations < 1:
        raise ValueError(f"iterations must be >= 1; got {iterations}")
    results: list[OperationResult] = []
    for op_name, budget in budgets.items():
        runner = operations.get(op_name)
        if runner is None:
            results.append(_build_skip_result(op_name, iterations, budget, _SKIP_REASON_CAP5))
            continue
        outcome = runner(iterations)
        if isinstance(outcome, str):
            results.append(_build_skip_result(op_name, iterations, budget, outcome))
            continue
        results.append(_build_ran_result(op_name, iterations, outcome, budget))
    return PerfReport(iterations=iterations, results=results)
