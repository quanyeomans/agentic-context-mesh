# Connector Architecture Refactor — Canonical Topology + Resilience Hardening

**Status:** Design (awaiting review)
**Scope:** The whole connector subsystem — config model, enablement gating, the ingest pipeline's deletion handling, and per-connector resilience to upstream nuances.
**Origin:** Two grounded reviews (architecture review + per-connector resilience audit, 2026-06-21) anchored to the live production-instance config. Supersedes/absorbs **PLA-189**.

---

## 1. Goal

Collapse the connector subsystem to **one canonical topology** (remove the legacy `connectors:`/`collections:` config blocks; make `topology_v2` the single model and drop the `v2` versioning), **fix the systemic deletion-correctness bug** that affects every live connector, and **harden each connector** against upstream nuances (rate limits, pagination, auth/token expiry, deletions, partial failure, schema drift) — preferring a small set of shared framework primitives over per-connector duplication.

**Non-goal:** re-architecting the connector framework. Both reviews concluded the core abstractions are sound (verdict: *sound-with-fixes*). The `SourceConnector`/Wave-B protocol split, the `next_cursor` don't-clobber discipline, and the resolver/registry machinery stay. This is *finish one migration + fix correctness + consolidate duplication*, not a rewrite.

---

## 2. Why — the verified findings

### 2.1 The architecture is sound
`SourceConnector` + Wave-B capability mix-ins (`protocols.py:1043-1114, 1719-1885`) are single-responsibility and additive; `ConnectorPipeline`'s per-chunk-commit + `next_cursor` None-means-don't-clobber contract (`pipeline.py:367-389`) prevents a documented full-resync incident for opaque-token sources; the flag resolver/registry is correct. Leave these alone.

### 2.2 Systemic correctness bug — deletions never propagate (the headline)
`ConnectorPipeline._process_item` **never branches on `change.op`** (no `.op` reference in `pipeline.py`). Every event is `fetch → bronze → extract → silver → upsert`. Connectors *do* emit `deleted`/`archived`/`cancelled`, so on the **live VM today**:
- Deleted SharePoint files, removed Obsidian notes, deleted Slack/GitHub content **stay searchable forever**.
- Cancelled M365/Notion/CalDAV events are **actively re-indexed with stale content every tick**.
- The documented escape hatch — the SlimConnector prune cycle consuming `retrieve_all_slim_docs` — has **zero live callers** anywhere in `kairix/core/` or `worker.py`. The connector docstrings claiming "deferred to the prune cycle" are currently fiction.

Affected (emit a delete op): obsidian, sharepoint, m365_calendar, slack, notion, google_drive, google_calendar, apple_caldav. Additive-only (item just vanishes from a delta feed, needs the prune cycle): gmail, dex_crm, linear, m365_email_headers, skills.

### 2.3 Per-connector live data-loss / wasted-work bugs
- **github** — the per-repo cursor stores `committed_at` and passes it as GitHub's *inclusive* `?since=`, so the boundary commit is re-fetched + re-extracted + re-upserted **every tick forever** on a quiet repo (`connector.py` `_drain_repo`).
- **slack** — `reindex()` (dead-letter replay) has contradictory `oldest` semantics → replays **zero** messages on the real wire; the in-memory fake can't catch it.
- **gmail** — `last_history_id() or cursor` re-uses the same cursor on a None historyId → re-queries the identical window; only `messagesAdded` history records are ingested (modifies/label-changes/deletes dropped).
- **google_drive** — Shared/Team Drives invisible (no `supportsAllDrives`/`includeItemsFromAllDrives`); native Docs/Sheets dead-letter every sync (`alt=media` 403).
- **google_calendar** — eager full-window drain sets the terminal `nextSyncToken` *before* the 500-item budget applies → events 501..N **permanently dropped** on a busy first sync.

### 2.4 The stalled migration + dead gating model (PLA-189)
The READ path + apply-at-boot already cut over to `topology_v2` unconditionally (task #132). The **write/ingest leg did not**: `_load_connector_config_entries` (`worker.py:355`) and the ranking-tier read (`factory.py:312`) still read legacy keys. Consequences:
- Connectors + collections are defined **twice** (legacy + topology_v2), hand-synced. Split-brain edit hazard.
- The **setup wizard writes only `topology_v2.connectors`** (`source_oauth.py:712`) but ingest reads only legacy `connectors:` → **a wizard-added source is search-routable but never ingested** on the shipped compose. (Masked on the VM because all 6 live connectors are hand-authored into the legacy block.)
- `connector_*` flags gate **dead `dispatch_<name>_sync` trios** (~430 lines, no production caller); `connector_x: false` does **not** disable a connector — config-presence does. The F54 both-branch tests assert against this dead path (**test theatre**).

### 2.5 F51 silently inert
`check_f51_flag_retirement` likely reads a non-CalVer scm version on a tagless CI checkout → vacuous-green, so six connector flags with `v2027` retire dates (past the 6-month ceiling, no `retire-extension` comment) sail through un-caught.

---

## 3. Target architecture

### 3.1 One canonical topology
- **Remove** the legacy top-level `connectors:` and `collections:` config blocks.
- `topology` (renamed from `topology_v2`) is the single model: `connectors` / `credentials` / `cc_pairs` / `collections` / `scope_profiles`. Read **and** write resolve from it.
- Ingest enumerates connectors from `topology.connectors` (not the legacy list); ranking tiers read `topology.collections`.
- **De-version the naming**: `topology_v2.py` → `topology.py`, retire the `topology_v2_*` flags (already retired from REGISTRY — only inert config residue remains), drop `_v2` suffixes. One source of truth, one set of code paths.
- This *is* the wizard-onboarding fix: wizard writes topology, ingest reads topology.

### 3.2 Connector enablement model (resolve PLA-189)
- **Enablement = config presence** in the canonical `topology.connectors` (already how obsidian/reflib work, and how the live loop actually behaves).
- **Per-connector flags = cutover-only**, introduced only when a connector's behaviour changes reversibly; default-safe; retired on the F51 deadline. A steady-state connector needs no permanent `connector_<name>` flag.
- Make the live loop honour any *registered* connector flag (flagged + effective-false → skip, logged), so the documented contract is true; flagless connectors always-on. Then **delete the dead `dispatch_<name>_sync` trios** and re-point the F54 tests at the real `run_connector_sync_pipeline` path (assert `ConnectorSyncResult` counters, not branch-marker logs).

### 3.3 Six shared framework primitives
1. **Delete/tombstone dispatch in `ConnectorPipeline._process_item`** — `op in {deleted,archived,access_lost,cancelled}` → `chunk_writer.delete_by_source_uri(connector.source_link(item_id))`, skip fetch/extract. ~10 lines; fixes the correctness half for all 8 delete-emitting connectors. The writer capability already exists.
2. **Wired SlimConnector prune cycle** — an off-tick maintenance job that diffs `retrieve_all_slim_docs()` against the `documents` table and `delete_by_source_uri`s orphans. The only deletion path for additive-only connectors. Off-tick so its full-enumeration cost doesn't blow the per-tick budget.
3. **Shared pagination-drain helper** — a true lazy generator (yield page-by-page; advance cursor only after the final page) with per-container try/except-and-record isolation built in. Converts the eager-materialise connectors to budget-respecting + memory-bounded, and fixes the "one bad repo/drive aborts the tick" gap, in one primitive. *Do exactly three things: drain next-token pages lazily, isolate per-container failures, stop when the consumer stops pulling.*
4. **Shared timestamp-cursor helper** — for the **watermark** connectors only (linear/notion/dex_crm/skills): compound `(timestamp, item_id)` cursor or `>= watermark + per-tick emitted-id dedup`, plus fixed-width microsecond isoformat normalisation. **Not** for opaque-token connectors (theirs are already correct). Two cursor families is the right shape — do not collapse them.
5. **Shared rate-limiter/backoff** — bounded + jittered + `Retry-After`-honouring (429/503/403-quota), sleep via injected seam. Nine connectors already hand-roll this near-identically; extract once, migrate the four gap/near-miss connectors (notion/google_calendar/apple_caldav/dex_crm), delete the copies.
6. **Capability-parity discipline (F75-shape rule)** — every advertised connector capability has a default-config live-path ingest test, so scope-mismatches (obsidian markdown-only glob, github commit-metadata-not-blobs, google_drive shared-drives, gmail append-only) can't recur.

### 3.4 Explicitly leave alone
`SourceConnector`/Wave-B protocols; the `next_cursor` don't-clobber contract; the resolver/registry flag machinery; the already-done read-path cutover; the connector→collection naming convention; the 10 `test_composed_connector_*_path.py` E2E files (real connector coverage — only drop their decorative `with_flag` lines).

---

## 4. Phased plan

**Sequenced to optimise for the total work, not for fixing any single bug fastest** (operator steer, 2026-06-21): do the structural collapse first so every later fix lands *once* on the single canonical path; build the framework primitives (the deletion fix is one of them) on that foundation; then per-connector hardening. **The deletion bug is fixed as the Phase-2 delete-dispatch primitive, not as a rushed standalone hotfix** — accepting it persists slightly longer in exchange for not doing throwaway work on the soon-deleted legacy path. Each phase is independently shippable and green-on-merge; the production-config cutover (Phase 1) is **HITL-gated**.

### Phase 1 — Canonical topology collapse (foundation; HITL on the prod cutover)
- Point ingest enumeration (`_load_connector_config_entries`, `_load_connector_entry`) + the ranking-tier read (`factory.py:312`) at `topology`; delete the legacy top-level `connectors:`/`collections:` blocks; rename `topology_v2`→`topology`, drop `_v2` suffixes; retire the inert `topology_v2_*` config residue.
- Resolve PLA-189 *on the canonical path*: enablement = config-presence in `topology.connectors`; flagged + effective-false → skip (logged); flagless → always-on. Then **delete the dead `dispatch_<name>_sync` trios** (~430 lines) and **de-theatre the F54 connector tests** (assert `ConnectorSyncResult` counters on the real path; fix the m365_calendar invented-gate test).
- This is also the wizard-onboarding fix (wizard writes topology, ingest now reads topology).
- **Migration** (capture-flip-soak-gate, §5): convert the live production-instance config from dual-block to canonical, last.

### Phase 2 — Framework resilience primitives (includes the deletion fix)
- **Primitive #1 — delete-dispatch** in `ConnectorPipeline._process_item` (`op in {deleted,archived,access_lost,cancelled}` → `chunk_writer.delete_by_source_uri(...)`, skip fetch/extract) + the **deletion-through-pipeline integration tests** (drive a `deleted` ChangeEvent through `factory.build_connector_pipeline().run_batch` and assert removal — they fail today; they pin the whole class). *This is the deletion-bug fix, done once on the canonical pipeline.*
- Primitive #2 prune cycle (wired into the worker, off-tick); #3 pagination-drain helper; #4 timestamp-cursor helper; #5 rate-limiter helper. Each helper ships with the migration of its adopting connectors + the budget-path test in the same change.

### Phase 3 — Per-connector hardening + completeness + discipline
- The live per-connector bugs, each fixed as that connector migrates onto the primitives: github inclusive-since boundary; slack `reindex()` semantics (+ MockTransport real-wire test); gmail None-historyId cursor + history taxonomy; google_calendar budget-yield (subsumed by the #3 lazy-drain migration).
- Completeness/scope: google_drive shared-drives + native-file export; slack thread replies; linear nested-connection pagination; obsidian glob honesty.
- Primitive #6 (F75-shape capability-parity rule); F51 de-vacuous (fetch-tags in CI or git-describe fallback) + `retire-extension` comments; vestigial flag/seam sweep (apple_caldav README, dex_crm dead flag, github unused `_flag_reader`, `_last_path_taken`).

---

## 5. Migration — the production config cutover (Phase 2)

The live production instance's `/etc/kairix/kairix.config.yaml` carries both blocks today. The cutover follows the standard capture-flip-soak-gate (CLAUDE.md):
1. **Capture** baseline: state digest (row counts per collection), eval scores, probe latency, sample-journey results.
2. **Flip**: deploy the canonical-only image + a config migrated to topology-only (legacy blocks removed). The connector *names* in `topology.connectors` must match what the live loop enumerated, and the cc_pair collection mappings must match where chunks already land (verify against `SELECT DISTINCT collection FROM documents`).
3. **Soak** 24h min.
4. **Gate**: state delta within ±2%, eval within ±2pp, latency within ±20%, sample-journey ≥80% parity → promote or rollback.

A config-migration helper converts a dual-block config to canonical (lossless), shipped with an upgrade note. **No prod change without explicit per-action authorisation** (HITL on releases).

---

## 6. Testing & discipline
- **Deletion**: a shared integration test per delete-emitting connector (or one parametrized) driving a `deleted` event through the real pipeline and asserting chunk removal (F68 failure-injection adjacent).
- **F54**: re-point connector-flag tests at `run_connector_sync_pipeline` + assert `ConnectorSyncResult` counters; tighten F54 to forbid asserting solely on a branch-marker log no production caller emits.
- **F75-shape**: new rule — advertised capability ⇒ default-config live-path ingest test.
- **F51**: make the gate non-inert.
- **Budget-path** (F69-shape): a `≥500`/`≥10K`-item test asserting the un-processed tail is re-delivered next tick (catches the google_calendar drop + the eager-materialise class).

---

## 7. Risks & sequencing notes
- **Collapse-first is the efficiency choice** (operator steer): every later fix lands once on the single canonical path instead of being built on the legacy path then reworked. The cost is that the deletion bug persists through Phase 1 — accepted deliberately ("optimise for the work, not the bug"); it is fixed in Phase 2 as the delete-dispatch primitive, done once on the canonical pipeline.
- Phase 1's prod cutover is the highest-risk step → capture-flip-soak-gate + HITL; touch the live config last.
- Within Phase 1, delete the dead `dispatch_<name>_sync` trios only **after** the canonical config-presence + flag-skip enablement is in place, so the F54 tests have a real gate to assert against.
- Phase 2/3 helpers (pagination-drain, timestamp-cursor, rate-limiter) each ship with the migration of their adopting connectors + the budget-path test in the same change — not as a bare helper followed by a separate migration.
- Two cursor families stay distinct (watermark vs opaque-token); do not collapse them into one abstraction.

---

## 8. Appendix — prioritised backlog (from the audit)

| Pri | Scope | Item | Connectors |
|---|---|---|---|
| P0 | framework | Delete-dispatch in pipeline `_process_item` | 8 delete-emitters |
| P0 | per-connector | google_calendar budget-yield cursor data-loss | google_calendar |
| P1 | per-connector | github inclusive-since boundary re-emit | github |
| P1 | per-connector | slack `reindex()` oldest-semantics → zero replay | slack |
| P1 | framework | Wire SlimConnector prune cycle | additive-only fleet |
| P1 | framework | Shared timestamp-cursor helper (same-ts skip) | linear, notion, dex_crm, skills |
| P1 | framework | Deletion-through-pipeline integration tests | delete-emitters |
| P2 | per-connector | gmail None-historyId cursor reuse | gmail |
| P2 | framework | Shared rate-limiter/backoff (F64 gaps) | notion, google_calendar, apple_caldav, dex_crm |
| P2 | framework | Lazy pagination-drain helper (budget defeat) | sharepoint, m365_calendar, slack, notion, dex_crm |
| P2 | per-connector | github/sharepoint enumeration isolation | github, sharepoint |
| P2 | per-connector | google_drive shared-drives + native-file export | google_drive |
| P3 | framework | Collapse to single drive topology (Wave-E vs legacy) | fleet |
| P3 | per-connector | m365_calendar in-process delta clobber | m365_calendar |
| P3 | per-connector | apple_caldav composite-cursor token drop | apple_caldav |
| P3 | framework | Capability-parity (F75-shape) + scope honesty | obsidian, github, gmail, slack, linear |

**Fleet grades:** m365_email_headers = solid; obsidian, sharepoint, slack, github, m365_calendar, linear, skills, gmail, google_drive, dex_crm = minor-gaps; notion, google_calendar, apple_caldav = notable-gaps.

Full per-connector findings: the audit output (`tasks/wqmkijj01.output`); architecture findings: `tasks/wo0hb69jw.output`.
