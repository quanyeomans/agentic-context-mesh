"""GH #334 — Curator-coupling boundary: SQLite ``entity_signals`` → Neo4j drain.

Public surface:

* :class:`Neo4jDrainer` — tick-driven component that drains the
  ``entity_signals`` staging table into Neo4j. F66-compliant
  (``per_tick_max_items``, ``disk_watermark_min_free_bytes``).
* :class:`NeoDrainResult` — frozen-dataclass envelope from one tick.
* :func:`run_neo4j_drain_tick` — free-function form for callers that
  want a one-shot drain without holding the class instance.

The connector pipeline stages every extracted ``EntitySignal`` into
``entity_signals`` (Wave 2 of the connector framework). The drain
implemented here is the Wave-3 Curator-coupling boundary that was
promised but never built — see GH #334 for the RCA. Until this module
landed, production accumulated 2.3M un-pushed signals over years.

Architectural fit:

* F26-clean — this module imports only protocols + ``kairix.knowledge.graph``
  (Neo4j wrapper), never ``kairix.providers.*`` or ``kairix.transport.*``.
* F66-compliant — :class:`Neo4jDrainer` declares ``per_tick_max_items``
  and ``disk_watermark_min_free_bytes`` even though F66's scanner does
  not currently include ``kairix/core/curator/``; the declaration is
  forward-armed against the day we add this tree to F66's scope.
"""

from __future__ import annotations

from kairix.core.curator.drain import (
    DEFAULT_DRAIN_BATCH_SIZE,
    MAX_PUSH_ATTEMPTS,
    Neo4jDrainer,
    NeoDrainResult,
    run_neo4j_drain_tick,
)

__all__ = [
    "DEFAULT_DRAIN_BATCH_SIZE",
    "MAX_PUSH_ATTEMPTS",
    "Neo4jDrainer",
    "NeoDrainResult",
    "run_neo4j_drain_tick",
]
