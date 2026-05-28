# Defect catalogue — production failures and the F-rules that catch them

Every production-impact defect named here gets a row. The row names the **defect class** (per ADR-024) and the **F-rule** that would catch a recurrence of the same shape at gate time. If a defect's row has no F-rule, the bottom of this file lists it as a proposed F-rule for the next discipline-hardening wave.

The discipline: **post-mortems must name which F-rule catches the defect's class**. If no rule catches it, the post-mortem proposes the new F-rule in the same PR. The catalogue is the running record.

## Recent defects (2026-05)

| # | Defect | Production impact | Class | F-rule (catches at gate time) |
|---|---|---|---|---|
| Bug 1 | Connector cursor stuck on full-resync | 8,783 SharePoint items re-fetched every 15 min | scale + state-management | F62 (multi-tick idempotency test) |
| Bug 2 | SharePoint 429 dead-lettered every item | Whole drive dead-lettered on rate-limit | failure-injection at HTTP boundary | **F68** (Protocol failure-injection contract — `unavailable` / `times_out` class) AND F64 (external HTTP rate-limit test) |
| Bug 3 | Unbounded `_prune_orphans` LIMIT | Disk IO saturation at 2.1M-row scale | scale-bound iteration | F63 (LIMIT or rationale on `.fetchall()`) AND **F69** (10⁴-row variant on iteration tests — ADR-024 Bundle D, pending) |
| [#335](https://github.com/three-cubes/kairix/issues/335) | Embed worker OOM at 1.27M vectors | Worker in 10-min restart loop | scale + resource ceiling | **F69** (10⁴-row variant) AND ADR-019 (cgroup ceilings) AND ADR-023 (vector-index write architecture) |
| [#334](https://github.com/three-cubes/kairix/issues/334) | Neo4j entity-graph drain never built | 2.3M signals stuck across 4 years | schema-writer symmetry | **F67** (staging-table drain symmetry — landed this session) |
| [#336](https://github.com/three-cubes/kairix/issues/336) | `documents_media` table never written | Per-extractor analytics blank since Wave 1 | schema-writer symmetry | **F70** (generalised schema-writer symmetry — ADR-024 Bundle B, landed) |
| #334 / preflight masking | `_check_entity_signals_staging_not_stuck` reported count=1000 regardless of true scale | Operator never saw 2.3M backlog | preflight truthfulness | **F71** (preflight count == ground-truth COUNT(*) — ADR-024 Bundle C, landed) |
| (SharePoint) | ~5,200 SP items in bronze-but-not-content limbo | Documents fetched + not chunked + not dead-lettered | cross-layer integrity | **F72** `bronze_coverage_parity` invariant (ADR-024 Bundle E, pending) |

## F-rule coverage status

| F-rule | Status | Defect class it catches |
|---|---|---|
| F62 | Shipped earlier (pre-this-session) | Multi-tick idempotency regressions (Bug 1) |
| F63 | Shipped earlier | Unbounded `.fetchall()` at scale (Bug 3) |
| F64 | Shipped earlier | External HTTP rate-limit handling (Bug 2 — partial) |
| F67 | Shipped this session | Staging table without drain (#334) |
| **F68** | Shipped this session (Bundle A) | Protocol failure-injection coverage (Bug 2 — full) |
| **F70** | Shipped this session (Bundle B) | Schema declared without writer (#336 + #338 + #339 sibling backlog) |
| **F71** | Shipped this session (Bundle C) | Preflight masking (#334 LIMIT-1000 anti-pattern) |
| **F72** | In flight (Bundle E) | Cross-layer integrity invariants (~5,200-limbo class) |
| **F69** | In flight (Bundle D, after E + F) | Scale-bound iteration tests (Bug 3, #335) |
| Soak tier | In flight (Bundle F) | Behaviour at production-scale fixtures (#335 OOM was unreproducible at fixture scale) |

## Proposed F-rules (no current rule covers these classes — escalate when a future defect surfaces)

| Proposed | Class | When to propose |
|---|---|---|
| F73 — alpha-deploy gate failure-mode classification | The alpha-deploy webhook currently fails on `chunk_date_populated` (a known-stale-data signal) and can't distinguish that from a real deploy regression. New rule: every check in `kairix onboard check` declares whether it's a "deploy blocker" (regression) or a "data quality signal" (operator backlog item). The alpha-deploy gate only blocks on the former. | If alpha gate fires on data-not-code issue more than once after this catalogue lands. |
| F74 — operator runbook freshness | Runbooks reference specific code paths / files / env vars; when the code shape changes the runbook can rot silently. New rule: every `docs/operations/runbooks/*.md` whose body grep-matches `kairix/...py` or `KAIRIX_*` env var must have at least one mention of those paths still resolvable in the current source tree. | If a runbook is observed to be wrong because the underlying code changed without the runbook being updated. |

## How to add a row when you ship a fix

When you commit a defect fix:

1. Add a row to "Recent defects" with the defect, impact, class, F-rule
2. If no F-rule catches the class: add to "Proposed F-rules" with the trigger condition for proposing the new rule
3. Update the F-rule coverage status table when the rule lands
4. Link the row's # column to the GH issue if the defect has one

The catalogue is canonical. Future post-mortems start here.
