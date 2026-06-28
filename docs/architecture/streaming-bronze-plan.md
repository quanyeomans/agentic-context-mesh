# Streaming Bronze Architecture — Implementation Plan

**Status:** Implemented · **Targeted release:** v2026.6.x (multi-week effort across several alphas)
**Author:** kairix engineering · **Date opened:** 2026-05-26

## 1. Problem

The current `FilesystemBronzeStore` persists every fetched raw byte to disk as a permanent blob, indexed by a `bronze_records` row. The v2026.5.27a2 SharePoint dogfood produced **112 GB of bronze for 8,783 items** (avg 13 MB/item — driven by PowerPoint and PDF binary sizes). Production deployments cannot assume that scale of spare disk:

- A 50,000-item corpus would project to **~650 GB**, exceeding typical operator disk allocations
- A 200,000-item corpus would exceed **2.5 TB**, requiring expensive enterprise storage tiers
- The `bronze_ttl_gc` flag (#316) is a partial mitigation but peak usage during a backfill is unbounded — fetch enumerates every item before TTL is meaningful
- Compressed bronze would buy 30-50% (PPTX/PDF binary compression ratios) — not transformative

The user's framing is correct: **kairix cannot ship as production-ready while assuming hundreds of GB of "wasted available expensive disk space waiting to be filled."**

## 2. Decision

**Adopt streaming bronze as the default.** Raw bytes flow from connector → extractor → silver in-memory without persistent disk staging. `bronze_records` retains a metadata row (`source_uri`, `mime`, `fetched_at`, `content_hash`, `extractor_version`) but no `raw_path`.

Re-extract recovery (the Bug D path that motivated keeping raw bytes) routes through `connector.fetch(item_id)` to re-pull from source. The trade-off is:

| | FilesystemBronzeStore (current) | StreamingBronzeStore (target) |
|---|---|---|
| Disk per item | ~MB (raw blob size) | ~kB (metadata only) |
| Re-extract speed | Fast (local disk read) | Slower (re-fetch from source) |
| Re-extract availability | Always (raw preserved) | Source-dependent (deleted/rotated items lost) |
| API cost on re-extract | Zero | Re-fetch = same cost as initial fetch |
| Audit trail | Full byte-level | Metadata + content_hash (re-fetch to verify) |

The re-fetch cost is acceptable because **re-extract is operationally rare**. Bug D recovery happens after a Dockerfile fix or extractor version bump — episodic events, not a hot path. The disk savings are continuous and bound the architecture to commodity-disk deployments.

The architecture remains *replayable* — operators retain the ability to recover from extractor bugs, just by re-fetching rather than by re-reading staged blobs.

## 3. Architectural mapping

### Current data flow (FilesystemBronzeStore)

```
connector.fetch(item_id) → RawArtefact(raw, mime, ...)
  ↓
  ├── bronze.write(...) → persists raw_bytes to disk + bronze_records row [PERSISTENT 13MB]
  └── extractor.extract(raw, mime) → ExtractedDocument             [in-memory copy of bytes]
       ↓
       silver.process(ref, doc, ...) → SilverOutput
       ↓
       chunk_writer.upsert(chunks) + entity_graph_sink.stage(signals)
```

### Target data flow (StreamingBronzeStore)

```
connector.fetch(item_id) → RawArtefact(raw, mime, ...)
  ↓
  ├── streaming_bronze.write(...) → bronze_records row ONLY (no raw_path)  [PERSISTENT ~kB]
  └── extractor.extract(raw, mime) → ExtractedDocument                      [in-memory bytes discarded after extract]
       ↓
       silver.process(ref, doc, ...) → SilverOutput
       ↓
       chunk_writer.upsert(chunks) + entity_graph_sink.stage(signals)
```

The connector → extractor pipe stays in-memory. Raw bytes are discarded after extract returns. The bronze_records row preserves enough to (a) detect duplicates by `content_hash`, (b) re-fetch by `(source_name, item_id)`, (c) audit `fetched_at`.

### Re-extract flow change

**Current (Bug D):**
```python
row = bronze_records.lookup(source_name, item_id)
raw_bytes = bronze.read(BronzeRef(...))                 # local disk read
doc = extractor.extract(raw_bytes, row.mime)
```

**Target:**
```python
row = bronze_records.lookup(source_name, item_id)
raw_artefact = connector.fetch(item_id)                 # re-fetch from source
doc = extractor.extract(raw_artefact.raw, raw_artefact.mime)
```

The Bug D code path becomes ~3 lines simpler. Failure mode flips from "raw file missing on disk" (skipped_no_bronze counter) to "source removed the item or auth failed on re-fetch" (skipped_source_unavailable counter).

## 4. Protocol changes

### `BronzeStore` Protocol (`kairix/core/protocols.py`)

No Protocol changes required. `StreamingBronzeStore` satisfies the existing Protocol:

- `write(source_name, item_id, raw, mime) -> BronzeRef` — implementation ignores `raw` (does NOT persist), returns a `BronzeRef` whose `raw_path` is empty/sentinel
- `read(ref) -> tuple[bytes, MimeType]` — raises `BronzeNotPersistedError` (new) so the re-extract path knows to route through re-fetch
- `replay(source_name, since)` — yields `BronzeRef`s from `bronze_records` rows (no on-disk dependency)

The `BronzeRef.raw_path` field becomes optional (`str | None`). Existing callers that read it (only Bug D) handle the None case by re-fetching.

### `bronze_records` schema

Add `content_hash` column (SHA-256 of raw bytes at fetch time) so duplicate detection and re-fetch verification work without on-disk raw. Existing columns retained.

```sql
ALTER TABLE bronze_records ADD COLUMN content_hash TEXT;
```

Idempotent for existing deploys; populated lazily.

### `BronzeRef` value object

```python
@dataclass(frozen=True)
class BronzeRef:
    source_name: str
    item_id: str
    raw_path: str | None  # was str; None signals streaming bronze
    mime: MimeType
    fetched_at: datetime
    content_hash: str | None = None  # added field; None on legacy rows
```

Migration: `raw_path` becomes nullable. Existing rows (FilesystemBronzeStore writes) keep their populated `raw_path`; new streaming writes set `raw_path=None`. Per F42 the dataclass stays frozen.

## 5. Implementation phases

### Phase 1 — Add `StreamingBronzeStore` alongside `FilesystemBronzeStore` (1 commit)

**Files:**
- `kairix/core/connectors/streaming_bronze.py` — new module, `StreamingBronzeStore` class
- `kairix/core/connectors/__init__.py` — export the new class
- `tests/unit/test_streaming_bronze.py` — Protocol-compliance + behaviour tests
- `tests/contracts/test_bronze_protocols.py` — assert both stores satisfy the BronzeStore Protocol identically

**Acceptance:**
- Both stores satisfy `runtime_checkable` BronzeStore Protocol
- `StreamingBronzeStore.write` inserts the metadata row, does NOT touch disk
- `StreamingBronzeStore.read(ref)` raises `BronzeNotPersistedError` (operator-readable message: "fix: route through `connector.fetch(item_id)` instead of `bronze.read`")
- Sabotage proof: replace `BronzeNotPersistedError` raise with `return (b"", "")` → tests fail because the empty-bytes path doesn't surface "you can't read from streaming bronze"

### Phase 2 — Add `content_hash` to BronzeRef + schema migration (1 commit)

**Files:**
- `kairix/core/db/schema.py` — `bronze_records.content_hash TEXT` column added
- `kairix/core/db/migrations/<n>_add_bronze_content_hash.sql` — idempotent ALTER TABLE
- `kairix/core/protocols.py` — `BronzeRef.content_hash: str | None`
- Existing call sites — compute SHA-256 at write time, populate column
- Tests — unit + integration confirming hash populated on new writes; existing rows unaffected

**Acceptance:**
- `BronzeRef.content_hash` populated on every new write (both stores)
- Legacy rows with `content_hash IS NULL` still parse without error
- Sabotage proof: skip the hash compute → test asserts `content_hash != None` on the freshly-written row

### Phase 3 — Make `BronzeRef.raw_path` nullable (1 commit)

**Files:**
- `kairix/core/protocols.py` — type change + docstring update explaining None semantics
- `kairix/core/connectors/bronze.py` — `FilesystemBronzeStore.read` raises if `ref.raw_path is None`
- `kairix/worker.py` — `_reextract_rows` handles `ref.raw_path is None` by routing through `connector.fetch`
- Tests — both branches of the `raw_path is None` check covered

**Acceptance:**
- Existing FilesystemBronzeStore deploys unchanged (raw_path always populated)
- Mixed-mode deploys (some streaming rows, some persisted) work — re-extract picks the right path per-row
- Sabotage proof: drop the `if ref.raw_path is None: re-fetch` branch → tests fail because re-extract throws BronzeNotPersistedError instead of recovering

### Phase 4 — Wire `StreamingBronzeStore` into worker via `bronze_mode` config (1 commit)

**Files:**
- `kairix/core/connectors/registry.py` — add `build_bronze_from_entry()` mirror of `build_extractor_from_entry`
- `kairix/worker.py` — `_run_one_connector_batch` + `_build_reextract_components` route through the helper
- `kairix.example.config.yaml` — operator-facing comment explaining `bronze_mode: streaming` opt-in
- `tests/unit/test_build_bronze_from_entry.py` — config-precedence tests
- `tests/bdd/features/bronze_streaming.feature` — F45 BDD feature for the new capability

Config shape:
```yaml
connectors:
  - name: sharepoint
    bronze_mode: streaming     # default 'filesystem' for backward compat
    extractor_chain: [markitdown, pdf_fallback, ocr]
```

**Acceptance:**
- Operators with no `bronze_mode` field get FilesystemBronzeStore (backward compatible)
- `bronze_mode: streaming` builds StreamingBronzeStore
- End-to-end smoke: configure a fake connector with streaming bronze, run a batch, assert no files on disk under `bronze_root/<name>/`
- Sabotage proof: invert the routing so streaming is always picked → existing tests with `bronze_mode: filesystem` fail because they observe missing on-disk blobs

### Phase 5 — Update Bug D re-extract path to handle streaming rows (1 commit)

**Files:**
- `kairix/worker.py` — `_reextract_rows` detects `ref.raw_path is None`, calls `connector.fetch(item_id)` instead of `bronze.read(ref)`
- `ReextractResult` adds counter `skipped_source_unavailable` (re-fetch raised, e.g. item deleted from source)
- `tests/test_worker_reextract.py` — new test cases for streaming-row re-extract (happy + source-unavailable)

**Acceptance:**
- Re-extract works against streaming-bronze rows (re-fetch + extract + commit)
- Counter increments when re-fetch raises
- Sabotage proof: replace the re-fetch branch with `raise NotImplementedError` → streaming-row re-extract fails the test

### Phase 6 — F30 outcome test for streaming bronze (1 commit)

**Files:**
- `tests/integration/test_outcome_streaming_bronze.py` — subprocess invocation of `kairix worker reextract` against a streaming-mode config + asserted JSON envelope

**Acceptance:**
- Subprocess-driven end-to-end test passes
- Envelope shape includes the new `skipped_source_unavailable` counter
- Sabotage proof per F30: mutate the production handler to emit wrong field names → outcome assertion fails

### Phase 7 — Default `bronze_mode` to streaming + remove FilesystemBronzeStore (1 commit)

**When this fires:** as soon as dogfood validation confirms streaming bronze is equivalent on the four observable axes:
1. **Ingest correctness** — full SharePoint sync produces the same chunks (count + content hash) under streaming vs filesystem on a small N-item slice
2. **Re-extract works** — Bug D path executes via re-fetch and produces equivalent ExtractedDocument output
3. **Disk usage drops as projected** — ~6000× reduction visible on `df` post-sync
4. **No source-API surprise** — re-fetch latency stays inside source rate limits

This is **not** a calendar-driven deprecation. kairix is pre-release; there's no installed user base running FilesystemBronzeStore in production that needs a migration window. Validation is fast (one dogfood cycle), so default-flip + legacy removal land in the same commit once the four signals are green.

**Files:**
- `kairix/core/connectors/registry.py` — `build_bronze_from_entry` defaults to streaming; the `bronze_mode: filesystem` opt-out branch is removed (along with the `FilesystemBronzeStore` reference)
- `kairix/core/connectors/bronze.py` — **deleted** (FilesystemBronzeStore removed)
- `kairix/core/connectors/__init__.py` — drop the `FilesystemBronzeStore` export
- `tests/unit/test_filesystem_bronze*.py` — **deleted** (no longer the production path)
- `tests/contracts/test_connector_protocols.py` — drop the FilesystemBronzeStore-specific contract tests (StreamingBronzeStore's Protocol-compliance tests remain)
- `kairix.example.config.yaml` — drop the `bronze_mode` field entirely (streaming is just how it works)
- `docs/architecture/connector-ingestion-architecture.md` — update §2 + §3 to reflect streaming-only bronze
- `docs/upgrades/v<next>.md` — operator note: peak disk savings + re-extract latency tradeoff
- `CHANGELOG.md` — entry covering both the default change and the legacy removal

**Acceptance:**
- Operators with `bronze_mode: filesystem` in config get a fix-pointer error at startup ("`bronze_mode` is no longer accepted — streaming bronze is the only path; remove this line from your config")
- All other configs work unchanged
- No FilesystemBronzeStore references remain in the codebase
- Upgrade note honestly describes the re-extract latency change

**Validation gate (what must be green before merging Phase 7):**
- Phase 4 has been live on the dogfood VM for at least 24h with `bronze_mode: streaming` configured for SharePoint
- Disk usage on the dogfood VM has dropped substantially (the 112GB bronze finding from v2026.5.27a2 should be ~MB after a full sync under streaming)
- A `kairix worker reextract --source-name sharepoint --dry-run --limit 10` against streaming-bronze rows returns successful recovery
- The eval suite (`bash scripts/run-evals.sh` or equivalent) shows no regression in recall/precision metrics
- Bronze write latency hasn't increased (streaming should be FASTER — no fsync of the raw blob)

## 6. Test discipline

Per F45 / F46 / F47 / F48:

| Test layer | Coverage |
|---|---|
| **Contract** (`tests/contracts/`) | Both BronzeStore impls satisfy the Protocol identically |
| **Unit** (`tests/unit/`) | StreamingBronzeStore.write metadata-only; .read raises with fix pointer; bronze_records hash populated |
| **Integration** (`tests/integration/`) | F47 factory composition — `build_bronze_from_entry` precedence + wiring into ConnectorPipeline |
| **BDD** (`tests/bdd/features/bronze_streaming.feature`) | F45 mandatory feature for the new capability — happy path + re-extract fallthrough + source-unavailable scenario |
| **E2E** (`tests/e2e/`) | F48 composed-production-path — full config → factory → ingest → query against streaming bronze; assert no on-disk blobs |
| **F30 outcome** (`tests/integration/test_outcome_streaming_bronze.py`) | Subprocess-driven CLI run with streaming config |

Sabotage proofs executed for every test before commit per `feedback_sabotage_must_be_executed`.

## 7. Risks + mitigations

### R1 — Re-fetch increases per-incident source load

**Risk:** A Bug D-style mass re-extract now re-fetches every dead-letter item from source. For 8,000 items at ~1s/fetch = 2.2 hours of source API traffic. May trip rate limits (SharePoint Graph, Slack, etc.).

**Mitigation:**
- Re-fetch loop honours the connector's existing rate-limit handling (tenacity backoff from v2026.5.26a1).
- Reextract CLI gets a `--rate-limit-rps` flag so operators can throttle.
- Document the trade-off in the upgrade notes so operators size their recovery windows accordingly.

### R2 — Items deleted at source between fetch and re-extract are unrecoverable

**Risk:** Streaming bronze loses the ability to re-extract items that have been deleted from source since the original fetch. With FilesystemBronzeStore, we'd still have the raw bytes on disk.

**Mitigation:**
- Counter `skipped_source_unavailable` makes the loss observable.
- Operators with audit/compliance requirements continue using FilesystemBronzeStore (opt-out via `bronze_mode: filesystem`).
- For the dogfood pattern (active SharePoint corpus, source files largely stable), this is acceptable.

### R3 — Auth failures during re-fetch

**Risk:** Re-fetch can fail because credentials rotated, app permissions revoked, scope changed. With FilesystemBronzeStore, raw bytes were already secured at first fetch.

**Mitigation:**
- Existing per-connector auth refresh paths apply (no change here).
- `skipped_source_unavailable` counter surfaces auth-flavoured failures the same as deletion-flavoured ones; operators triage via the dead_letter table's `last_error`.

### R4 — Hash-based duplicate detection requires the hash column populated

**Risk:** Existing bronze_records rows have `content_hash IS NULL`. Operators running mixed-mode deploys may rely on hash for dedupe and hit gaps.

**Mitigation:**
- `content_hash` is purely additive — its absence doesn't break anything in the current pipeline.
- Document in the upgrade note that hash-based features (if any) only apply to rows written under v2026.6.x+.
- Optional follow-up: backfill script that computes hashes for rows where `raw_path IS NOT NULL` (FilesystemBronzeStore data).

### R5 — Reextract counter semantics change

**Risk:** Operators with monitoring on `skipped_no_bronze` see the counter drop to ~zero (streaming rows never hit that branch). Existing alerts may be silently broken.

**Mitigation:**
- Upgrade note explicitly calls out the counter semantics change.
- The replacement counter `skipped_source_unavailable` captures the same operator concern (item not recoverable).
- Monitoring guidance: alert on `skipped_no_bronze + skipped_source_unavailable` for parity.

### R6 — F38 / F61 fitness functions

**Risk:** The new code lands under `kairix/core/connectors/` which is the canonical home for connector framework code. F38 (Silver processing only in `silver.py`) and F61 (legacy chunk writer only inside `kairix/core/connectors/`) need to not regress.

**Mitigation:**
- `StreamingBronzeStore` lives in `kairix/core/connectors/streaming_bronze.py` — already inside the F38/F61-blessed tree.
- No new chunker construction; the existing chunk_writer call paths are unchanged.

## 8. Out of scope (separately tracked)

- **Bronze content-hash backfill for FilesystemBronzeStore rows** — separate one-off migration script, not part of this plan
- **Compressed FilesystemBronzeStore** — orthogonal mitigation for operators who want to stay on filesystem-backed bronze with lower disk usage
- **Per-source streaming bronze opt-in** — already supported by phase 4's config field; no further work needed
- **Streaming for the extractor's internal scratch** — already addressed by v2026.5.27a2 TMPDIR + cleanup discipline

## 9. Rollout checklist

- [ ] Phase 1 lands in alpha — green CI, sabotage-proven, BDD-covered
- [ ] Phase 2 lands in alpha — schema migration tested against a backup of dogfood DB
- [ ] Phase 3 lands in alpha — re-extract handles both raw_path shapes
- [ ] Phase 4 lands in alpha — operator opt-in works; default still filesystem
- [ ] Dogfood VM flipped to `bronze_mode: streaming` (manual opt-in) for ≥7 days
- [ ] Dogfood validation: re-fetch latency observed, source rate-limits not tripped, no functionality regression in MCP search
- [ ] Phase 5 (Bug D update) ships alongside phase 4
- [ ] Phase 6 F30 outcome test green in CI
- [ ] Phase 7 default flip — separate alpha, separate upgrade note, separate operator green-light
- [ ] Phase 8 deprecation announcement
- [ ] FilesystemBronzeStore removal — after deprecation window

## 10. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-05-26 | Choose true streaming over TTL+compressed compromise | TTL is a half-measure; production-sized corpora still blow the disk during initial backfill. Streaming is the architectural answer. |
| 2026-05-26 | Re-extract routes through `connector.fetch`, not by retaining raw bytes | Source connectors already support per-item fetch; the re-fetch cost is paid only during episodic recovery, not the hot path. |
| 2026-05-26 | Ship as opt-in first (`bronze_mode` config), default later | Lets dogfood validate without forcing every operator to migrate immediately. Phase 7 (default flip) is its own deliberate decision. |
| 2026-05-26 | Keep FilesystemBronzeStore alongside as an opt-in for audit-heavy deploys | Some operators have compliance reasons to retain raw bytes. Opt-in retention is a legitimate use case. |

## 11. Open questions

1. **Do we need a `raw_path` migration for existing rows?** Probably not — leaving them populated is fine; the re-extract path inspects per-row, and the FilesystemBronzeStore.read still works on them.

2. **Should `bronze_ttl_gc` (#316) still exist?** Yes — operators on FilesystemBronzeStore (opt-out) still want TTL-based reaping. The flag stays.

3. **Should we add `bronze_mode: hybrid` (stream raw + persist for N days)?** Adds complexity without clear demand. Defer until a real operator asks. Keep the two clear modes (streaming vs filesystem) initially.

4. **What about Phase 0 — disable bronze persistence entirely as a fast prototype before formalising the Protocol?** Tempting but skips the test discipline. Stick to the phased plan; it's only one alpha per phase.
