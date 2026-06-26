---
type: adr
id: ADR-018
title: Adopt dlt as the connector ingestion framework (phased, Protocol-gated)
status: superseded by outcome — Wave 1 shipped; Waves 2-3 abandoned after three negative dlt-fit findings (see "Wave 2 outcome" below). The original "adopt dlt" thesis is rejected; the productive scope was the in-process chunking that closes #321.
date: 2026-05-26
related:
  - connector-ingestion-architecture
  - feature-flag-architecture
  - test-discipline-hardening
---

# ADR-018: Adopt dlt as the connector ingestion framework

**Status:** Superseded — dlt rejected; the connector framework was built in-house (ADR-018-dlt direction not taken)
**Date:** 2026-05-26

---

## Decision

Migrate the connector ingestion framework (`kairix/core/connectors/{bronze,cursor,dead_letter,pipeline}.py` plus per-connector implementations under `kairix/connectors/<name>/`) from kairix-homegrown primitives to [**dlt**](https://dlthub.com/) (Apache-2.0, Python-native, SQLite + Postgres + DuckDB + S3 + many other destinations), behind the existing Protocol seams, in four iterative waves. Each wave ships behind a feature flag with F54 OFF/ON coverage and soaks in dogfood before cutover.

---

## Context

### The four production issues from 2026-05-24/25 that converged on this decision

1. **#317** — `/tmp` overflowed the OS disk during SharePoint binary downloads. Tactical: tmpfs default in compose. Underlying: nothing bounded scratch storage growth.
2. **#318** — Bronze writes that fsync'd but never committed accumulated as orphan files (36 GB on the production VM). Tactical: orphan reaper stage in the maintenance scheduler. Underlying: the bronze write contract assumed a sweeper that was never built.
3. **#316** — Bronze had no garbage collection at all. Tactical: TTL-based GC behind `bronze_ttl_gc` flag. Underlying: there is no concept of retention policy in the connector framework.
4. **#321** — The `ConnectorPipeline` runs an entire batch inside one SQLite transaction. SharePoint's `list_changes()` returns the whole drive in one iterator call, so a single backfill is ~6000 items / ~1.5 hours with no checkpoint. Any worker restart mid-batch rolls back every uncommitted bronze write while the on-disk blobs remain. Tactical: not yet attempted. Underlying: no per-item commit boundary and no connector-driven chunking.

All four are bugs the kairix-homegrown framework either created or failed to address. They share a root: kairix has been reinventing storage-layer primitives — atomic writes, transactional outbox, cursor checkpointing, retention policy, dead-letter — that battle-tested OSS libraries already solve.

### What kairix has been reinventing

| Kairix homegrown | dlt equivalent | What dlt fixes mechanically |
|---|---|---|
| `SourceConnector` Protocol + per-connector adapters | `@dlt.resource` decorator | Cursor-based incremental built in; configurable batch size |
| `CursorStore` + per-source delta tokens (`kairix/core/connectors/cursor.py`) | `dlt.sources.incremental("modified_at")` + `_dlt_pipeline_state` table | State persisted automatically; one less surface area to maintain |
| `FilesystemBronzeStore` + atomic write contract | dlt "load packages" — atomic write barrier per package | #318 (orphans) impossible by construction: package commits or rolls back, no half-state |
| `ConnectorPipeline._process_batch` + single-transaction-per-batch commit | `pipeline.run(resource, write_disposition=…, chunk_size=N)` | #321 (single-txn batches): bounded packages; SharePoint backfill checkpoints every N items |
| `DeadLetterStore` hand-rolled `(source, item_id, failure_count)` table + poison threshold | dlt's `_dlt_loads` + per-resource retry semantics + `LoadInfo.error` | Same observability without bespoke schema |
| Bronze TTL GC (#316) `bronze_ttl_gc` flag + `gc_aged` SQL DELETE | dlt's `write_disposition='replace'` + retention policy | Configurable per-resource; no bespoke flag |
| Bronze orphan reaper (#318) walking filesystem | Load packages are atomic — orphans can't exist | The whole class of bug doesn't apply |

### What is NOT in scope for this migration

- `SilverProcessor` — kairix-domain chunking + entity-signal extraction. Stays. dlt calls into it as a transformer.
- Search pipeline (BM25 + vector RRF) — kairix retrieval. Out of scope.
- MCP server + agent surfaces — kairix product. Out of scope.
- Extractor plugins (markitdown, pdf_fallback, OCR, pptx, docx, xlsx) — these run inside the connector pipeline but the Extractor Protocol stays as-is; dlt resources just call them.

---

## Test coverage audit (the safety net for the refactor)

The migration is only safe because the existing test discipline (BDD → E2E → Contract → Unit) already pins the behaviours we need to preserve. The audit below inventories what currently exercises each to-be-refactored component. Each wave's acceptance criteria is "all of the existing tests are green against the new dlt-backed implementation."

### Wave 1 surface — Bronze store (highest priority; closes #316 + #318 + #321)

**Production code being replaced:**
- `kairix/core/connectors/bronze.py` — `FilesystemBronzeStore` class
- Specifically: `write()`, `read()`, `replay()`, `reap_orphans()`, `gc_aged()`

**Tests pinning the contract:**

| Layer | Test | Lines | What it pins |
|---|---|---:|---|
| Contract | `tests/contracts/test_connector_protocols.py` | — | `BronzeStore` Protocol shape compliance (both real impl and `FakeBronzeStore`) |
| Unit | `tests/unit/test_connector_bronze_store.py` | 374 | `write` persists bytes + pointer row; idempotent on `(source, item_id)`; caller owns commit; `read` round-trip + raises on missing; `replay` ordering + `since` filter + scoped to source; `reap_orphans` deletes unreferenced, returns zero on clean, scoped to source, `min_age_seconds` protects in-flight; `gc_aged` deletes >TTL, preserves <TTL, refuses negative TTL, scoped to source |
| BDD | `tests/bdd/features/connector_bronze.feature` | 25 | write-with-pointer scenario, replay-in-fetch-order scenario, orphan-reaper scenario (`@atomicity @orphan @lifecycle`) |
| BDD | `tests/bdd/features/feature_flag_bronze_ttl_gc.feature` | 27 | OFF / ON both-branch coverage for `bronze_ttl_gc` flag |
| Integration | `tests/integration/test_feature_flag_bronze_ttl_gc.py` | — | OFF (intact) / ON (deletes backdated) / ON-but-fresh (preserves) — three scenarios |
| Integration | `tests/integration/test_pipeline_integration.py` | 733 | Multi-component bronze write inside the pipeline (real silver, real writer, real cursor) |
| E2E | `tests/e2e/test_composed_bronze_ttl_gc_path.py` | — | Composed production path: schema → seeded backdated + fresh rows → flag-gated TTL stage → asserts deletion + survival |
| E2E | `tests/e2e/test_composed_obsidian_connector_primary_path.py` | — | Obsidian E2E exercises bronze write through the full pipeline |
| E2E | `tests/e2e/test_composed_connector_sharepoint_path.py` | — | SharePoint E2E exercises bronze write through the full pipeline |
| Unit (scheduler) | `tests/core/maintenance/test_scheduler.py` | — | `bronze_reaper` and `bronze_ttl_gc` Deps + stages + exception isolation (3 new tests each from today) |

**Gaps to close before swapping (Wave 1 prerequisites):**

| Gap | Tactical add |
|---|---|
| No characterization test that asserts "8000-item simulated batch survives a worker restart mid-stream" (the #321 failure mode). | Add `tests/integration/test_connector_pipeline_long_batch_durability.py` — drives N items, simulates SIGTERM after item K, asserts (a) orphan count bounded by chunk_size, (b) cursor resumes from K. Currently this test would FAIL — that's the regression-lock we want. |
| No contract test that proves BronzeStore implementations are equivalent. | Add `tests/contracts/test_bronze_store_equivalence.py` — parametrised across `FilesystemBronzeStore` AND `DltBronzeStore`, runs the same scenarios. Today only Fakes are parameterised; this rule extends to real impls. |
| No fitness function that catches a new BronzeStore implementation drifting from the Protocol. | F43 (contract test required per-plugin) already partly covers this. Verify it triggers on `DltBronzeStore` once added. |

### Wave 2 surface — Cursor + dead-letter stores

**Production code being replaced:**
- `kairix/core/connectors/cursor.py` — `SqliteCursorStore`
- `kairix/core/connectors/dead_letter.py` — `SqliteDeadLetterStore`

**Tests pinning the contract:**

| Layer | Test | What it pins |
|---|---|---|
| Contract | `tests/contracts/test_connector_protocols.py` | `CursorStore`, `DeadLetterStore` Protocol shape |
| Unit | `tests/unit/test_connector_cursor_store.py` (138 LoC) | read/write/round-trip; missing-source returns None; idempotent overwrite |
| Unit | `tests/unit/test_connector_deadletter_store.py` (157 LoC) | record/is_poisoned/threshold; per-source isolation |
| BDD | `tests/bdd/features/connector_cursor.feature` | cursor advance + replay |
| BDD | `tests/bdd/features/connector_deadletter.feature` | dead-letter on 3rd failure; operator-listable |
| Integration | `tests/integration/test_pipeline_integration.py` | Cursor + dead-letter in the full pipeline |

**Gaps to close:** Same equivalence-contract pattern as Wave 1 (parametrise across kairix + dlt implementations).

### Wave 3 surface — Per-connector reshape as `@dlt.resource`

**Production code being replaced (one connector per sub-wave):**
- `kairix/connectors/sharepoint/connector.py` (broken first — start here)
- `kairix/connectors/obsidian/connector.py`
- `kairix/connectors/slack/connector.py`
- `kairix/connectors/github/connector.py`
- `kairix/connectors/notion/connector.py`
- `kairix/connectors/m365_email_headers/connector.py`
- `kairix/connectors/m365_calendar/connector.py`
- `kairix/connectors/dex_crm/connector.py`

**Tests pinning per-connector behaviour (each connector ships its own set):**

| Layer | Per-connector test |
|---|---|
| Contract | `tests/contracts/test_<name>_protocol.py` — paired fake + real, runs same scenarios |
| Unit | `tests/unit/test_<name>_connector_unit.py` (where present) — connector-specific edge cases |
| BDD | `tests/bdd/features/connector_<name>.feature` + steps + binding |
| Integration | `tests/integration/test_feature_flag_connector_<name>.py` — both-branch flag coverage |
| E2E | `tests/e2e/test_composed_connector_<name>_path.py` — composed-path proof |

**Gap to close per connector:** A "soak under realistic load" integration test that simulates the full delta stream (e.g. 5000 items) and asserts batch checkpointing. Today this only exists implicitly via dogfood; making it a deterministic test means we can run it in CI.

### Wave 4 surface — Retire homegrown framework code

**Production code being deleted:**
- `kairix/core/connectors/bronze.py` (after Wave 1 cutover)
- `kairix/core/connectors/cursor.py` (after Wave 2 cutover)
- `kairix/core/connectors/dead_letter.py` (after Wave 2 cutover)
- `kairix/core/connectors/pipeline.py` (after Wave 3 cutover)

**Tests to retire:** Only the implementation-specific unit tests for the deleted classes. Contract / BDD / E2E tests remain — they now exercise the dlt-backed implementations.

**Fitness functions to update:**
- F34 (kairix/core/connectors/** may not import connectors/ or extractors/) — adjust to allow `import dlt` at the framework layer
- F38 (Silver lives only in kairix/core/connectors/silver.py) — unchanged
- F40 (Extractor version mandatory) — unchanged, dlt resources call extractors
- F42 (Protocol method return types are frozen dataclasses) — unchanged, dlt outputs map to our frozen dataclasses

---

## Wave plan

Each wave: characterize → swap → soak → cutover. Tests stay green at every step; the only "red phase" is when we INTENTIONALLY add a failing characterization test (e.g. the long-batch-durability test) that the new implementation will make green.

### Wave 1 — Closes #321 by chunked-commit inside ConnectorPipeline (NOT dlt) — DONE 2026-05-26

**Pivot from the original ADR proposal**, recorded here for the audit trail:

The original Wave 1 proposed a `DltBronzeStore` adapter. A focused dlt evaluation (research notes in this ADR's commit history) showed that raw binary blobs + filesystem pointer rows are a **poor fit for dlt's row-into-table model**:

- dlt's `filesystem` destination only accepts `jsonl / insert_values / parquet / csv / model / reference` loader formats — there is no "raw bytes passthrough" path ([filesystem destination capabilities](https://dlthub.com/docs/dlt-ecosystem/destinations/filesystem))
- The custom `@dlt.destination` decorator's docs are explicit: *"Keeping the batch atomicity is on you ... you can still get duplicated data if you committed half of the batch"* ([destination.md atomic-loads section](https://dlthub.com/docs/dlt-ecosystem/destinations/destination#adjust-batch-size-and-retry-policy-for-atomic-loads))
- We would have written a custom dlt destination JUST to own the file+row atomicity, while bypassing every part of dlt that earns its keep (schema inference, evolution, normalization)

The actual root cause of #321 was simpler: `ConnectorPipeline._process_batch` ran the entire batch inside ONE SQLite transaction. The fix is to commit every `chunk_size` items inside the same pipeline. ~50 LoC. Closes #321 directly; #318 orphan reaper still handles the bounded per-chunk orphans.

Wave 1 actual delivery (ff5472a4, 2026-05-26):

1. **Characterization** — `tests/integration/test_connector_pipeline_long_batch_durability.py` two paired tests pinning #321. The xfail test became live PASS as soon as chunking shipped.
2. **Implementation** — `ConnectorPipeline.__init__(chunk_size=50)`; `_process_batch` extracted into `_process_change` + `_commit_and_flush` helpers under `_BatchTotals` + `_ChunkAccumulator` dataclasses. Cognitive complexity stays ≤15 per F16.
3. **Equivalence** — all 7969 existing unit/bdd/contract/integration tests green against the chunked code without modification. The chunking change is invisible to the API surface; only the transaction granularity changed.
4. **Soak** — VM deploy of the chunking fix lands in the next alpha cut following this ADR revision.

The dlt evaluation isn't wasted: Waves 2-3 still benefit from dlt for the layers where rows-into-tables IS the natural shape.

### Wave 2 — SKIPPED — cursor + dead-letter layers don't fit dlt

**Pivot from the original ADR proposal**, recorded for the audit trail:

The original Wave 2 proposed `DltCursorStore` and `DltDeadLetterStore` adapters. Closer reading of the existing code surfaced two reasons this doesn't fit:

1. **Transactional coupling.** `CursorStore.write` and `DeadLetterStore.record` issue SQL against the **same `sqlite3.Connection`** that `FilesystemBronzeStore.write` uses. The per-chunk commit in `ConnectorPipeline` commits cursor + dead-letter + bronze pointer rows in ONE transaction. dlt operates with its own connection + load-package lifecycle and would split this atomicity, opening a new class of bug (cursor advances but bronze didn't, or vice versa).

2. **dlt's cursor primitives are per-resource, not per-store.** `dlt.sources.incremental("modified_at")` lives inside a `@dlt.resource` function — it's how a connector's iterator tracks its position. It's not a swap-in for the `CursorStore` Protocol that the pipeline composes. Same for `_dlt_loads` — it tracks dlt's load packages, not the per-item retry semantics our `DeadLetterStore` records.

Net: keeping `CursorStore` + `DeadLetterStore` as the existing ~150-line SQLite-backed implementations is the right call. They do one thing well and the transactional coupling with chunked commits is a feature.

dlt's actual value lands at the per-connector resource layer (Wave 3), where structured pagination + cursor + state is exactly the shape `@dlt.resource` is designed for.

### Wave 2 outcome — ABANDONED — SharePoint dlt reshape doesn't fit either

Genuine implementation attempt (uncommitted code: `kairix/connectors/sharepoint/dlt_connector.py`, reverted after design check) surfaced the **third structural finding** that dlt isn't the right OSS library for kairix connectors:

- **dlt's resource state is bound to its pipeline lifecycle.** `dlt.current.resource_state()` is only meaningful when called from a resource that dlt's `pipeline.run()` is currently executing. Outside that context the state primitive is unavailable.
- **dlt is end-to-end or nothing.** dlt is designed to OWN the data flow from source to destination. Using only its state primitive (and discarding its data output by routing to a `/dev/null` destination) is fighting the framework, not adopting it.
- **SharePoint's deltaLink is opaque, not a timestamp.** dlt's `dlt.sources.incremental("field")` works on numeric/timestamp values for cursor advance. The SharePoint Graph API uses opaque deltaLink URLs. The fit is wrong at the cursor-shape level too, even before the state-primitive issue.

The three negative findings together (Wave 1 bronze, Wave 2 cursor/dead-letter, Wave 2-revised SharePoint connector) are a pattern. **dlt is the wrong OSS library for kairix's connector framework**, given kairix's architectural constraints:
- Raw binary blobs as a first-class output (not rows-into-tables)
- Per-chunk transactional coupling between bronze writes and cursor advance
- Opaque per-connector cursor shapes (deltaLink, RTM event positions, watermarks, OAuth state)

### Final decision — Wave 1 is the productive scope; abandon the rest

The original "adopt dlt" thesis is REJECTED. The four production issues that motivated this ADR are addressed as follows:

| Issue | Final resolution |
|---|---|
| #316 — bronze TTL GC | Shipped in v2026.5.25a1 (homegrown TTL behind `bronze_ttl_gc` flag). Not dlt. |
| #317 — `/tmp` overflow | Shipped in v2026.5.25a1 (tmpfs default in compose). Not dlt. |
| #318 — bronze orphan reaper | Shipped in v2026.5.25a1 (homegrown reaper as scheduler stage). Not dlt. |
| #321 — single-transaction-per-batch | Shipped in v2026.5.26a1 (chunked commits inside `ConnectorPipeline`, ~50 LoC). Not dlt. |

Total: four production issues closed by ~200 lines of new kairix code + one new feature flag. **Zero dlt code shipped.** The investigation cost (research + ADR + abandoned prototype) was real but bounded; the alternative (forcing dlt into a misfit) would have been worse.

### Future work (intentionally limited)

This ADR does NOT recommend a follow-up dlt evaluation. If a future ADR-019 wants to explore connector-framework consolidation against an OSS library, the candidates worth evaluating are:

- **Airbyte Python CDK** — designed for connector cursor diversity (OAuth state, deltas, watermarks). Heavier than dlt; closer fit for SaaS connectors with opaque cursors.
- **Singer specification** — JSON-pipe protocol with ecosystem of taps/targets. Heavy ops infrastructure (separate processes per connector).
- **Meltano** — Singer-based orchestrator.

The recommendation if any of these is explored: **start with a single existing connector and prove the fit before any ADR commitment**. The ADR-018 lesson is that a multi-wave commitment ahead of fit-validation costs investigation time on multiple negative findings.

### Engineering pattern recorded for the future

The right shape for "should we adopt OSS library X for layer Y?" decisions:

1. **Spike first, ADR second.** Implement the smallest meaningful integration (one resource, one Protocol implementation) against the real test surface. If it fits, write the ADR with the spike's evidence. If it doesn't fit, the negative finding is the artefact and the ADR is unnecessary.
2. **Three negative findings is a stop signal.** If the first integration attempt surfaces a design mismatch, evaluate carefully before pivoting to a different layer. Two failed pivots within the same library are a strong signal the library is wrong for the use case.
3. **Architectural constraints first, library second.** kairix's constraints (raw blobs, transactional coupling, opaque cursors) are real and not negotiable. Libraries that fight them aren't a fit even if they're best-in-class for other use cases.

---

## Risks + escape hatches

### Risk: dlt's SQLite destination doesn't match kairix's schema exactly

dlt creates its own metadata tables (`_dlt_*`). Our existing `documents`, `content`, `content_vectors`, `bronze_records` are kairix-domain. The adapter must:
- Write dlt's load packages to a `bronze/` schema/namespace
- Continue writing kairix-domain rows (documents, content, content_vectors) via the existing `chunk_writer`

**Escape:** dlt supports custom destinations (Python class). If the SQLite-native destination causes friction, write a thin custom destination that maps dlt's package boundary onto our existing tables. This is documented in dlt's docs.

### Risk: dlt's incremental cursor doesn't match our `(source_name, last_modified_at)` shape exactly

dlt uses `dlt.sources.incremental("field_name", initial_value=...)`. Our cursors are opaque strings (per-source format).

**Escape:** Adapter converts. If the conversion is lossy, we keep the cursor table separately in Wave 2 and only Wave 1 (bronze) migrates first.

### Risk: dlt adds Python dependencies kairix has been avoiding (e.g. pandas, pyarrow)

dlt is Python-native and the SQLite destination should be light. But dlt pulls in `requests`, `sqlglot`, etc.

**Escape:** Vendor-pin dlt and audit its transitive deps in pyproject. Same as we did for `usearch` and `sentence-transformers`. If a heavyweight dep is unavoidable, write a custom destination that doesn't load it.

### Risk: dlt's semantics don't actually solve #321 (single-txn batches)

If dlt's load packages internally use a single transaction per package, we've reinvented the same bug with more dependencies.

**Mitigation:** The characterization test (long-batch-durability) is the first thing we land. It FAILS against the current implementation. If it also fails against dlt, we know before any migration work. dlt's docs explicitly describe "load packages" as bounded write barriers — but trust-but-verify with the test.

### Risk: dlt's release cadence introduces breakage

dlt is active development; breaking changes possible.

**Mitigation:** Pin to a specific minor version in pyproject. Per the dependency-cooldown feedback memory, we don't take dlt updates until 7+ days after release. Bump deliberately, with the test suite as the safety net.

### Risk: Connector-specific edge cases (SharePoint deltaLink semantics, Slack RTM vs Socket Mode, etc.) don't fit dlt's resource model

**Mitigation:** Wave 3 is per-connector and reversible. Start with sharepoint (which is currently broken). If a connector genuinely doesn't fit, the flag stays OFF and that connector keeps its homegrown path until the next architecture review.

---

## Non-goals

- Replacing the silver processor (chunking + entity signals) — that's kairix-domain
- Replacing the search pipeline — out of scope
- Adopting dlt's destinations beyond SQLite — Postgres/DuckDB are future work, not in scope here
- Replacing the MCP server / agent surfaces — out of scope
- Migrating to Singer / Airbyte CDK / Meltano — dlt is the chosen library; alternatives evaluated and rejected (Singer requires separate processes / JSON pipes; Airbyte requires Docker/K8s; dlt is the only Python-library-native option that fits kairix's local-first constraints)

---

## Acceptance for the ADR

- Wave 1 ships with all existing tests green AND the new long-batch-durability test green (it was failing before)
- `bronze_backend: dlt` flag both-branch covered per F54
- #316 + #318 + #321 closed at the end of Wave 1
- #319 (test discipline meta) re-evaluated at the end of Wave 4 — most of the proposed F62/F63/F64 rules are satisfied by dlt's design rather than needing kairix-side enforcement

---

## What we are NOT doing in this ADR

- Committing to a specific Wave 3 ordering beyond "sharepoint first"
- Committing to dlt for non-connector subsystems (search, eval, etc.)
- Committing to retire the homegrown code on a fixed deadline — Wave 4 happens when Waves 1-3 have all soaked successfully

---

## References

- [`docs/architecture/connector-ingestion-architecture.md`](connector-ingestion-architecture.md) — the framework being phased out
- [`docs/architecture/feature-flag-architecture.md`](feature-flag-architecture.md) — the flag-cutover pattern this ADR uses
- [`docs/architecture/test-discipline-hardening.md`](test-discipline-hardening.md) — F45..F49 + composed-path discipline (the safety net)
- [`docs/architecture/fitness-functions.md`](fitness-functions.md) — F34/F35/F38/F40/F42/F43 (the rules that protect the Protocol seams during migration)
- GitHub issues #316, #318, #319, #321 — the four production observations that motivate this work
- [dlt project docs](https://dlthub.com/docs/intro)
