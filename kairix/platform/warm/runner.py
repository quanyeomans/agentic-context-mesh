"""Warm-up runner — pay the factory-init + first-search costs at startup.

An agent's first request pays ~192 MB of allocations + factory
wall-time (#279). This runner absorbs that cost so it lands BEFORE
``/healthz/ready`` flips to 200.

Steps:
    1. Build the SearchPipeline (factory: DB connections, Azure embed
       client, BM25 + vector backend init). Costs ~120 MB.
    2. Issue one no-op tool_search (populates per-call caches, builds
       query plan). Costs ~70 MB.
    3. Open the Neo4j client connection (small but waitable).

Never raises — each step is wrapped so a single failure populates a
WarmFailure entry but other steps still attempt to run. Caller decides
whether to flip ``/healthz/ready`` based on ``WarmResult.ok``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Step names — extracted as constants so the same literal isn't repeated
# across dispatch + skip + post-check sites.
_STEP_BUILD = "build_search_pipeline"
_STEP_PROBE = "probe_search"
_STEP_GRAPH = "open_graph_client"
_STEP_SQLITE_STATS = "ensure_sqlite_stats"


# Workload signature used for the no-op probe — short, lowercase, ASCII,
# unlikely to match real content in any vault. Tests assert this exact
# string is what the warm-up sends so a regression that changes the probe
# to operator-visible content (e.g. an actual term in a deployed vault)
# is caught.
WARMUP_QUERY = "__kairix_warmup_probe__"


@dataclass(frozen=True)
class WarmStep:
    """Outcome of one warm-up step."""

    name: str
    ok: bool
    duration_s: float
    detail: str = ""


@dataclass(frozen=True)
class WarmFailure:
    """One step that failed."""

    step: str
    detail: str


@dataclass(frozen=True)
class WarmResult:
    """Outcome of one ``run_warm`` invocation.

    Attributes:
        steps: per-step results in execution order.
        failures: structured failures for the non-ok steps.
        ok: True only when every step succeeded.
        total_duration_s: wall time across all steps.
    """

    steps: list[WarmStep] = field(default_factory=list)
    failures: list[WarmFailure] = field(default_factory=list)
    ok: bool = True
    total_duration_s: float = 0.0

    def to_envelope(self) -> dict[str, Any]:
        """Project to the JSON envelope CLI --json + MCP emit."""
        return {
            "ok": self.ok,
            "total_duration_s": self.total_duration_s,
            "steps": [{"name": s.name, "ok": s.ok, "duration_s": s.duration_s, "detail": s.detail} for s in self.steps],
            "failures": [{"step": f.step, "detail": f.detail} for f in self.failures],
        }


def _step_build_pipeline() -> Any:
    """Build the production search pipeline. Pays the ~120 MB factory cost."""
    from kairix.core.factory import build_search_pipeline

    return build_search_pipeline()


def _step_probe_search(pipeline: Any) -> Any:
    """Issue one no-op search through the warmed pipeline.

    Triggers per-call cache population + query-plan compilation that
    would otherwise land on the first agent request. Result is
    discarded — only side-effects matter.
    """
    return pipeline.search(query=WARMUP_QUERY, budget=500, scope="shared+agent")


def _step_ensure_sqlite_stats() -> Any:
    """Bootstrap SQLite query-planner statistics if missing.

    Idempotent: when ``sqlite_stat1`` is already populated this is a
    structural no-op. On a fresh install with > 0 documents it runs
    ``ANALYZE`` once so the planner picks the right index for hot-path
    queries (avoiding the idx_documents_active vs idx_documents_collection
    regression the 2026-06-02 production audit found).

    Lazy import keeps the warm runner importable on call sites that
    don't have ``sqlite3`` linked at module load.
    """
    import sqlite3

    from kairix.paths import KairixPaths
    from kairix.platform.warm.sqlite_stats import (
        DETAIL_SKIPPED_STATS_PRESENT,
        STEP_NAME,
        WarmStepResult,
        ensure_sqlite_stats,
    )

    paths = KairixPaths.resolve()
    db_path = paths.db_path
    # Fresh-install path: warm can run before any worker has bootstrapped
    # the DB file or its parent directory. Treat "no DB yet" as a skip
    # rather than a hard failure — the planner-stats bootstrap only makes
    # sense once ingest has written rows.
    if not db_path.exists() or not db_path.parent.exists():
        return WarmStepResult(
            name=STEP_NAME,
            ok=True,
            elapsed_ms=0.0,
            detail=DETAIL_SKIPPED_STATS_PRESENT,
        )
    # F77-allow: warm-up runs once per container before the worker loop owns its coordinator
    db = sqlite3.connect(str(db_path))
    try:
        return ensure_sqlite_stats(db, paths)
    finally:
        db.close()


def _step_open_graph_client() -> Any:
    """Open the Neo4j driver connection so the first entity lookup is fast.

    Returns the client whether or not Neo4j is reachable — soft-fail
    semantics so the warm-up doesn't block on an optional subsystem.

    Importing neo4j costs ~22 MB on the Python heap, so when no Neo4j URI
    is configured we skip the load entirely — the entity-graph subsystem
    is auxiliary and gracefully degrades when absent. Operators who have
    Neo4j configured pay the cost (and get a warmed driver); operators
    who don't save the heap.
    """
    from kairix.secrets import neo4j_uri_configured

    if not neo4j_uri_configured():
        return None
    from kairix.knowledge.graph.client import get_client

    client = get_client()
    _ = client.available
    return client


def _time_step(name: str, fn: Callable[[], Any]) -> tuple[WarmStep, Any]:
    """Run one warm-up step under a timer; return the step record + result.

    Catches everything so a single subsystem failure (e.g. Neo4j down)
    doesn't fail the whole warm-up. Errors land in WarmStep.ok=False
    with the exception class + message in ``detail``.
    """
    t_start = time.perf_counter()
    try:
        result = fn()
        duration = round(time.perf_counter() - t_start, 3)
        return WarmStep(name=name, ok=True, duration_s=duration), result
    except Exception as exc:
        duration = round(time.perf_counter() - t_start, 3)
        logger.warning("warm step %s failed: %s", name, exc, exc_info=True)
        return (
            WarmStep(name=name, ok=False, duration_s=duration, detail=f"{type(exc).__name__}: {exc}"),
            None,
        )


def _emit_progress(callback: Callable[[str], None] | None, stage_name: str) -> None:
    """Fire ``callback(stage_name)`` if provided, swallowing exceptions.

    Progress reporting is an observability seam — it must never abort
    the warm-up if a downstream callback misbehaves. Tests assert the
    callback is invoked; production wires the WarmProgress holder update.
    """
    if callback is None:
        return
    try:
        callback(stage_name)
    except Exception as exc:
        logger.warning("warm progress callback for stage %s raised: %s", stage_name, exc, exc_info=True)


def run_warm(
    *,
    pipeline_builder: Callable[[], Any] | None = None,
    search_probe: Callable[[Any], Any] | None = None,
    graph_client_opener: Callable[[], Any] | None = None,
    sqlite_stats_ensurer: Callable[[], Any] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> WarmResult:
    """Run all warm-up steps and return a structured result.

    Args:
        pipeline_builder: injectable; tests pass a fake to avoid spinning
            up the full search pipeline. Production omits.
        search_probe: injectable; tests pass a no-op that accepts the
            pipeline argument and returns immediately.
        graph_client_opener: injectable; tests pass a fake.
        sqlite_stats_ensurer: injectable; tests pass a fake that drives
            the ANALYZE bootstrap without opening a real SQLite
            connection. Production omits.
        progress_callback: optional one-arg callable invoked with the
            stage name each time a step completes (success or failure).
            Default ``None`` — preserves prior behaviour. The CLI wires a
            callback that appends the stage to the live
            :class:`kairix.platform.warm.state.WarmProgress` so the
            ColdStart envelope surfaces real elapsed/remaining time (#390).

    Returns:
        WarmResult. Never raises — top-level errors populate the
        per-step ``detail`` and ``ok=False``.
    """
    from kairix.platform.warm.state import mark_warm, mark_warming

    build = pipeline_builder or _step_build_pipeline
    probe = search_probe or _step_probe_search
    open_graph = graph_client_opener or _step_open_graph_client
    ensure_stats = sqlite_stats_ensurer or _step_ensure_sqlite_stats

    mark_warming()
    t_total_start = time.perf_counter()
    steps: list[WarmStep] = []

    step_build, pipeline = _time_step(_STEP_BUILD, build)
    steps.append(step_build)
    _emit_progress(progress_callback, _STEP_BUILD)

    if pipeline is not None:
        step_probe, _ = _time_step(_STEP_PROBE, lambda: probe(pipeline))
        steps.append(step_probe)
    else:
        steps.append(
            WarmStep(
                name=_STEP_PROBE,
                ok=False,
                duration_s=0.0,
                detail="skipped because build_search_pipeline failed",
            )
        )
    _emit_progress(progress_callback, _STEP_PROBE)

    step_stats, stats_result = _time_step(_STEP_SQLITE_STATS, ensure_stats)
    # When the ensurer returned a WarmStepResult-shaped object, hoist its
    # ``detail`` into the WarmStep so operators see "ANALYZE complete" /
    # "stats already present, skipped" in the envelope without having to
    # cross-reference a separate field.
    detail_attr = getattr(stats_result, "detail", None) if stats_result is not None else None
    if detail_attr:
        step_stats = WarmStep(
            name=step_stats.name,
            ok=step_stats.ok,
            duration_s=step_stats.duration_s,
            detail=str(detail_attr),
        )
    steps.append(step_stats)
    _emit_progress(progress_callback, _STEP_SQLITE_STATS)

    step_graph, _ = _time_step(_STEP_GRAPH, open_graph)
    steps.append(step_graph)
    _emit_progress(progress_callback, _STEP_GRAPH)

    total_duration = round(time.perf_counter() - t_total_start, 3)
    failures = [WarmFailure(step=s.name, detail=s.detail) for s in steps if not s.ok]

    result = WarmResult(
        steps=steps,
        failures=failures,
        ok=not failures,
        total_duration_s=total_duration,
    )
    # The graph step soft-fails so we accept warm without it — the
    # load-bearing path is search + probe.
    if result.steps[0].ok and any(s.ok for s in result.steps if s.name == _STEP_PROBE):
        mark_warm()
    return result
