# ADR-020 — Connector tick budget + disk-watermark gating

**Status:** Accepted 2026-05-28
**Issues:** #328 (logged 2026-05-27); 2026-05-27 morning saturation incident
**Related:** ADR-019 (infrastructure ceiling — defence-in-depth that pairs with this application ceiling); ADR-018 (DLT connector framework — the layer this constrains); ADR-021 (per-source metadata — sibling Wave E.5 work)

## Context

`ConnectorPipeline.run_batch` and `MaintenanceScheduler.tick` were both unbounded in how much work they did per invocation. The pipeline drained every `ChangeEvent` returned by `connector.list_changes(cursor)` — however many that was. The scheduler scanned every orphan row returned by a `LEFT JOIN` — however many that was.

At small scale (a few hundred items, a few thousand orphan rows) both were fine. At production scale:

- The 2026-05-27 morning incident saw a stale-cursor scenario force the SharePoint connector to return all 8,783 items in one `list_changes` call. The pipeline drained them serially for hours, holding `sdb` at 96% util. (Fixed at the cursor-write layer by Bug 1 in v2026.5.28; this ADR addresses the residual structural gap that allowed a single tick to take that long.)
- The maintenance `_prune_orphans` was already fixed in v2026.5.28 (F63 — added `LIMIT` per tick). This ADR generalises that pattern to the connector pipeline + every other tick-driven component.

Even with cursor-write fixed, the **first sync of a large corpus** or any **recovery scenario** (cursor expired, schema migration, prolonged downtime) hits the same pattern: one tick has to drain N items. For N=100k items at ~1-2 items/sec extract latency, that's a 14-28 hour single-tick run with no checkpoint, no yield, no operator backpressure signal.

## Decision

Every tick-driven component in `kairix/core/connectors/` and `kairix/core/maintenance/` declares two ceilings:

1. **`per_tick_max_items: int`** — maximum unit-of-work items the component processes before yielding back to the worker loop. The component MUST commit its cursor at the budget boundary and return; the worker loop dispatches the next tick on its normal cadence. Default 500 items per tick.

2. **`disk_watermark_min_free_bytes: int | None`** — if set, the component MUST check free disk on `/data` (or its configured working volume) at tick start. If free bytes < watermark, the tick yields immediately without doing work. Default None (no gating); operators on small disks override to e.g. `5 * 1024**3` (5 GiB) to prevent disk-full during heavy ingest.

### Implementation shape

```python
class SourceConnector(Protocol):
    name: str
    per_tick_max_items: int  # default 500 via dataclass field
    disk_watermark_min_free_bytes: int | None  # default None
    ...

class ConnectorPipeline:
    def run_batch(self, connector: SourceConnector, extractor: Extractor) -> BatchResult:
        # Watermark gate
        if connector.disk_watermark_min_free_bytes is not None:
            free = self._disk_free_resolver()
            if free < connector.disk_watermark_min_free_bytes:
                logger.info("watermark_skip name=%s free=%d min=%d", connector.name, free, connector.disk_watermark_min_free_bytes)
                return BatchResult(processed=0, dead_lettered=0, poisoned_skipped=0, watermark_skipped=True)
        # Tick body
        cursor = self._cursor_store.read(connector.name)
        items_processed = 0
        for change in connector.list_changes(cursor):
            self._process_change(connector, extractor, change, totals, chunk)
            items_processed += 1
            if items_processed >= connector.per_tick_max_items:
                self._commit_and_flush(connector, totals, chunk)
                return BatchResult(processed=totals.processed, dead_lettered=totals.dead_lettered, poisoned_skipped=totals.poisoned_skipped, budget_yielded=True)
        # Drain completed within budget — final flush
        self._commit_and_flush(connector, totals, chunk)
        return BatchResult(...)
```

`WorkerDeps` gains:

```python
@dataclass
class WorkerDeps:
    ...
    disk_free_resolver: Callable[[], int] = field(default_factory=lambda: lambda: shutil.disk_usage("/data").free)
```

Tests inject a fake resolver returning a configurable byte count. F1-clean (no monkey-patch); F2-clean (no env override).

### Per-connector overrides

Connectors with naturally large items (Slack thread fetches, GitHub blob downloads) may set lower budgets:

```python
class SlackConnector:
    per_tick_max_items = 100  # 8 KB messages but tree traversal is expensive
    disk_watermark_min_free_bytes = 5 * 1024**3  # 5 GiB — attachments can be large
```

Connectors with light items (Obsidian markdown files, dex_crm rows) inherit the default 500.

## Alternatives considered

**A. No application ceiling; rely on ADR-019 infrastructure ceiling only.**
Rejected. Infrastructure ceilings stop saturation but don't fix the long-tick UX problem (operator sees worker doing the same source for 14 hours straight, no progress visibility, no ability to interrupt cleanly). A bounded tick model is also necessary for ADR-019's defaults to be meaningful — without it, the worker spends every tick at its CPU cap, just slowly.

**B. Time-based budget (`max_seconds_per_tick`) instead of item-based.**
Rejected. Per-item latency varies 100x between source types (a Slack message extract is ~50ms; a SharePoint PDF OCR is 30s+). A 60-second time budget for Slack means thousands of items; for SharePoint OCR it means 2. Item count is a more meaningful unit of "work done" across connector types.

**C. Hybrid (both item count AND time budget).**
Deferred. Item count is sufficient for the observed failure mode. If a future incident shows item count alone isn't enough (e.g. one item taking 10+ minutes), add `per_tick_max_seconds` as a sibling field. F66 fitness function then enforces both.

**D. Disk watermark via cgroup-v2 device limits instead of application-level check.**
Rejected. cgroup-v2 device limits are absolute throughput caps, not free-space gates. The watermark is a "should I do work" signal, not a "throttle my work" signal. Application-level check is the right shape.

**E. Adaptive backoff based on observed latency.**
Deferred to a later ADR. Adaptive systems are harder to reason about; we start with declarative ceilings and add adaptivity only if static defaults prove insufficient.

## Acceptance criteria

- [ ] `SourceConnector` Protocol gains `per_tick_max_items: int = 500` + `disk_watermark_min_free_bytes: int | None = None` (default impls; all existing connectors inherit defaults without change).
- [ ] `ConnectorPipeline.run_batch` honours `per_tick_max_items` — commits cursor at boundary, returns `BatchResult` with `budget_yielded=True`.
- [ ] `WorkerDeps.disk_free_resolver` exists with `shutil.disk_usage("/data").free` default; tests inject a fake via constructor kwarg.
- [ ] F66 fitness function blocks any new connector OR tick-driven component that doesn't declare both ceilings.
- [ ] BDD scenarios:
  - `connector_pipeline_per_tick_budget_drains_over_multiple_ticks.feature` — backlog of 1500 items at budget 500 drains over exactly 3 ticks with cursor advancing per tick.
  - `connector_pipeline_watermark_gate_skips_tick.feature` — free bytes below watermark → tick yields with `watermark_skipped=True`; bronze + cursor untouched.
- [ ] Integration test asserts the worker loop wakes the connector on its normal cadence after a `budget_yielded` return — recovery converges over multiple ticks.
- [ ] `docs/architecture/connector-ingestion-architecture.md` §10 wave plan updated to insert Wave E.5 between current E and F (Wave E.5 ships this ADR's primitives).

## Operational implications

**Recovery scenarios scale predictably**: a 100,000-item first-sync converges over `100000 / 500 / 4 = 50` hours of 15-min ticks. That's the same total wall-clock as a single 50-hour tick would be, but with continuous progress visibility, restartability, and host stability.

**Operators see budget yields as a signal**: log line `tick_yielded_at_budget name=sharepoint items=500 cursor_pos=<token>` is the actionable signal that a backlog is draining over many ticks. Operators can tune `per_tick_max_items` upward on quiet ticks to drain faster, or downward on busy ticks to share IO with neo4j / kairix-1.

**Watermark gating prevents disk-full incidents**: an operator with 4 GiB free who sets `disk_watermark_min_free_bytes = 5 * 1024**3` gets a clean `watermark_skip` log line instead of a partial-disk-write crash.

## Pairing with ADR-019

ADR-019 is the **infrastructure ceiling**: bounds what a saturated worker can do to the host. ADR-020 is the **application ceiling**: bounds what a healthy worker tries to do per tick. Both are required for production-readiness — ADR-019 protects against bugs in this ADR's implementation; ADR-020 protects against single-tick UX problems ADR-019 doesn't address.

## Migration

- Wave E.5 ships the implementation (per-connector + pipeline + WorkerDeps + tests).
- Existing connectors inherit `per_tick_max_items = 500` by default; per-connector overrides land as part of Wave E.5 (Slack, GitHub, SharePoint get tuned values).
- F66 baseline grandfathers existing connectors until their override is declared; F49 paydown removes baseline entries as connectors are tuned.
