# ADR — Connector / Collection / Scope Topology

**Status**: Proposed (2026-05-23). Pending review during IM-6 soak window.

**Context**: The current connector framework (Wave 0–5) hardwires
`name = entry-point key = SQL collection = cursor scope = source
identity`. That overload is only honest for the simplest case (one
credential per logical store, one collection per credential, no
per-actor scope enforcement). Every other source kind we care about
(SharePoint, Notion, M365, Slack, GitHub, Google Drive) and every
common retrieval pattern (memory tiers, team-scoped search, skill-driven
SoW preparation, graph-anchored entity briefing) requires the topology
to separate concerns that today share one field.

Companion analysis: `00-overview.md` through `05-non-functionals.md`.
The simulation in `04-simulation.md` walked the layered model against
every use case + per-connector scenario and identified 12 concrete
break points; this ADR resolves each with a schema + interface
decision.

## Decision

Adopt a five-layer model with explicit interface boundaries:

### 1. Connector instance (credential boundary)

```python
@dataclass(frozen=True)
class ConnectorInstance:
    name: str                       # unique instance id (cursor + deadletter scope)
    kind: str                       # entry-point key (obsidian, sharepoint, …)
    credential_ref: str | None      # secret-store path (kv://, file://, env://)
    config: Mapping[str, Any]       # kind-specific config
    default_sensitivity: F39Tier    # fallback per F39 chain
    freshness_strategy: FreshnessStrategy  # push + poll + reconcile cadence
```

YAML shape in `kairix.config.yaml`:

```yaml
connectors:
  - name: sharepoint-corp
    kind: sharepoint
    credential_ref: kv://kairix/sharepoint-app-tenant-credentials
    config:
      tenant_id: <tenant-uuid>
      app_id: <app-uuid>
      site_filter: ["site:engineering", "site:research"]
    default_sensitivity: internal
    freshness_strategy:
      push: { enabled: true, endpoint: https://kairix.tld/webhooks/sharepoint-corp }
      poll: { interval: 5m, reconcile_every_n_polls: 12 }
```

Key change vs current: `kind` is distinct from `name`. Two instances
can share `kind` (multi-vault Obsidian, multi-tenant Dex). Migration
defaults `kind = name` for back-compat.

### 2. Container (per-instance internal scope unit)

```python
@dataclass(frozen=True)
class Container:
    connector_name: str
    container_id: str               # kind-specific opaque string (drive_id, mailbox_id, channel_id, …)
    access_state: Literal["accessible", "revoked", "not_yet_granted", "transient_error"]
    cursor_token: str | None
    last_synced_at: datetime
```

Schema: new `connector_containers` table; primary key `(connector_name, container_id)`. The existing
`connector_cursors` table is **deprecated** in favour of this richer
shape (migration: existing rows become `(connector_name, container_id=connector_name, cursor_token, ...)`).

Connector Protocol gains:

```python
class SourceConnector(Protocol):
    def iter_containers(self) -> Iterator[Container]: ...
    def list_changes(self, container: Container) -> Iterator[ChangeEvent]: ...
    def reconcile_container(self, container_id: str, *, full: bool = True) -> Iterator[ChangeEvent]: ...
    def subscribe(self, callback_url: str) -> Subscription | None: ...        # optional
    def renew_subscription(self, sub: Subscription) -> Subscription: ...      # optional
```

`ChangeEvent.op` extends to:
`"created" | "modified" | "archived" | "access_lost" | "deleted"`.

`RawArtefact` extends with `sensitivity_hint: F39Tier | None`.

### 3. Collection (retrieval bucket — decoupled from connector)

```python
@dataclass(frozen=True)
class Collection:
    name: str
    sources: tuple[CollectionSource, ...]
    default_sensitivity: F39Tier
    on_unmapped_item: Literal["land_in_default_collection", "drop"]
    visibility: Literal["public", "engagement", "team", "private"]

@dataclass(frozen=True)
class CollectionSource:
    connector_name: str
    source_path_filter: str             # glob on item_id
    sensitivity_override: F39Tier | None
```

YAML shape:

```yaml
collections:
  - name: vault-projects
    sources:
      - connector_name: obsidian-personal
        source_path_filter: "01-Projects/**"
    default_sensitivity: internal

  - name: client-x-engagement      # aggregates across connectors
    sources:
      - { connector_name: obsidian-personal, source_path_filter: "01-Projects/Client-X/**" }
      - { connector_name: sharepoint-corp,   source_path_filter: "site:client-x/**" }
      - { connector_name: dex-crm-personal,  source_path_filter: "orgs/client-x" }
    default_sensitivity: confidential
    visibility: engagement
```

Runtime layer: `CollectionRouter` consumes `(connector_name, item_id)`
and routes chunks to the matching collection's chunk-writer.
Most-specific filter wins (sort by filter length).

Schema: new `collections` and `collection_sources` tables (auto-populated from YAML on worker boot for visibility / audit).

### 4. Scope profile (per-actor access)

```python
@dataclass(frozen=True)
class ScopeProfile:
    actor_id: str
    actor_kind: Literal["agent", "human", "team", "skill"]
    entries: tuple[ScopeEntry, ...]

@dataclass(frozen=True)
class ScopeEntry:
    collection_name: str
    read: bool
    write: bool
    max_sensitivity: F39Tier          # cap; entries at higher tier excluded
```

YAML shape:

```yaml
scope_profiles:
  - actor_id: agent-shape
    actor_kind: agent
    entries:
      - { collection_name: agent-shape/private-memory,   read: yes, write: yes,  max_sensitivity: restricted }
      - { collection_name: team-shape-builder/decisions, read: yes, write: yes,  max_sensitivity: confidential }
      - { collection_name: team-shape-builder/lessons,   read: yes, write: no,   max_sensitivity: confidential }
      - { collection_name: reference-library,            read: yes, write: no,   max_sensitivity: public }
      # team-legal/contracts deliberately absent → no read

  - actor_id: team-shape-builder
    actor_kind: team
    entries:
      # team-level entries inherited by team members per membership map
```

### 5. Skill (composable search strategy)

```python
@dataclass(frozen=True)
class Skill:
    name: str
    task_collections: tuple[TaskCollection, ...]
    ranking: str                      # named strategy key
    iteration: Literal["one_shot", "sequential_per_task_collection", "graph_anchored"]

@dataclass(frozen=True)
class TaskCollection:
    name: str                         # may match real Collection or be virtual aggregator
    sources: tuple[CollectionSource, ...]
    weight: float
```

YAML shape:

```yaml
skills:
  - name: prepare-sow
    task_collections:
      - name: client-x-engagement
        weight: 1.0
        sources: ...
      - name: reference-superannuation-au
        weight: 0.7
        sources: ...
      - name: ai-operating-model-pattern
        weight: 0.8
        sources: ...
      - name: team-engagement-lessons
        weight: 0.5
        sources: ...
    ranking: fuse_then_rerank_by_skill_priors
    iteration: sequential_per_task_collection
```

### 6. Search strategy (runtime resolver)

```python
class SearchStrategy(Protocol):
    def scope_resolve(self, actors: list[str], skill: str | None, task: str) -> ResolvedScope: ...
    def execute(self, scope: ResolvedScope, query: str) -> ResultEnvelope: ...

@dataclass(frozen=True)
class ResolvedScope:
    collections: tuple[ResolvedCollection, ...]
    excluded_collections: tuple[ExcludedCollection, ...]      # for envelope transparency

@dataclass(frozen=True)
class ResolvedCollection:
    name: str
    max_sensitivity: F39Tier          # effective cap (intersection across actors)
    weight: float                     # from skill if present

@dataclass(frozen=True)
class ExcludedCollection:
    name: str
    reason: Literal["actor_lacks_read", "sensitivity_cap_too_high", "container_revoked", "container_transient"]
    escalation_hint: str | None
```

Multi-actor composition: collections by intersection, `max_sensitivity`
by F39-min, write-rights by AND. Caller can opt into union via
authorised `scope_composition: "union"` flag.

`ResultEnvelope` includes per-source freshness (last_synced, age,
state), included / excluded collections (no silent loss of context),
and per-collection result contribution counts.

---

## Resolved break points (from `04-simulation.md`)

| # | Break | Resolution |
|---|---|---|
| 1 | SourceConnector single-cursor | `iter_containers()` + per-container `list_changes(container)` |
| 2 | Chunk-writer bound to one collection per connector | `CollectionRouter` per-connector with per-collection chunk-writers |
| 3 | sensitivity_for hook is too late | `RawArtefact.sensitivity_hint` + four-step fallback chain |
| 4 | Sites.Selected revocation not modelled | `Container.access_state` + `ContainerAccessDenied` outcome |
| 5 | archive vs delete | `ChangeEvent.op` enum extended; `documents.archived` + `access_lost` columns |
| 6 | Slack message edit | per-item-root RESET-and-WRITE; chunk index re-derived |
| 7 | GitHub force-push | `reconcile_container(full=True)` Protocol method, operator + event triggerable |
| 8 | Cross-source entity resolution | unchanged (Curator's surface); provenance edges enable scope-filtered chunk back-refs |
| 9 | Inaccessible task_collection | `ResultEnvelope.excluded_collections` with reason + escalation_hint |
| 10 | Aggregated query intersection | composition rule (∩ for collections, F39-min for sensitivity) |
| 11 | Webhook + reconciler universal | Protocol-level optional `subscribe`/`renew_subscription`/`unsubscribe`; framework-managed |
| 12 | Parallel per-container backfill | ConnectorPipeline gains bounded ThreadPool (cap defaults per `05-non-functionals.md`) |

---

## Migration plan

Five waves; each is reversible-until-validated via a feature flag per
`feature-flag-architecture.md`.

### Wave A — schema additions (back-compat)

Add net-new tables / columns without breaking existing code:
- `connector_containers` (alongside existing `connector_cursors`)
- `collections` + `collection_sources`
- `documents.archived` + `documents.access_lost` columns (default false)
- `RawArtefact.sensitivity_hint` field (default None)
- `ChangeEvent.op` enum extended (back-compat)

Feature flag: `topology_v2_schema` (introduce stage default-off; only
controls whether new tables are populated).

### Wave B — connector Protocol extensions

Extend `SourceConnector` Protocol with optional new methods:
- `iter_containers()` — default impl yields one container with `container_id=connector.name`
- `list_changes(container)` — default impl delegates to existing `list_changes(cursor)` for single-container connectors
- `reconcile_container(container_id, full=True)` — default impl re-runs `list_changes(cursor=None)`
- `subscribe()/renew_subscription()/unsubscribe()` — optional; default returns None (poll-only)

All existing connectors (obsidian, dex_crm, m365_email_headers,
m365_calendar) get the default impls without behavioural change.
Feature flag: `topology_v2_protocol` (introduce stage default-off).

### Wave C — runtime: CollectionRouter + ScopeProfile resolver

Land:
- `CollectionRouter` in ConnectorPipeline
- `ScopeProfileResolver` reading `scope_profiles` YAML
- `ResultEnvelope` extended with per-source freshness + included /
  excluded collections

Feature flag: `topology_v2_runtime` (introduce stage default-off).
When OFF: behaviour is identical to current (collection = connector
name, no scope enforcement at search). When ON: new behaviour kicks
in. Both branches tested per F54.

### Wave D — operator config promotion

Add `collections:` + `scope_profiles:` + `skills:` blocks to the
operator config schema. Validation rules:
- `collections.*.sources.*.connector_name` must reference a declared
  connector instance
- `scope_profiles.*.entries.*.collection_name` must reference a
  declared collection
- `skills.*.task_collections.*.sources.*.connector_name` must
  reference a declared connector

`kairix features status` extended to show collection-routing /
scope-resolution diagnostics per actor.

### Wave E — connector-side opt-in to multi-container

For each connector kind that benefits (SharePoint, Notion, Slack,
GitHub, Google Drive), the per-kind connector implementation overrides
`iter_containers()` to enumerate its real container set + uses the
per-container `list_changes(container)` path. Default sensitivity hint
emission per kind.

This is a per-connector flag (e.g. `topology_v2_sharepoint`) so each
plugin's adoption is independent. F54 requires both-branch tests per
flag.

After Wave E for all kinds: retire `topology_v2_*` flags + delete the
default-impl shims; the protocol becomes "thick" at the connector
side. ~12 months from Wave A landing.

---

## Operator-visible config example (post-migration)

```yaml
connectors:
  - name: obsidian-personal
    kind: obsidian
    config: { vault_root: /data/vaults/personal }
    default_sensitivity: internal

  - name: sharepoint-corp
    kind: sharepoint
    credential_ref: kv://kairix/sharepoint-app
    config:
      tenant_id: <uuid>
      sensitivity_label_map: { abc-public: public, abc-internal: internal, abc-confidential: confidential }
    default_sensitivity: internal
    freshness_strategy:
      push: { enabled: yes, endpoint: ... }
      poll: { interval: 5m }

collections:
  - name: vault-projects
    sources:
      - { connector_name: obsidian-personal, source_path_filter: "01-Projects/**" }

  - name: client-x-engagement
    sources:
      - { connector_name: obsidian-personal, source_path_filter: "01-Projects/Client-X/**" }
      - { connector_name: sharepoint-corp,   source_path_filter: "site:client-x/**" }
    default_sensitivity: confidential

scope_profiles:
  - actor_id: agent-shape
    actor_kind: agent
    entries:
      - { collection_name: agent-shape/private-memory, read: yes, write: yes, max_sensitivity: restricted }
      - { collection_name: vault-projects,             read: yes, write: no,  max_sensitivity: internal }
      - { collection_name: client-x-engagement,        read: yes, write: no,  max_sensitivity: confidential }

skills:
  - name: prepare-sow
    task_collections:
      - { name: client-x-engagement,         weight: 1.0 }
      - { name: reference-superannuation-au, weight: 0.7 }
    ranking: fuse_then_rerank_by_skill_priors
    iteration: sequential_per_task_collection
```

---

## Consequences

### Wins

- **Multi-vault Obsidian works** (the conversation-starter for this ADR).
- **Multi-tenant connectors** (multiple Dex instances, multiple
  SharePoint tenants) are first-class.
- **Cross-source collections** (client-x-engagement aggregating
  Obsidian + SharePoint + Dex) become trivial to declare.
- **Per-actor + per-team access control** moves from operator-trust to
  enforcement layer.
- **Per-skill task scoping** (prepare-sow, triage-morning, brief-on-X)
  becomes declarative.
- **Per-item sensitivity** (SharePoint labels, Slack channel privacy,
  GitHub repo visibility) overrides connector defaults via fallback chain.
- **Freshness transparency** in every result envelope — agents make
  informed decisions about staleness.
- **Push + poll universality** — every source uses the same shape;
  no per-connector subscription-lifecycle reinvention.

### Tradeoffs

- **Schema breadth**: 3 new tables, multiple existing tables extended.
  Migration is back-compat per Wave A but adds operational surface.
- **Operator config grows**: `collections`, `scope_profiles`, `skills`
  blocks add YAML lines. Mitigated by sensible defaults (a deployment
  with one connector + no scope profile + no skills behaves like today).
- **Protocol surface widens**: `SourceConnector` gains 5 methods, 3
  optional. Default impls keep existing plugins running unchanged.
- **Cognitive load on agents using kairix**: scope profile + skill +
  task is more concept-set than "give me top-K". Mitigated by sensible
  defaults — most calls still produce sensible results with no skill
  invocation.

### Non-consequences (deliberately not addressed)

- **Curator + entity resolution semantics** — unchanged; the topology
  hooks into the existing Neo4j surface via chunk back-references.
- **Hybrid retrieval pipeline** (BM25 + vector + RRF + intent + boosts)
  — unchanged; we change WHAT collections it scopes over, not HOW it ranks.
- **Bronze / Silver / chunk-writer internals** — unchanged within a
  connector run.
- **Two-scope (engagement vs firm) boundary** per ADR-017 — unchanged;
  the topology operates within one engagement scope.

---

## Acceptance criteria

The ADR is considered landed when:

1. Wave A schema + Wave B Protocol shims merge with feature flags off
   (no behavioural change).
2. F54 both-branch tests exist for every new flag.
3. Wave C runtime lands with `obsidian-personal` running in
   per-folder routing mode on the dogfood VM — proves the
   `CollectionRouter` end-to-end without depending on multi-connector
   integrations.
4. At least one Wave E connector (sharepoint OR notion) lands in
   multi-container mode with both-branch tests, proving the
   per-container cursor + sensitivity-hint paths.
5. `kairix features status` shows the topology v2 surface; operator
   config reference doc lands at
   `docs/operations/configuration/connectors-and-collections.md`.

After all five: the IM-6 obsidian cutover promotes to cutover-stage
(default = true) under the new topology, not the legacy
single-collection shape.

---

## References

- `00-overview.md` — package nav + non-goals
- `01-source-analysis.md` — per-connector auth / scope / freshness / storage
- `02-use-cases.md` — 14 use cases across memory / knowledge /
  composite / graph / access modalities
- `03-bdd-scenarios.md` — Gherkin scenarios pinning each topology decision
- `04-simulation.md` — model walked, 12 break points identified +
  resolved
- `05-non-functionals.md` — storage / freshness / latency / cost
  envelopes per connector
- `connector-ingestion-architecture.md` — the Wave 0-5 framework this
  builds on
- `feature-flag-architecture.md` — the flag pattern Wave A-E adopt
- `provider-plugin-architecture.md` — the parallel three-layer split
  this mirrors for connectors
- `fact-layer.md` — entity resolution surface this composes with
- `ADR-017` (kairix-pro repo) — engagement / firm scope boundary
