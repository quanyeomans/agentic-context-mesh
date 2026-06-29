"""Perf & affordance SLO harness — the measurement engine (PLA-256).

Captures, in ONE report, the three SLO dimensions the Agent Performance &
Affordance Wave needs to claim "directional improvement" against measured
baselines, for the most-used agent commands (``brief`` / ``remember`` /
``recall`` / ``search``):

- **Latency** — p50/p95, COLD (first call after process start) and WARM,
  at concurrency 1 and N (default 5).
- **Fact-recall quality** — recall@k + NDCG@k against a labelled fact
  suite (the #340 fact-pattern entity-attribute-value set).
- **Affordance completeness** — the fraction of agent-facing result
  records that carry a resolvable ``source_uri`` breadcrumb.

Design contract:

- **F29-clean** — lives under ``kairix/quality/probe/``, the single perf
  surface for the whole project.
- **Reuse, don't duplicate** — latency timing goes through the existing
  :func:`kairix.quality.probe.executor.run_concurrent` and percentile
  stats through :func:`kairix.quality.probe.stats.latency_stats`, so the
  numbers line up with the search-probe dashboards.
- **Pure** — no I/O, no heavy imports, total dependency injection. The
  four command adapters and the recall suites are injected as callables
  (see :mod:`kairix.quality.probe.slo_probes`), so the engine is
  deterministic and unit-testable without a configured index.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from kairix.quality.probe.executor import TimedResult, run_concurrent
from kairix.quality.probe.stats import LatencyStats, latency_stats

__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_RECALL_K",
    "PHASE_COLD",
    "PHASE_WARM",
    "AffordanceRow",
    "CommandCall",
    "CommandProbe",
    "GroundTruthFact",
    "LatencyRow",
    "RecallRow",
    "RecallSuite",
    "SLOReport",
    "build_report",
    "is_resolvable_breadcrumb",
    "measure_command",
    "measure_recall",
]

DEFAULT_CONCURRENCY = 5
"""Default high-concurrency level (N) — the teaming-load arm of the probe."""

DEFAULT_RECALL_K = 5
"""Default cut-off for recall@k / NDCG@k on the fact suite."""

PHASE_COLD = "cold"
PHASE_WARM = "warm"

# Breadcrumb sentinels an agent cannot follow. A result whose breadcrumb
# stringifies to one of these (case-insensitive) is counted as MISSING a
# source_uri for the affordance metric — present-but-useless is not
# "resolvable".
_UNRESOLVABLE: frozenset[str] = frozenset({"", "none", "null", "unknown", "n/a", "-"})


def is_resolvable_breadcrumb(value: Any) -> bool:
    """True when ``value`` is a source_uri breadcrumb an agent can follow.

    Resolvable means: a string that, stripped and lower-cased, is not a
    known null/placeholder sentinel. The affordance metric counts the
    fraction of agent-facing result records whose breadcrumb passes this
    test — a missing or placeholder breadcrumb (``None``, ``""``,
    ``"unknown"``) is a dead end the agent cannot trace back to a source.
    """
    if not isinstance(value, str):
        return False
    return value.strip().lower() not in _UNRESOLVABLE


@dataclass(frozen=True)
class GroundTruthFact:
    """One labelled fact in a recall suite — an entity-attribute-value triple.

    Mirrors the ``ground-truth-facts.json`` shape used by the
    reference-library conversation corpora (#340). ``value`` is matched as
    a case-insensitive substring of a retrieved record's value, so an
    over-long retrieved value still counts as a recall hit.
    """

    entity: str
    attribute: str
    value: str


@dataclass(frozen=True)
class CommandCall:
    """One command invocation's agent-facing outcome.

    ``breadcrumbs`` carries one entry per result record the agent would
    see; each entry is that record's source_uri breadcrumb (a document
    path, ``turn://`` id, ``memory://`` id, ...) or ``None`` when the
    record carries none. The harness only needs the breadcrumbs to score
    affordance — never the result bodies — so adapters return just these.
    """

    breadcrumbs: tuple[str | None, ...] = ()


@dataclass(frozen=True)
class CommandProbe:
    """A named agent command plus its workload and a single-call runner.

    ``name`` is the command label (``brief`` / ``remember`` / ``recall``
    / ``search``). ``payloads`` is the workload (>=1 input); the harness
    times one cold call then re-runs the whole workload warm at
    concurrency 1 and N. ``run`` executes the command once for a payload
    and returns a :class:`CommandCall`.
    """

    name: str
    payloads: tuple[str, ...]
    run: Callable[[str], CommandCall]


@dataclass(frozen=True)
class LatencyRow:
    """Latency distribution for one (command, phase, concurrency) cell."""

    command: str
    phase: str
    concurrency: int
    stats: LatencyStats

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view of this latency cell."""
        return {
            "command": self.command,
            "phase": self.phase,
            "concurrency": self.concurrency,
            "samples": self.stats.n,
            "p50_ms": self.stats.p50_ms,
            "p95_ms": self.stats.p95_ms,
        }


@dataclass(frozen=True)
class AffordanceRow:
    """Breadcrumb-completeness for one command's agent-facing results."""

    command: str
    total_records: int
    resolvable: int

    @property
    def pct_resolvable(self) -> float:
        """Percentage of records carrying a resolvable source_uri.

        A command that surfaced zero records is vacuously complete
        (100.0) — there is nothing missing a breadcrumb.
        """
        if self.total_records == 0:
            return 100.0
        return round(100.0 * self.resolvable / self.total_records, 1)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view of this affordance row."""
        return {
            "command": self.command,
            "total_records": self.total_records,
            "resolvable": self.resolvable,
            "pct_resolvable": self.pct_resolvable,
        }


@dataclass(frozen=True)
class RecallRow:
    """Fact-recall quality for one labelled suite at cut-off ``k``."""

    suite: str
    k: int
    n_facts: int
    recall_at_k: float
    ndcg_at_k: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view of this recall row."""
        return {
            "suite": self.suite,
            "k": self.k,
            "n_facts": self.n_facts,
            "recall_at_k": self.recall_at_k,
            "ndcg_at_k": self.ndcg_at_k,
        }


@dataclass(frozen=True)
class SLOReport:
    """The single artefact the harness produces — all three SLO dimensions."""

    concurrency_n: int
    recall_k: int
    latency: tuple[LatencyRow, ...] = ()
    affordance: tuple[AffordanceRow, ...] = ()
    recall: tuple[RecallRow, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view consumed by ``kairix slo --format json``."""
        return {
            "concurrency_n": self.concurrency_n,
            "recall_k": self.recall_k,
            "latency": [r.to_dict() for r in self.latency],
            "affordance": [r.to_dict() for r in self.affordance],
            "recall": [r.to_dict() for r in self.recall],
        }

    def render_table(self) -> str:
        """Render the operator-facing cold/warm/concurrency + quality table."""
        sections = [
            f"kairix SLO harness  (concurrency N={self.concurrency_n}, recall@{self.recall_k})",
            _render_latency(self.latency),
            _render_recall(self.recall),
            _render_affordance(self.affordance),
        ]
        return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Latency + affordance measurement
# ---------------------------------------------------------------------------


def _call_of(result: TimedResult[Any]) -> CommandCall | None:
    """Recover the :class:`CommandCall` a probe task produced, if any."""
    value = result.result
    return value if isinstance(value, CommandCall) else None


def _bind(probe: CommandProbe, payload: str) -> Callable[[], CommandCall]:
    """Bind one zero-arg task for ``payload`` (avoids late-binding-loop bugs).

    A typed factory rather than a ``lambda p=payload: ...`` so mypy can
    infer the task type and the run-concurrent contract stays explicit.
    """
    return lambda: probe.run(payload)


def measure_command(probe: CommandProbe, *, concurrency_n: int) -> tuple[list[LatencyRow], AffordanceRow]:
    """Measure one command's cold/warm latency and breadcrumb completeness.

    Sequence:

    1. **Cold** — time the very first call (a single observation at
       concurrency 1). In real mode this pays the factory-build /
       cold-start tax; in synthetic mode it is just the first sample.
    2. **Warm c1** — re-run the whole workload sequentially through the
       pool; the underlying pipeline is now warm.
    3. **Warm cN** — re-run the whole workload at concurrency ``N``.

    Affordance is aggregated over the warm-c1 results — the representative
    agent-facing surface.

    Raises:
        ValueError: when the probe has no payloads (nothing to measure).
    """
    if not probe.payloads:
        raise ValueError(f"probe {probe.name!r} has no payloads to measure")

    cold = run_concurrent([_bind(probe, probe.payloads[0])], concurrency=1)
    cold_stats = latency_stats([r.duration_ms for r in cold.results])

    warm_tasks = [_bind(probe, payload) for payload in probe.payloads]
    warm_c1 = run_concurrent(warm_tasks, concurrency=1)
    warm_cn = run_concurrent(warm_tasks, concurrency=concurrency_n)

    rows = [
        LatencyRow(probe.name, PHASE_COLD, 1, cold_stats),
        LatencyRow(probe.name, PHASE_WARM, 1, latency_stats([r.duration_ms for r in warm_c1.results])),
        LatencyRow(probe.name, PHASE_WARM, concurrency_n, latency_stats([r.duration_ms for r in warm_cn.results])),
    ]
    affordance = _affordance_from(probe.name, warm_c1.results)
    return rows, affordance


def _affordance_from(command: str, results: Sequence[TimedResult[Any]]) -> AffordanceRow:
    """Count resolvable breadcrumbs across every record in ``results``."""
    total = 0
    resolvable = 0
    for result in results:
        call = _call_of(result)
        if call is None:
            continue
        for breadcrumb in call.breadcrumbs:
            total += 1
            if is_resolvable_breadcrumb(breadcrumb):
                resolvable += 1
    return AffordanceRow(command=command, total_records=total, resolvable=resolvable)


# ---------------------------------------------------------------------------
# Fact-recall quality measurement
# ---------------------------------------------------------------------------


def _fact_matches(gt: GroundTruthFact, hit: Any) -> bool:
    """True when ``hit`` satisfies the ground-truth fact ``gt``.

    Accepts either a ``FactHit``-shaped object (``hit.record.entity`` ...)
    or a bare ``FactRecord``-shaped object. Match = same entity, same
    attribute, and the ground-truth value is a case-insensitive substring
    of the retrieved value — the same relevance relation the eval suite
    runner uses for extractor scoring.
    """
    record = getattr(hit, "record", hit)
    try:
        return (
            str(record.entity).strip().lower() == gt.entity.strip().lower()
            and str(record.attribute).strip().lower() == gt.attribute.strip().lower()
            and gt.value.strip().lower() in str(record.value).strip().lower()
        )
    except AttributeError:
        return False


def measure_recall(
    suite: str,
    gt_facts: Sequence[GroundTruthFact],
    recall_fn: Callable[[str], Sequence[Any]],
    *,
    k: int,
) -> RecallRow:
    """Score recall@k + NDCG@k of ``recall_fn`` against ``gt_facts``.

    For each ground-truth fact, the harness queries ``recall_fn`` with
    ``"<entity> <attribute>"`` and inspects the top ``k`` hits. recall@k
    is the fraction of facts whose matching record appears in the top
    ``k``. NDCG@k is the rank-discounted gain averaged over the facts —
    each fact has exactly one relevant record, so the ideal DCG is 1.0
    and NDCG collapses to ``mean(1 / log2(rank + 2))`` over the facts
    that were retrieved (0 for misses).
    """
    n = len(gt_facts)
    if n == 0:
        return RecallRow(suite=suite, k=k, n_facts=0, recall_at_k=0.0, ndcg_at_k=0.0)

    hits = 0
    dcg_sum = 0.0
    for gt in gt_facts:
        retrieved = list(recall_fn(f"{gt.entity} {gt.attribute}"))[:k]
        rank = next((i for i, hit in enumerate(retrieved) if _fact_matches(gt, hit)), None)
        if rank is not None:
            hits += 1
            dcg_sum += 1.0 / math.log2(rank + 2)
    return RecallRow(
        suite=suite,
        k=k,
        n_facts=n,
        recall_at_k=round(hits / n, 4),
        ndcg_at_k=round(dcg_sum / n, 4),
    )


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


# A recall suite is (suite_name, ground-truth facts, recall callable).
RecallSuite = tuple[str, Sequence[GroundTruthFact], Callable[[str], Sequence[Any]]]


def build_report(
    *,
    probes: Sequence[CommandProbe],
    recall_suites: Sequence[RecallSuite],
    concurrency_n: int = DEFAULT_CONCURRENCY,
    recall_k: int = DEFAULT_RECALL_K,
) -> SLOReport:
    """Run every probe + recall suite and fold the result into one report.

    Raises:
        ValueError: when ``concurrency_n`` or ``recall_k`` is < 1.
    """
    if concurrency_n < 1:
        raise ValueError(f"concurrency_n must be >= 1; got {concurrency_n}")
    if recall_k < 1:
        raise ValueError(f"recall_k must be >= 1; got {recall_k}")

    latency_rows: list[LatencyRow] = []
    affordance_rows: list[AffordanceRow] = []
    for probe in probes:
        rows, affordance = measure_command(probe, concurrency_n=concurrency_n)
        latency_rows.extend(rows)
        affordance_rows.append(affordance)

    recall_rows = [measure_recall(name, facts, fn, k=recall_k) for name, facts, fn in recall_suites]

    return SLOReport(
        concurrency_n=concurrency_n,
        recall_k=recall_k,
        latency=tuple(latency_rows),
        affordance=tuple(affordance_rows),
        recall=tuple(recall_rows),
    )


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

_LATENCY_HEADER = "Latency (ms)"
_RECALL_HEADER = "Fact-recall quality"
_AFFORDANCE_HEADER = "Affordance completeness (resolvable source_uri)"


def _render_latency(rows: Sequence[LatencyRow]) -> str:
    lines = [_LATENCY_HEADER, f"  {'command':<10} {'phase':<6} {'conc':>4} {'n':>4} {'p50':>8} {'p95':>8}"]
    for row in rows:
        lines.append(
            f"  {row.command:<10} {row.phase:<6} {row.concurrency:>4} "
            f"{row.stats.n:>4} {row.stats.p50_ms:>8.1f} {row.stats.p95_ms:>8.1f}"
        )
    return "\n".join(lines)


def _render_recall(rows: Sequence[RecallRow]) -> str:
    lines = [_RECALL_HEADER, f"  {'suite':<18} {'facts':>6} {'recall@k':>9} {'ndcg@k':>8}"]
    for row in rows:
        lines.append(f"  {row.suite:<18} {row.n_facts:>6} {row.recall_at_k:>9.3f} {row.ndcg_at_k:>8.3f}")
    return "\n".join(lines)


def _render_affordance(rows: Sequence[AffordanceRow]) -> str:
    lines = [_AFFORDANCE_HEADER, f"  {'command':<10} {'records':>8} {'resolvable':>11} {'pct':>7}"]
    for row in rows:
        lines.append(f"  {row.command:<10} {row.total_records:>8} {row.resolvable:>11} {row.pct_resolvable:>6.1f}%")
    return "\n".join(lines)
