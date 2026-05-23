# Implementation gap analysis — current `kairix` baseline vs ADR v2

Snapshot date: 2026-05-23. Source walk performed against the head of
`main` (`e7d33db8` at scan time, since pushed forward). Read against
`docs/architecture/connector-scope-topology/ADR.md` (v2, ~700 lines).

This doc is the **gap inventory** — what's in the code today, what ADR v2
demands, and the delta. Drives the Wave A → G migration plan with concrete
file:line references so an implementer can act on it.

---

## Section 1 — Connector framework (current state)

### 1.1 `kairix/core/connectors/__init__.py`

Public exports (`__all__`):

- `BatchResult`, `ChunkWriter`, `ConnectorPipeline` (from `pipeline`)
- `ConnectorRegistry`, `ExtractorRegistry`, `iter_connectors`, `iter_extractors`, `resolve_connector`, `resolve_extractor` (from `registry`)
- `CursorStore` (from `cursor_store`)
- `DeadLetterEntry`, `DeadLetterStore` (from `dead_letter`)
- `DefaultSilverProcessor` (from `silver`)
- `FilesystemBronzeStore` (from `bronze`)

Protocols live in `kairix.core.protocols`, not in the connectors package. No registry helper for `Chunker`, `Section`, `ScopeProfile`, `Container`, `Skill`, or any other ADR-v2 surface.

### 1.2 Connector Protocols (canonical home: `kairix/core/protocols.py`)

Connector-framework surface (lines 787–1092):

| Protocol | Methods (return type) | Lines |
|---|---|---|
| `SourceConnector` | `list_changes(cursor) → Iterator[ChangeEvent]`; `fetch(item_id) → RawArtefact`; `source_link(item_id) → str`; `sensitivity_for(item_id) → Sensitivity`. Class attribute `name: str` | 973–1002 |
| `Extractor` | `can_extract(mime, magic_bytes) → bool`; `extract(raw, mime) → ExtractedDocument`; `quality_ok(doc) → bool`. Class attributes `name: str`, `version: str` | 1005–1030 |
| `BronzeStore` | `write(source_name, item_id, raw, mime) → BronzeRef`; `read(ref) → tuple[bytes, MimeType]`; `replay(source_name, since=None) → Iterator[BronzeRef]` | 1033–1053 |
| `SilverProcessor` | `process(raw, extracted, source_uri, source_modified_at, sensitivity) → SilverOutput` | 1056–1076 |
| `EntityGraphSink` | `stage(signals) → int` | 1079–1092 |

Frozen-dataclass value objects (lines 814–971):

- `ChangeEvent(op, item_id, modified_at, parent_id=None, metadata={})` — `op: Literal["created","modified","deleted"]`
- `RawArtefact(raw, mime, fetched_at)` — **no `sensitivity_hint`**
- `Page(page_number, text, has_images)`
- `Image(page_number, classification, data)`
- `DocMetadata(title, author, created_date, language, page_count)`
- `ExtractedDocument(markdown, pages, images, metadata, confidence)`
- `BronzeRef(source_name, item_id, raw_path, mime, fetched_at)`
- `Chunk(text, content_hash, source_name, source_uri, source_modified_at, source_page, sensitivity)` — **no `chunker_version`, no `section_kind`**
- `EntitySignal(kind, value, source_uri, modified_at, confidence, sensitivity)` — `kind: Literal["person","org","relationship"]`
- `SilverOutput(chunks, entity_signals)`

Type aliases: `Cursor = str`; `MimeType = str`; `Sensitivity = Literal["public","internal","client-confidential","personal"]`.

`ChunkWriter` Protocol is defined in `kairix/core/connectors/pipeline.py` lines 70–87 — single method `upsert(chunks) → int`.

### 1.3 `ConnectorPipeline` (`kairix/core/connectors/pipeline.py`)

Class shape (lines 108–232). Composes a shared `sqlite3.Connection` as the transaction boundary with six injected stores: `BronzeStore`, `SilverProcessor`, `ChunkWriter`, `EntityGraphSink`, `CursorStore`, `DeadLetterStore`, plus integer `dead_letter_threshold=3`.

`run_batch(connector, extractor) → BatchResult` (line 141): one batch, one SQLite transaction. Iterates `connector.list_changes(cursor)`, calls `_process_item` per `ChangeEvent`. Per-item fetch/extract failure → `DeadLetterStore.record`; silver/writer/sink failure → batch rollback + reraise. Cursor advances to the **most recent `modified_at`** seen this batch (line 184); commit at end via `_db.commit()`.

`BatchResult` (lines 90–105): frozen dc — `processed`, `dead_lettered`, `poisoned_skipped`.

### 1.4 `bronze.py`

`_content_hash(raw) → str` (line 37): SHA-256 hex-encoded.

`FilesystemBronzeStore(db, bronze_root)` (line 42): persists bytes to `<bronze_root>/<source_name>/<hash[:2]>/<hash>` with tmp-then-rename for atomicity (lines 67–69). Writes a `bronze_records` row via `INSERT OR REPLACE`. Caller owns the commit. `replay()` streams ordered by `fetched_at ASC` with optional `since` filter.

### 1.5 `silver.py` — UNIFORM PARAGRAPH chunker (the F38 lock-in)

`DefaultSilverProcessor` (line 146) is the F38-locked singular Silver surface. Chunker is **uniform paragraph-boundary** — _not_ kind-aware:

- `_chunk_markdown(markdown)` (line 56): splits on `\n\s*\n`, glues paragraphs up to `_TARGET_CHUNK_CHARS = 1000` (line 46).
- `_chunk_pages(pages)` (line 87): per-page chunking, no straddle.
- `_extract_entity_signals` (line 108): regex `\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b`. Tags `org` if trailing token in `_ORG_SUFFIXES = {"Corp","Inc","Ltd","LLC","GmbH","Plc","Company","Group"}`, else `person`. Confidence fixed `0.5`.

`process(raw, extracted, source_uri, source_modified_at, sensitivity)` (line 161): page-aware vs flat dispatch. Every Chunk carries `content_hash = sha256(text)`. **No `chunker_version` field anywhere.**

### 1.6 `registry.py`

Entry-point group names: `kairix.connectors`, `kairix.extractors` (lines 33–34).

`resolve_connector(name) → Callable` (KeyError with full `fix:`/`next:`/`run:` triage — F21-clean), `resolve_extractor(name)` likewise. `ConnectorRegistry.resolve(name, *, config={})` thin wrapper; `ExtractorRegistry.resolve(mime, magic_bytes) → Extractor` walks every registered extractor, returns first whose `can_extract()` returns True.

### 1.7 `cursor_store.py` + `dead_letter.py`

`CursorStore(db)`: `read(source_name) → Cursor | None`, `write(source_name, token) → None`. Table `connector_cursors` keyed on `source_name PRIMARY KEY`.

`DeadLetterStore(db)`: `record`, `is_poisoned(threshold=3)`, `list`. Table `connector_deadletter` keyed on `(source_name, item_id) UNIQUE`.

---

## Section 2 — Shipped connectors (current state)

### 2.1 `kairix/connectors/__init__.py` + `_base.py`

Re-exports `SourceConnector`, `ChangeEvent`, `Cursor`, `MimeType`, `RawArtefact`, `Sensitivity` from `kairix.core.protocols`.

### 2.2 obsidian

Files: `connector.py` (342 lines), `fs.py`, `reconciler.py`, `watcher.py`, `__init__.py`.

`ObsidianConnector` (line 89). Constructor `(vault_root, collections=None, sensitivity="internal", *, known_state_resolver, watcher_factory, reconcile_every=10)`. Implements `SourceConnector` only — no capability mix-ins.

Auth: none (local FS). Cursor: ISO-8601 UTC timestamp string. Change detection: watchdog + `FullScanReconciler` every Nth call. Factory `make_connector(config)` (line 286).

### 2.3 dex_crm

Files: `connector.py` (322 lines), `client.py`, `__init__.py`.

`DexCrmConnector` (line 118). Auth: API-key Bearer via `kairix.transport.auth.api_key.ApiKeyAuth`. Surfaces `MissingCredentialsError` on first `list_changes`. Cursor: ISO-8601 timestamp passed as `updated_after` query param. Internal `_CursorTimestamps(contacts, organisations, relationships)` tracker exists but only the min-across-kinds persists.

### 2.4 m365_email_headers

`M365EmailHeadersConnector` (line 123). Constructor takes `user_principal_name` (per-mailbox). Module-level `LOCKED_SENSITIVITY: Sensitivity = "personal"` (line 68) — operator cannot lower the tier. Auth: OAuth2 client-credentials with `connector-m365-{tenant-id,client-id,client-secret}` secret triple. Cursor: opaque Graph `deltaLink` URL.

### 2.5 m365_calendar

`M365CalendarConnector`. Frozen-dc `M365CalendarConfig(user_id, tenant_id, client_id, client_secret, sensitivity, scope, window_days_back, window_days_forward)` (line 66). Auth shares M365 AAD app registration with email-headers. Cursor: opaque OData `@odata.deltaLink`.

---

## Section 3 — SQLite schema (current state)

File: `kairix/core/db/schema.py`. `SCHEMA_VERSION = "2"`.

### `documents` columns (lines 50–66)

`id INTEGER PK AUTOINCREMENT`, `collection TEXT NOT NULL`, `path TEXT NOT NULL`, `title TEXT`, `hash TEXT NOT NULL`, `created_at TEXT`, `modified_at TEXT`, `active INTEGER DEFAULT 1`, `agent_owner TEXT`, `source_name TEXT`, `source_uri TEXT`, `source_modified_at TEXT`, `source_page INTEGER`, `sensitivity TEXT NOT NULL DEFAULT 'public'`, `UNIQUE(collection, path)`.

### `connector_cursors` (lines 110–114) — **single token per source**

`source_name TEXT PRIMARY KEY`, `cursor_token TEXT NOT NULL`, `updated_at TEXT NOT NULL`. **No per-cc_pair, per-container, or per-tenant axis.**

### `connector_deadletter` (lines 116–124)

`id INTEGER PK AUTOINCREMENT`, `source_name TEXT NOT NULL`, `item_id TEXT NOT NULL`, `failure_count INTEGER NOT NULL`, `last_error TEXT`, `last_attempt TEXT NOT NULL`, `UNIQUE(source_name, item_id)`.

### `bronze_records`, `entity_signals`, `documents_media`, `document_pages` — all single-source-keyed

(See full schema for details; none carry a cc_pair or container_id axis.)

**Missing tables vs ADR v2**: `connector_containers`, `connector_hierarchy_nodes`, `cc_pairs`, `credentials`, `scope_profiles`, `scope_entries`, `skills`, `task_collections`, `federated_connectors`, `group_grants`, `collection_sources`. **None exist.**

---

## Section 4 — Worker dispatch (current state)

File: `kairix/worker.py` (1216 lines).

### `WorkerDeps` (line 634)

Dataclass with `default_factory` fields including `connector_sync_fn` defaulting to `_default_connector_sync` (line 660).

### Dispatch chain

`_default_connector_sync()` (line 515) → `dispatch_connector_sync(read_flag, on_branch, off_branch)` (line 554) → flag `obsidian_connector_primary` → either `run_via_connector_pipeline` (line 427) or `run_via_legacy_document_scanner` (line 468).

A second dispatcher `dispatch_m365_email_headers_sync` (line 608) reads `connector_m365_email_headers`.

### `run_connector_sync_pipeline` (line 366)

Reads `connectors:` list from `kairix.config.yaml`. Per entry runs `_run_one_connector_batch(db, entry, bronze_root)` (line 272):

1. Resolves connector + extractor via `resolve_connector(name)` / `resolve_extractor(extractor_name)`.
2. Calls `connector_factory(entry.get("config", {}))`.
3. Calls `extractor_factory()` or `**entry["extractor_config"]`.
4. Builds `ConnectorPipeline(db=, bronze=FilesystemBronzeStore, silver=DefaultSilverProcessor, chunk_writer=_SqliteChunkWriter(db, collection=name), entity_graph_sink=_SqliteEntityGraphSink(db), cursor_store=CursorStore(db), dead_letter=DeadLetterStore(db))`.
5. `pipeline.run_batch(connector, extractor)`.

`collection` for the ChunkWriter is **always set to the connector name** — no per-cc_pair, no Collection/cc_pair binding.

### Maintenance scheduling

Constants (lines 51–73): `EMBED_INTERVAL=3600`, `CONNECTOR_SYNC_INTERVAL=900`, etc. Idle backoff via `EMBED_BACKOFF_MAX_INTERVAL=14400`. Per-task `last_*` timestamps tracked in `_maybe_run_maintenance_cycle` (line 1039) and `main()` loop (line 1194).

---

## Section 5 — Test surface (current state)

### `tests/contracts/test_protocols.py`

`isinstance()` conformance for Fake* against each Protocol: `IntentClassifier`, `DocumentRepository`, `GraphRepository`, `VectorRepository`, `EmbeddingService`, `FusionStrategy`, `BoostStrategy`, `ScoringStrategy`, `SearchLogger`, `FeatureFlagResolver`.

**Connector-surface Protocols NOT in this file** — they're covered piecemeal by:
- `tests/contracts/test_connector_protocols.py`
- `tests/contracts/test_connectors_base.py`
- `tests/contracts/test_extractors_base.py`
- `tests/contracts/test_obsidian_protocol.py`
- `tests/contracts/test_dex_crm_protocol.py`
- `tests/contracts/test_m365_email_headers_protocol.py`
- `tests/contracts/test_m365_calendar_protocol.py`
- Per-extractor `test_<name>_protocol.py`

### `tests/bdd/features/` — connector-related

- `connector_pipeline.feature`, `connector_bronze.feature`, `connector_silver.feature`, `connector_cursor.feature`, `connector_deadletter.feature`
- `connector_obsidian` (via `feature_flag_obsidian_connector_primary.feature`), `connector_dex_crm.feature`, `connector_m365_email_headers.feature`, `connector_m365_calendar.feature`
- Extractor: `extractor_passthrough.feature`, `extractor_markitdown.feature`, `extractor_pdf_fallback.feature`, `extractor_ocr.feature`, `extractor_pptx.feature`, `extractor_xlsx.feature`, `extractor_docx.feature`
- Feature-flag: per-flag feature file

### `tests/integration/` — connector-touching

`test_connector_pipeline_contract.py`, `test_schema_migration_connector.py`, `test_silver_per_page_citation.py`, per-flag `test_feature_flag_*.py`, `test_search_emits_source_page.py`.

### `tests/e2e/` — composed-path

`test_composed_production_path.py`, `test_composed_obsidian_connector_primary_path.py`, `test_composed_connector_dex_crm_path.py`, `test_composed_connector_m365_email_headers_path.py`, `test_composed_connector_m365_calendar_path.py`.

### `tests/fakes.py` — connector-relevant Fake*

- `FakeSourceConnector` (line 2246)
- `FakeDexCrmConnector` (line 2176), `FakeObsidianConnector` (~2100s), `FakeM365EmailHeadersConnector` (~2300), `FakeM365CalendarConnector` (~2370)
- `FakeExtractor` (line 2412), `FakeEntityGraphSink` (line 2457), `FakeChunkWriter` (line 2474), `FakeFeatureFlagResolver` (line 2491)

**No Fake exists for** `BronzeStore`, `SilverProcessor`, `CursorStore`, `DeadLetterStore` (integration tests construct real classes against tmp_path SQLite). **No capability-mix-in Fakes** (the Protocols themselves don't exist yet).

---

## Section 6 — Operator config (current state)

Schema in `kairix/core/search/config_loader.py` (833 lines) + `config_validator.py` (253 lines).

### Top-level sections currently parsed

- `_schema_version`, `_schema_version_required_min`
- `provider:`, `retrieval:` (with `fusion_strategy`, `rrf_k`, `bm25_limit`, `vec_limit`, `boosts.*`, `rerank`)
- `collections:` — `shared: [{name, path, in_default, retrieval}]`, `agent_pattern`, `agent_paths`
- `agents:` — list of `{name, collection, write_path}`
- `connectors:` — flat list of `{name, config, extractor, extractor_config}`. **No schema validation beyond "list of dicts each with string name"** — `validate_config` does not inspect this section.
- `features:` — read by `kairix/core/features/`

### Layering

`load_layered_yaml` (line 240): base at `KAIRIX_CONFIG_BASE_PATH` (default `/opt/kairix/kairix.config.yaml`) merged with overlay at `KAIRIX_CONFIG_OVERLAY_PATH`. Legacy single-file fallback at `KAIRIX_CONFIG_PATH` or `./kairix.config.yaml`.

**MISSING in current schema**: `containers:`, `hierarchy_nodes:`, `cc_pairs:`, `credentials:`, `scope_profiles:` / `scope_entries:`, `skills:` / `task_collections:`, `federated_connectors:`, `group_grants:`, `chunker:` registry, `extractors.<name>.chunker_version`, `collection.sources:`.

---

## Section 7 — F-rules enforcing connector discipline today

From `docs/architecture/fitness-functions.md` F34–F44:

- **F34** — `kairix/core/connectors/**` ≠ import `kairix/connectors/**` or `kairix/extractors/**` (mirrors F26)
- **F35** — `kairix/connectors/<a>/**` ≠ import another connector or any extractor
- **F36** — every plugin under `{connectors,extractors}/<name>/` has matching `tests/bdd/features/*` AND E2E examples row
- **F37** — change-detection libs (watchdog/msgraph/notion_client/slack_sdk.rtm/dulwich) allowed only under `kairix/connectors/<name>/` or `kairix/core/connectors/`
- **F38** — Silver chunking + entity-signal extraction only in `kairix/core/connectors/silver.py`
- **F39** — every `Chunk(...)` constructor must pass `source_uri` + `source_modified_at` + `sensitivity`
- **F40** — every Extractor declares `version: str` written to `documents_media.extractor_version`
- **F41** — every plugin carries `py.typed` + rationale on `# type: ignore`
- **F42** — public Protocol methods return frozen dc / tuple thereof / allowed simple shape
- **F43** — every plugin has `tests/contracts/test_<name>_protocol.py` against canonical fake + real impl
- **F44** — engagement-scope code (every `kairix/`) ≠ import firm-scope storage clients (psycopg*, asyncpg, pg8000, aiopg)

---

## Table A — what exists vs ADR v2 (per component)

| ADR v2 component | Currently in code | Status |
|---|---|---|
| `Connector(kind, name, config)` dataclass | Runtime construct is a `SourceConnector` Protocol impl. Config is raw `Mapping[str, Any]` passed to `make_connector`. No `kind` field; the wire-level name doubles as kind. | **partial** |
| `Credential` dataclass | MISSING. Ad-hoc `M365Credentials(tenant_id, client_id, client_secret)` in `m365_email_headers/connector.py:91` and `M365CalendarConfig` inline in `m365_calendar/connector.py:66`. No shared abstraction. | **partial** |
| `ConnectorCredentialPair` dataclass | MISSING. No cc_pair concept. Current pipeline binds connector 1:1 with extractor; `connector.name` is the only identifier. | **missing** |
| `Container` dataclass | MISSING. No `containers` table, no `Container` Protocol/dc. Closest analogue: per-connector `CollectionConfig(name, path, glob, exclude)` for Obsidian (`kairix/core/db/scanner.py`) — filesystem-shaped only. | **missing** |
| `HierarchyNode` + `HierarchyNodeType` enum | MISSING. No hierarchy_node table; `ChangeEvent.parent_id` field exists (`protocols.py:842`) but no node typing or persistence layer reads it. | **missing** |
| `Collection` + `CollectionSource` + `FederatedConnector` + `GroupGrant` | `Collection` exists as `CollectionDef(name, path, in_default, retrieval_overrides)` in `config_loader.py:476`; sources NOT modelled. `FederatedConnector` MISSING. `GroupGrant` MISSING. | **partial** |
| `ScopeProfile` + `ScopeEntry` | MISSING. Scope today is the search-time `ScopeQualifier` enum (`MY_AGENT`, `SHARED`, `ALL_AGENTS`, `EVERYTHING`) at the agent level, not a configurable profile-and-entry list. | **missing** |
| `Skill` + `TaskCollection` | MISSING. No skill or task_collection table, Protocol, or dc. | **missing** |
| `Chunker` Protocol + registry | MISSING. Chunking lives inline in `DefaultSilverProcessor` (`silver.py`) with hard-coded `_TARGET_CHUNK_CHARS = 1000` paragraph splitter. No `Chunker` Protocol, no entry-point group, no per-source override hook. | **missing** |
| `Section` typed union (Text / Tabular / Image) | MISSING. Existing shape is `ExtractedDocument(markdown, pages, images, metadata, confidence)` with separate flat lists. No `Section` union, no `section_kind` on `Chunk`. | **missing** |
| Capability Protocols (Poll / Checkpointed / Slim / SlimWithPermSync / Event / Resolver / Hierarchy / OAuth / Credentials) | MISSING. `SourceConnector` is monolithic. No capability mix-ins; behaviour differences are method-body branches. | **missing** |
| `RawArtefact.sensitivity_hint` | MISSING. `RawArtefact` (`protocols.py:846`) has `raw`, `mime`, `fetched_at` only. Sensitivity queried via `connector.sensitivity_for(item_id)`. | **missing** |
| `ChangeEvent.op` extended enum | `op: Literal["created","modified","deleted"]` only (`protocols.py:838`). No `archived`, `access_lost`. | **partial** |
| `AccessType` per cc_pair | MISSING. No cc_pair, no per-pair access-type qualifier. | **missing** |
| Typed exception hierarchy | Single typed exception is `MissingCredentialsError` from `kairix.transport.auth.api_key`. All other connector failures raise generic `RuntimeError`/`ValueError`/`KeyError`. | **partial** |
| `connector_containers` + `connector_hierarchy_nodes` tables | MISSING. Schema knows only `connector_cursors` (one row per source) + `connector_deadletter` (one row per (source, item_id)). | **missing** |

---

## Table B — F-rule deltas required by ADR v2

| F-rule | New / Modified | Rationale |
|---|---|---|
| **F55** (`chunker_version` on every chunk write) | **NEW** | ADR v2 introduces `Chunker` registry with versioned implementations; every `Chunk(...)` must carry chunker's version so a bump triggers tractable re-chunking (mirrors F40 for extractors). Today `Chunk` has no version field. |
| **F56** (capability Protocol declaration per connector) | **NEW** | ADR v2 splits monolithic `SourceConnector` into capability mix-ins. F56 enforces every plugin declares its supported capability set via Protocol inheritance or a module-level `CAPABILITIES: frozenset[str]` so the orchestrator routes without `hasattr` introspection. |
| **F57** (cc_pair lifecycle state-machine integrity) | **NEW (proposed)** | ADR v2 §3 defines `cc_pair.status` as a state machine (`SCHEDULED → INITIAL_INDEXING → ACTIVE ↔ PAUSED / DELETING / INVALID`). F57 enforces only valid transitions, each timestamped, no jumps. Today `connector_cursors` has only `(source_name, cursor_token, updated_at)` with no lifecycle state. |
| **F58** (HierarchyNode parent-before-child invariant) | **NEW (proposed)** | Every `HierarchyNode` emission has `raw_parent_id` either None (root) or referencing a previously-emitted node within the same `iter_containers()` call. Protects against orphan emissions / out-of-order hierarchy construction. |
| **F59** (Collection.sources cc_pair referential integrity) | **NEW (proposed)** | Every `Collection.sources[*].cc_pair` references a declared cc_pair. Config-validation rule. |
| **F60** (ScopeProfile.entries collection_name referential integrity) | **NEW (proposed)** | Every `ScopeProfile.entries[*].collection_name` references a declared collection. Config-validation rule. |
| **F61** (CollectionRouter singleton — extends F38) | **NEW (proposed)** | Every chunk write goes through `CollectionRouter` (no direct `_SqliteChunkWriter(collection=name)` outside the router). Today `worker.py:_run_one_connector_batch` does direct chunk-writer construction. |
| **F38** (Silver singleton) | **MODIFIED** | Current: "chunking lives only in `kairix/core/connectors/silver.py`". ADR v2 introduces a Chunker registry that pulls chunking into a plugin layer. F38 relaxes to "Silver is the singular orchestrator that COMPOSES the chunker; the chunker implementation lives in `kairix/chunkers/<name>/`". |
| **F42** (frozen-dc Protocol returns) | **MODIFIED** | Extend roster: add `Chunker`, `Resolver`, `Hierarchy`, `Container`, `Credential`, capability-mix-in Protocols. |
| **F39** (chunk metadata) | **MODIFIED** | Add to mandatory write surface: `chunker_version`, `section_kind`, optionally `cc_pair_id` + `container_id`. |
| **F36** (plugin BDD coverage) | **MODIFIED** | Extend discovery glob to `chunker_<name>.feature` and other new plugin types. |
| **F43** (per-plugin contract test) | **MODIFIED** | Extend to chunker / resolver / hierarchy plugins. |

---

## Critical observations

- **The current schema is at `SCHEMA_VERSION = "2"`** (`kairix/core/db/schema.py:32`); ADR v2 needs its own migration entry. Wave A delivers `SCHEMA_VERSION = "3"` with 12 new tables added back-compat (none drop, none alter old shapes).
- **Obsidian's `known_state_resolver` DI seam** (`kairix/connectors/obsidian/connector.py:118`) is the closest existing analogue to ADR v2's `Resolver` capability. Likely retrofittable as a `Resolver` Protocol implementation in Wave B.
- **F44 (Postgres ban)** is the only existing rule touching the engagement-vs-firm boundary that ADR v2's `ScopeProfile` model formalises. ADR v2 doesn't change F44 — same boundary, more elaborate scope shape within engagement.
- **No production code references** `cc_pair`, `container`, `hierarchy_node`, `chunker_version`, `scope_profile`, `task_collection`, `federated_connector`, `credential_pair` — confirmed by grep over `kairix/`. Net-new vocabulary across all 12 tables.
- **Worker dispatch is tightly coupled** to the flat-name model (`worker.py:_run_one_connector_batch` directly constructs `_SqliteChunkWriter(db, collection=name)`). Wave C runtime needs to redirect through `CollectionRouter`. F61 (proposed) prevents regression.
- **Capability Protocols don't exist** as Protocol classes — they only exist as method-body branches in current connector implementations. Wave B Protocol shims (default impls preserving today's behaviour) are the migration mechanism.

---

## Wave-by-wave delivery map

| Wave | Deliverable | Files touched (concrete) |
|---|---|---|
| **A — schema additions** | Schema v3: 12 new tables, extend `documents` + `documents_media` columns, extend `RawArtefact` + `ChangeEvent` enums | `kairix/core/db/schema.py` (add tables); `kairix/core/protocols.py` (extend dataclasses); migration script `kairix/core/db/migrations/v2_to_v3.py` |
| **B — Protocol capability split** | Add capability mix-in Protocols, default-impl shims for existing 4 connectors | `kairix/core/protocols.py` (add 9 Protocol classes); `kairix/connectors/{obsidian,dex_crm,m365_email_headers,m365_calendar}/connector.py` (shim methods); F56 check |
| **C — runtime: cc_pair + CollectionRouter + Chunker registry** | cc_pair lifecycle + ScopeProfileResolver + CollectionRouter + ChunkerRegistry behind SilverProcessor + ResultEnvelope freshness | `kairix/core/connectors/__init__.py` (new exports); `kairix/core/connectors/cc_pair.py` (new); `kairix/core/connectors/collection_router.py` (new); `kairix/core/connectors/silver.py` (dispatch to registry); F55, F57, F58, F61 checks; `kairix/worker.py` (rewire dispatch) |
| **D — operator config promotion** | YAML schema for `cc_pairs:` / `credentials:` / `collections.sources:` / `scope_profiles:` / `skills:` / `federated:`; new CLI verbs | `kairix/core/search/config_loader.py` + `config_validator.py`; `kairix/cli.py` (new subcommands: `cc-pair`, `credential`, `scope`, `skill`); F59, F60 checks |
| **E — per-connector multi-container** | sharepoint, notion, jira, slack, github, gdrive plugins with full capability sets | `kairix/connectors/{sharepoint,notion,jira,slack,github,gdrive}/` (new); per-connector flag (F54 both-branch tests) |
| **F — chunker plugins** | tree-sitter / per-ticket / thread-aware / slide / tabular / email-thread / event / transcript / web chunkers | `kairix/chunkers/<name>/` (new tree); F40-equivalent F55 enforcement; per-chunker BDD + contract tests |
| **G — retirement** | Drop `connector_cursors` (post-migration to `connector_containers`); retire `topology_v2_*` flags; delete default-impl shims | `kairix/core/db/migrations/v3_to_v4.py` (drop deprecated table); F-rule baseline updates |

Each wave includes:
- F54 both-branch BDD tests for any new feature flag
- F36-compliant new BDD feature files
- F43-compliant contract tests
- F48 E2E composed-path test for net-new top-level capabilities
- Sabotage-proven new tests per `feedback_sabotage_must_be_executed`

---

## What this analysis commits us to before any code lands

1. **No new behaviour without test surface** — F36 + F43 + F45 + F46 + F47 + F48 already enforce this. New tables / Protocols / chunkers all need their BDD + contract + integration + E2E coverage before merging.

2. **Migration tests per wave** (new directory `tests/migrations/`) — every wave's feature flag has both-branch BDD coverage AND a migration smoke test that walks A → G end-state without breaking canonical queries (per `10-test-architecture.md`).

3. **Capability-inventory tests per connector** — assert which capability Protocols each connector implements; prevents silent drift.

4. **F-rule scripts added before the schema migration** — F55, F57, F58, F61 land in Wave A so they're armed before code grows into the gap.

5. **Operator-facing CLI for cc_pair lifecycle** — `kairix cc-pair {list,create,pause,resume,rotate-credential,delete}` lands in Wave D; until then operators have no surface to operate the topology.

The total scope is genuinely large but every step is bounded by:
- One feature flag default-off until validated.
- Both-branch tests at every flag.
- Existing 4 connectors stay green at every wave (the default-impl shims preserve their behaviour).
- Each wave has explicit acceptance criteria (per ADR v2 §"Acceptance criteria").
