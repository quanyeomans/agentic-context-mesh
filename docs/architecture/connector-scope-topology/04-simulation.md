# Simulation — where the proposed model breaks

For each BDD scenario in `03-bdd-scenarios.md` (and a handful of
cross-cutting failure modes), walk the layered model end-to-end and
identify where it breaks, contradicts itself, or has no answer. The
output drives the ADR's schema + interface decisions.

## Proposed model under simulation (frozen for this walk)

The model under test:

```
ConnectorInstance:
  name: str                # unique instance id (cursor + deadletter scope)
  kind: str                # entry-point key (obsidian, sharepoint, notion, ...)
  credential_ref: str | None  # secret-store path
  config: Mapping[str, Any]   # per-kind config

Container:                 # per-instance internal scope unit
  connector_name: str
  container_id: str        # opaque to framework; kind-specific shape
                           # examples: drive_id, mailbox_id, channel_id, vault_root_subdir
  cursor_token: str | None
  last_synced_at: datetime
  access_state: Literal["accessible", "revoked", "not_yet_granted"]

Collection:
  name: str
  sources: list[CollectionSource]
  read_policy: ...         # see ScopeProfile
  default_sensitivity: F39Tier
  on_unmapped_item: Literal["land_in_default_collection", "drop"]

CollectionSource:
  connector_name: str
  source_path_filter: str  # glob / prefix on item_id
  sensitivity_override: F39Tier | None

ScopeProfile:
  actor_id: str
  entries: list[ScopeEntry]

ScopeEntry:
  collection_name: str
  read: bool
  write: bool
  max_sensitivity: F39Tier  # e.g. "internal" caps reads at internal+below

Skill:
  name: str
  task_collections: list[TaskCollection]
  ranking: str
  iteration_strategy: str

TaskCollection:
  name: str                # may match a real Collection, or be a virtual aggregator
  sources: list[CollectionSource]
  weight: float

SearchStrategy:
  scope_resolve(actor, skill, task) -> list[Collection]
  iterate(collections, query) -> ResultEnvelope
```

This is the foothold the simulation pressure-tests.

---

## Simulation 1 — SharePoint per-drive cursors (UC-KNW-1 + 03-sharepoint)

**Scenario**: 47 drives in tenant; first sync needs 47 delta cursors.

**Model walk**:
1. `ConnectorInstance(name="sharepoint-corp", kind="sharepoint",
   credential_ref=..., config={...})`
2. Worker calls `list_changes(connector_instance, cursor=None)` — but
   "cursor" is now ambiguous. The protocol's existing
   `list_changes(cursor: Cursor | None)` was designed for one cursor
   per connector. SharePoint needs N.

**Break point #1**: `SourceConnector.list_changes` signature is
single-cursor.

**Fix**: extend Protocol to support multi-container connectors. Two
shapes possible:

(a) `list_changes` returns `Iterator[ContainerChanges]` where each
`ContainerChanges` carries its own `(container_id, cursor_after)`.
The framework persists per-container cursors automatically.

(b) Connector exposes `iter_containers() -> Iterator[Container]` and
the framework calls `list_changes(container)` per container,
managing cursors per container.

Shape (b) is closer to the cursor schema; shape (a) is closer to the
current Protocol. Recommend (b) — explicit container model lets the
framework parallelise + retry per container, and surfaces partial
failure (one drive down doesn't crash the connector).

**Schema impact**: `connector_cursors` becomes
`(connector_name, container_id, cursor_token, ...)`. Migration:
existing single-cursor rows become
`container_id=name` (one container per single-container connector).

---

## Simulation 2 — Obsidian per-folder collection routing (03-obsidian)

**Scenario**: one obsidian instance routes 01-Projects/** → vault-projects,
02-Areas/** → vault-areas.

**Model walk**:
1. `ConnectorInstance(name="obsidian-personal", kind="obsidian", config=...)`
2. Collection definitions reference the connector instance with
   path filters:
   - `Collection(name="vault-projects", sources=[CollectionSource(connector_name="obsidian-personal", source_path_filter="01-Projects/**", ...)])`
   - same for vault-areas etc.
3. Worker runs the connector; for each emitted change event with
   `item_id`, it consults the collection-mapping table to find which
   collection(s) match the filter.

**Break point #2**: the current ConnectorPipeline writes via
`_SqliteChunkWriter(db, collection=name)` — chunk-writer is bound to
ONE collection at construction time, but the runtime needs to route
per-item.

**Fix**: introduce a `CollectionRouter` that consumes a change event
+ source path, looks up the matching collection (or falls back to the
connector's default), and dispatches to the right chunk-writer. The
ConnectorPipeline composes one chunk-writer-per-collection instead of
one per connector.

```python
class CollectionRouter:
    def __init__(self, db, connector_name, mappings: list[CollectionMapping]):
        self._mappings = sorted(mappings, key=lambda m: -len(m.source_path_filter))  # most specific first
        self._writers = {m.collection_name: _SqliteChunkWriter(db, collection=m.collection_name) for m in mappings}
        self._default = _SqliteChunkWriter(db, collection=connector_name)

    def write(self, item_id: str, chunks: list[Chunk]) -> None:
        for mapping in self._mappings:
            if fnmatch(item_id, mapping.source_path_filter):
                self._writers[mapping.collection_name].upsert(chunks)
                return
        self._default.upsert(chunks)
```

**Edge case**: a single item matches MULTIPLE mappings. Two choices:
(a) most-specific-filter wins (sort by filter length); (b) first
match by config order. Recommend (a) — predictable + matches
filesystem-glob conventions. Document in the ADR.

**Edge case**: no mapping matches. Policy is in the Collection
definition (`on_unmapped_item`):
- `land_in_default_collection`: write to the connector's named default
- `drop`: silent skip with a `dropped_unmapped` counter for visibility

---

## Simulation 3 — Per-item sensitivity overrides connector default (UC-ACS-2)

**Scenario**: SharePoint item carries Purview label "abc-public".
Connector default sensitivity is "internal". Item should land at
"public" per operator's label_map.

**Model walk**:
1. Connector emits change event for item.
2. Connector's `fetch(item_id)` returns a `RawArtefact`.
3. Connector's `sensitivity_for(item_id)` returns connector default
   — but this is too late; the sensitivity should come from the
   item-level metadata in the fetch.

**Break point #3**: `SourceConnector.sensitivity_for(item_id) -> Sensitivity`
is a per-id-not-per-fetch hook. For sources with per-item labels we
need the sensitivity to flow with the RawArtefact, not be re-queried.

**Fix**: extend `RawArtefact` to carry optional `sensitivity_hint:
Sensitivity | None`. The connector emits hint when it can derive it
from metadata (Purview label, Slack channel privacy, GitHub repo
visibility, Drive sharing tier). The Silver processor applies the
hint with precedence:

```
final_sensitivity = (
    raw_artefact.sensitivity_hint
    or collection_source.sensitivity_override
    or collection.default_sensitivity
    or connector.default_sensitivity
)
```

This is the canonical fallback chain. Four sources of sensitivity
truth in declining precedence: per-item hint > per-collection-source
override > collection default > connector default.

**Edge case**: sensitivity hint is "public" but operator policy demands
nothing lower than "internal" for that connector. Resolve via a per-
connector `min_sensitivity` floor (operator-policy):

```
final_sensitivity = max(
    min_sensitivity_floor,
    hint or override or default
)
```

where `max` follows F39's order (`public < internal < confidential < restricted`).

---

## Simulation 4 — Sites.Selected revoked mid-soak (03-sharepoint)

**Scenario**: connector has been syncing site:x via Sites.Selected;
site admin revokes; next sync gets 403.

**Model walk**:
1. Worker calls `list_changes(connector, container=site-x-drive-1)`.
2. Connector returns 403 from Graph.
3. Existing pipeline's `try/except` wraps the connector-call —
   currently a 403 would be caught + the entry skipped for THIS
   sync, but no persistent state captures "this container is
   inaccessible".

**Break point #4**: the Container's `access_state` field is on the
Container model but the runtime path to set it is missing.

**Fix**: connector's per-container call returns one of three outcomes:
- `ContainerSyncResult(items, deadletter, cursor_after)` — success
- `ContainerAccessDenied(error_class, message)` — 403/401/scope-grant-revoked
- `ContainerTransient(retry_after_seconds)` — 429/503/timeout

Framework updates Container.access_state accordingly. Search-time:
collections sourcing from an "access_state=revoked" container surface
a "freshness=stale; access=revoked" envelope warning so the operator
knows a chunk-set is frozen.

**Edge case**: chunks ingested BEFORE revocation stay in the index.
Operator-policy decides whether to tombstone them on revocation
(strict) or leave them as known-stale (permissive). Recommend
permissive default with explicit `on_revocation: tombstone | retain`
config.

---

## Simulation 5 — Notion archive vs hard-delete (03-notion)

**Scenario**: page is archived (recoverable); separate page is hard-deleted (404).

**Model walk**:
1. Connector's reconcile sweep enumerates visible pages.
2. For archived: `archived=true` returned, `last_edited_time`
   updated. Connector emits `ChangeEvent(op="modified", item_id=X,
   metadata={archived: true})`.
3. For hard-deleted: not in search results, 404 on direct retrieve.
   Connector emits `ChangeEvent(op="deleted", item_id=Y)` from the
   diff between known-state and live-state.

**Break point #5**: current `ChangeEvent` has `op: "created" | "modified" | "deleted"`.
"Archived" doesn't fit "deleted" cleanly — the chunks shouldn't be
tombstoned, just flagged.

**Fix**: extend `ChangeEvent.op` enum with `archived` and
`access_lost`:

```
ChangeOp = Literal["created", "modified", "archived", "access_lost", "deleted"]
```

Semantics:
- `created` / `modified` — standard
- `archived` — soft-delete; chunks remain but marked `archived=true`
  in metadata; default search-filter excludes archived; opt-in
  `--include-archived` brings them back
- `access_lost` — the credential's grant was revoked; chunks remain
  but not re-fetchable (cannot refresh content)
- `deleted` — hard-delete; chunks tombstoned (removed from active index)

**Schema impact**: `documents` table needs `archived` boolean +
`access_lost` boolean (or a single `state` enum column).

---

## Simulation 6 — Slack message edit (03-slack)

**Scenario**: message ts=1700000000.000100 first sent "hello", later edited to "hello world".

**Model walk**:
1. Connector receives `message_changed` event with `previous_message`.
2. Connector emits `ChangeEvent(op="modified", item_id="ts:1700000000.000100", ...)`.
3. Bronze + Silver run: re-extract, re-chunk, upsert.

**Break point #6**: chunk identity. The current Silver chunker
produces chunks like `obsidian://...#0`, `obsidian://...#1` indexed
by position. A Slack edit might produce a different chunk count
(text length changed). Upsert by `(connector_name, item_id, chunk_index)`
needs to handle "previous chunk_index 3 no longer exists".

**Fix**: chunk-writer must do a per-item RESET-and-WRITE:

```sql
DELETE FROM documents WHERE collection = ? AND source_name = ? AND item_id_root = ?
-- then insert N new chunks
```

where `item_id_root` is the item_id without the `#chunk_index` suffix.

Performance concern: every modify triggers full-item rewrite. That's
acceptable for low-edit-rate sources (most things) and probably
necessary for Slack message edits anyway.

---

## Simulation 7 — GitHub force-push reconcile (03-github)

**Scenario**: branch force-pushed; commit SHA "abc123" no longer exists; chunks indexed against abc123 are now orphans.

**Model walk**:
1. Webhook fires `push` event with `forced=true`.
2. Connector enumerates current HEAD tree.
3. Existing chunks keyed by `source_uri` containing the SHA become
   orphans — no live commit references them.

**Break point #7**: tombstone reconciliation requires comparing
KNOWN chunks (indexed) vs LIVE state (current commits). Current
framework's reconciler only runs on `cursor=None` first calls. A
force-push reconcile needs operator-triggerable per-container resync.

**Fix**: connector exposes `reconcile_container(container_id, full=True) ->
list[ChangeEvent]` as an explicit Protocol method. Triggers:
- First-call (`cursor=None`)
- Periodic (every N calls, per connector config)
- Operator-on-demand via CLI: `kairix worker reconcile --connector github-org-acme --container repo:acme/secrets`
- Source-event-driven: webhook payload signals reconcile needed
  (force-push, library reorganisation, scope grant change)

---

## Simulation 8 — Cross-source entity resolution (UC-KNW-3, UC-GRP-1)

**Scenario**: "Client-X" mentioned in Obsidian / SharePoint / Notion / Dex CRM. All four should resolve to one Neo4j entity.

**Model walk**:
1. Each connector's Silver stage emits `EntitySignal` rows into
   `entity_signals` staging table with `(item_id, entity_kind,
   entity_name_normalized, provenance_link)`.
2. Curator (separate worker) periodically reads `entity_signals`,
   runs entity resolution (Dex's IDs are authoritative when present;
   fuzzy match for sources without explicit IDs), and writes to Neo4j
   with chunk back-references.

**Break point #8**: entity resolution precision. Dex gives clean
Person/Org IDs; NER-derived mentions from Obsidian / SharePoint /
Notion have fuzzy precision. Same name in two contexts may or may
not be the same entity (Acme Pty Ltd vs Acme LLC).

**Fix**: entity resolution is its own surface; design constraint here
is that the *provenance back-references* are correctly populated so
that:
- Search-time graph-anchored query at "Client-X" returns the union
  of back-referenced chunks from all four sources.
- Operator can audit "which sources contributed this entity?" via the
  Neo4j node's provenance edge.

Precision is the Curator's problem; the topology just needs the
back-references to be correct + scope-filtered at retrieval.

**Schema impact**: Neo4j entity nodes carry a list of
`(connector_name, collection_name, chunk_id, sensitivity_at_ingest)`
provenance edges. Search-time filter uses the chunk's CURRENT
sensitivity + actor's scope to decide visibility.

---

## Simulation 9 — Skill-driven multi-collection search with one collection inaccessible (UC-CMP-1)

**Scenario**: prepare-sow skill's `team-engagement-lessons` collection
is excluded from agent-builder's scope profile.

**Model walk**:
1. `agent-builder` invokes `prepare-sow` skill.
2. SearchStrategy.scope_resolve(agent-builder, prepare-sow, task) →
   ordered list of collections.
3. The skill's `task_collections` has 4 entries; agent-builder's
   profile has 3 of them as read=yes; one (team-engagement-lessons)
   is missing.

**Decision point**: do we (a) silently exclude, (b) explicitly
report exclusion, or (c) refuse to run the skill?

**Recommendation**: (b). The result envelope is structured:

```
ResultEnvelope:
  results: list[RankedChunk]
  included_collections: ["client-x-engagement", "reference-superannuation-au", "ai-operating-model-pattern"]
  excluded_collections:
    - name: "team-engagement-lessons"
      reason: "actor scope profile excludes this collection"
      escalation_hint: "fix: ask team-lead to grant read; or invoke skill with --escalation=human-review"
  total_results: 47
```

Agent decides whether to escalate (request access from a human) or
proceed with the partial result. Critical: NO silent loss of context.

---

## Simulation 10 — Aggregated query least-permissive intersection (UC-ACS-3)

**Scenario**: query on-behalf-of [agent-shape, agent-builder] where
shape sees [A, B], builder sees [B, C].

**Model walk**:
1. Caller passes `actors=[agent-shape, agent-builder]`.
2. SearchStrategy.scope_resolve intersects profiles:
   `(A ∩ B) ∩ (B ∩ C) ∩ ... = B`
3. Search runs over collection B only.

**Break point #9** (subtle): max_sensitivity rules per-collection
may differ between actors. E.g. on collection B, shape's
max_sensitivity is "internal", builder's is "confidential". Intersection
yields "internal" (least permissive).

**Fix**: scope-profile composition rule:
- For collection presence: intersection (`A ∩ B`).
- For max_sensitivity: min across actors' caps (F39-ordered min).
- For write rights: AND across actors (composition is read-mostly;
  write-on-behalf-of-many is rare and requires explicit caller authz).

**Edge case**: caller passes `scope_composition: "union"` with a
"superuser" authz token to expand to A ∪ C. Authz check happens at
the search-pipeline boundary, not in scope_resolve.

---

## Simulation 11 — Webhook + reconciler universality (cross-cutting)

**Scenario**: every source supports both push (webhooks / events / sockets)
AND poll (delta query / reconcile). Connector framework should provide
a Protocol-level pattern, not per-connector reinvention.

**Model walk**:
1. ConnectorInstance config carries `freshness_strategy`:
   ```yaml
   freshness_strategy:
     push:
       enabled: true
       endpoint: https://kairix.tld/webhooks/sharepoint-corp
       subscription_renewal_interval: "2d"  # default per source's expiry
     poll:
       interval: "5m"
       reconcile_every_n_polls: 12  # full-walk every hour
     fallback_on_push_miss:
       max_acceptable_lag: "10m"
   ```
2. Worker manages subscription lifecycle (renew before expiry).
3. Worker polls per the schedule; pushes wake the poll early.

**Break point #10**: subscription renewal is per-source-specific. The
Protocol shape needs:

```python
class SourceConnector(Protocol):
    def subscribe(self, callback_url: str) -> Subscription | None:
        """Return subscription handle, or None if push not supported."""
    def renew_subscription(self, subscription: Subscription) -> Subscription:
        """Renew before expiry; raises if revoked."""
    def unsubscribe(self, subscription: Subscription) -> None:
        ...
```

Connectors that don't support push (Dex CRM today, possibly) return
None from `subscribe` and the framework falls back to poll-only.

---

## Simulation 12 — Iterative search primitive (UC-MEM-3, UC-CMP-1)

**Scenario**: "what did shape do last task?" → expand with "what
files did shape touch in Notion for that task?" → expand with "any
mentions of vendor X in those files?"

**Model walk**:
1. Initial query returns top-K chunks.
2. Agent inspects results, formulates follow-up.
3. Follow-up query carries the prior results as context (e.g. "narrow
   to chunks authored by agent-shape", "filter to notion-source").
4. Repeat.

**Break point #11**: kairix's current search is one-shot. The
iteration lives in the calling agent. That's fine for now but makes
skill-defined iterative strategies (UC-CMP-1's `prepare-sow`
defining per-task_collection sequential search) hard to express
declaratively.

**Fix**: add `tool_iterative_search(strategy: SearchStrategy, query: str)`
as an MCP tool. The strategy enumerates an ordered list of
sub-queries with carryover context; the tool runs them server-side
and returns a composite envelope. Agent-side iteration is still
possible but kairix-side iteration is now an option.

---

## Performance + non-functional break points

Performance modelling per connector (detailed in `05-non-functionals.md`):
- **Obsidian**: O(file_count × hash_cost) initial walk; sub-second
  realtime via watchdog. 5,754-file vault = seconds. Storage: ~12 chunks/file × ~150B/chunk = ~2KB/file.
- **SharePoint**: 47-drive tenant initial delta = throttled hours.
  Webhook lag = seconds. Storage: doc-conversion-dependent (pptx + pdf
  dominate).
- **Notion**: 3 req/s ceiling. 10k-page workspace re-scan ≈ minutes
  of API time. Webhook beta lag = seconds.
- **Slack**: Tier 3 50 req/min `conversations.history`. Backfill of
  1M messages ≈ hours. Webhook lag = seconds.
- **GitHub**: 5000 req/h PAT, 5000–15000 App. Monorepo tree-walk
  truncates >100k entries — fall back to clone.
- **Google Drive**: 1000 q/100s per user. DWD domain backfill = hours.
- **Dex CRM**: 1 req/s self-rate-limited. Whole tenant ≈ minutes.

**Break point #12**: initial-backfill cost is dominated by per-item
fetch + conversion (for office docs especially). The model must
support PARALLEL per-container backfill (one drive at a time within
SharePoint, with bounded concurrency). Current ConnectorPipeline is
single-threaded per connector.

**Fix**: ConnectorPipeline gains a per-container worker pool:

```python
class ConnectorPipeline:
    def run_batch(self, connector, extractor, max_concurrent_containers: int = 4):
        with ThreadPoolExecutor(max_workers=max_concurrent_containers) as ex:
            futures = [ex.submit(self._process_container, container, connector, extractor) for container in connector.iter_containers()]
            ...
```

Concurrency cap is per-connector (respect source rate limits). Cap
defaults sized to rate-limit budgets in `05-non-functionals.md`.

---

## Failure-mode catalogue (consolidated)

| Failure mode | Source kinds affected | Surface |
|---|---|---|
| Credential expiry / revocation | all | `ContainerAccessDenied`; collection's freshness envelope reports stale |
| Subscription expiry (push) | sharepoint, notion (beta), drive | renewer in worker; on miss, poll catches up |
| Rate limit (429 / 503 with Retry-After) | all push/poll | `ContainerTransient(retry_after)`; framework respects |
| Source archive (recoverable) | notion, github archived repos | `op="archived"`; chunks retained, metadata flag set |
| Source hard-delete | all | `op="deleted"`; chunks tombstoned |
| Force-push / non-monotonic content | github | `reconcile_container(full=True)` re-walks state |
| Free-tier age cap | slack | `op="access_lost"` for messages outside window; metadata note |
| Multi-homing (file in multiple drives) | google drive | dedupe by file ID in index, source_uri may be ambiguous |
| Sensitivity-label change after ingest | sharepoint | next sync detects via delta; chunk's sensitivity updates |
| Operator config drift (label_map mismatch) | sharepoint, notion, slack | sensitivity falls back to connector default; visible in audit |

---

## Schema changes summary

The simulations identify these schema deltas vs current code:

1. `connector_cursors` table: add `container_id` to primary key.
2. `documents` table: add `archived` + `access_lost` boolean (or
   `state` enum); add `item_id_root` for per-item reset semantics.
3. New `connector_containers` table: `(connector_name, container_id,
   access_state, last_synced_at)`.
4. New `collection_definitions` table or YAML config block:
   `(name, default_sensitivity, on_unmapped_item)`.
5. New `collection_sources` table: `(collection_name, connector_name,
   source_path_filter, sensitivity_override)`.
6. New `scope_profiles` config block: per-actor scope entries.
7. New `skills` config block: per-skill task_collections + ranking +
   iteration.
8. `RawArtefact` dataclass: add `sensitivity_hint`.
9. `ChangeEvent.op` enum: add `archived` + `access_lost`.

Protocol changes:

10. `SourceConnector.iter_containers()` — new method.
11. `SourceConnector.list_changes(container, cursor)` — per-container.
12. `SourceConnector.subscribe()/renew_subscription()/unsubscribe()` — optional push surface.
13. `SourceConnector.reconcile_container(container_id, full=True)` — operator-triggerable.

ConnectorPipeline changes:

14. Per-container concurrent execution with rate-limit-budget caps.
15. `CollectionRouter` between connector emit + chunk-write.
16. `EnvelopeBuilder` that reports included / excluded collections + per-source freshness.

---

## Next: ADR

The simulation converges. `05-non-functionals.md` quantifies the
performance + storage envelope under the new model; the ADR
synthesises 01–05 into the canonical decision + migration plan.

Open design choices the ADR must resolve:
- (a) `kind` separate from `name`, OR `name` = kind by convention with collection separate (back-compat lean)
- (b) Collection definitions in YAML config OR a dedicated `collections` table writable via CLI
- (c) Scope profiles per-actor OR per-team OR both (both is the answer)
- (d) Skill definitions as YAML config (no runtime mutation) OR a dedicated `skills` table
- (e) Push-subscription state in the connector instance OR a dedicated `subscriptions` table

Recommend (a) `kind` + `name` distinct; (b) YAML for collection definitions
with optional CLI surface to author them; (c) both (scope_profile has
optional `team_id`); (d) YAML for skills; (e) dedicated `subscriptions`
table because lifecycle (renew, expire) is independent of connector
state.
