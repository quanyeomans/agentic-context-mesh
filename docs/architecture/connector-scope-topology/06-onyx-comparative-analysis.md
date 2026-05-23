# Onyx connector framework — comparative analysis

Fetched from [`onyx-dot-app/onyx`](https://github.com/onyx-dot-app/onyx) `main` at commit `cd7c86e78f20373c5459a174e70a9da9740f1038` (2026-05-22), specifically `backend/onyx/connectors/`, `backend/onyx/access/`, and `backend/onyx/db/`. Onyx is a comparable open-source enterprise search platform shipping ~48 connectors. Use this analysis to (a) adopt patterns that solve problems we hadn't yet noticed, (b) reject patterns we have a cleaner shape for, (c) preserve patterns where our model is strictly richer.

> **Caveat (per the conversation)**: Onyx solved their problems, not necessarily ours. Some Onyx choices reflect tactical shipping pressure rather than considered architecture. Mining for ideas, not gospel.

---

## 1. Connector framework architecture (`backend/onyx/connectors/interfaces.py`)

Onyx uses `abc.ABC` abstract base classes, **not** `typing.Protocol`. The base is `BaseConnector(abc.ABC, Generic[CT])` where `CT` is bounded by `ConnectorCheckpoint`. The abstract surface is small:

```python
class BaseConnector(abc.ABC, Generic[CT]):
    REDIS_KEY_PREFIX = "da_connector_data:"
    raw_file_callback: RawFileCallback | None = None

    @abc.abstractmethod
    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None: ...
    def validate_connector_settings(self) -> None: ...
    def set_allow_images(self, value: bool) -> None: ...
    def set_raw_file_callback(self, callback: RawFileCallback) -> None: ...
    def build_dummy_checkpoint(self) -> CT: ...
```

Capability is layered through **mix-in ABCs**, each adding one abstract method:

- `LoadConnector` — `load_from_state() → GenerateDocumentsOutput`
- `PollConnector` — `poll_source(start, end)` (deprecated; checkpointed is canonical)
- `SlimConnector` — `retrieve_all_slim_docs(start, end, callback)` (id-only, used for prune/dedup)
- `SlimConnectorWithPermSync` — slim retrieval with per-doc ACL
- `EventConnector` — `handle_event(event)` (webhooks)
- `CheckpointedConnector[CT]` — `load_from_checkpoint(start, end, checkpoint) → CheckpointOutput[CT]`
- `CheckpointedConnectorWithPermSync` — checkpointed + ACL
- `Resolver` — `reindex(errors, include_permissions)` for retrying failed documents
- `HierarchyConnector` — `load_hierarchy(start, end) → HierarchyOutput`
- `OAuthConnector` — class methods `oauth_id`, `oauth_authorization_url`, `oauth_code_to_token`
- `CredentialsConnector` — `set_credentials_provider(provider)`

A concrete connector announces capabilities by inheritance. `ConfluenceConnector` declares `CheckpointedConnector[ConfluenceCheckpoint], SlimConnector, SlimConnectorWithPermSync, CredentialsConnector, Resolver` — five capabilities composed by inheritance. `ConnectorRunner.run()` dispatches via `isinstance` checks against each mix-in.

**Our shape vs Onyx's**: We use `typing.Protocol` (structural typing) and a flatter surface. Onyx's mix-in-by-inheritance pattern is more explicit at the declaration site (you can SEE which capabilities a connector advertises) but less flexible for static type-narrowing. **Recommendation**: keep Protocol but borrow the **capability surface** — split our `SourceConnector` into the same dimensional axes (poll vs event, full vs slim, permsync, resolver). Each axis becomes an optional Protocol; connector implementations satisfy whichever apply. Operator can introspect at runtime via `isinstance(connector, SlimConnector)` style checks. This is the right middle ground.

---

## 2. Per-connector parametrisation — the cc_pair triad

Onyx uses a **`Connector` (kind + config) × `Credential` × `ConnectorCredentialPair` triad** instead of a flat instance:

```python
class Connector(Base):
    id: int
    source: DocumentSource
    input_type: InputType
    connector_specific_config: dict[str, Any]   # JSONB
    refresh_freq: int | None
    prune_freq: int | None

class Credential(Base):
    id: int
    source: DocumentSource
    credential_json: SensitiveValue[dict[str, Any]] | None   # EncryptedJson
    user_id: UUID | None
    admin_public: bool

class ConnectorCredentialPair(Base):
    connector_id: int
    credential_id: int
    name: str
    access_type: AccessType                                  # PUBLIC | PRIVATE | SYNC
    status: ConnectorCredentialPairStatus
    last_successful_index_time: datetime | None
    last_time_perm_sync: datetime | None
    last_time_external_group_sync: datetime | None
    last_time_hierarchy_fetch: datetime | None
    total_docs_indexed: int
```

"Two Confluence instances" = two `Connector` rows (each with different `connector_specific_config`), bound to one or two `Credential` rows via `ConnectorCredentialPair`. The cc_pair is the **operational unit** — timestamps, status, access type all live there.

**Onyx's cleaner separation than our proposal**: in our 5-layer model we conflated "what to talk to" with "the binding". Onyx separates:
- `Connector` (the configured target — Confluence pointed at site:X with query:Y)
- `Credential` (the auth shape — a Confluence token)
- `ConnectorCredentialPair` (the binding — its own cursor, its own status, its own audit timestamps, its own access mode)

**Recommended adoption**: replace our `ConnectorInstance` with the triad. Same credential can drive two scoped connectors (one ingesting site:X, another ingesting site:Y) OR same scope can rotate credentials (token A this week, token B next week) without losing operational state. The cc_pair_id becomes the cursor + access-state key — which fixes a class of issues our flat model wouldn't have surfaced until much later.

---

## 3. Scope hierarchy inside one credential — checkpoint-embedded

Onyx puts intra-credential scope hierarchy **inside the checkpoint blob**, with connector `__init__` taking the operator's scope filter list. SharePoint is the clearest example:

```python
class SharepointConnectorCheckpoint(ConnectorCheckpoint):
    cached_site_descriptors: deque[SiteDescriptor] | None = None
    current_site_descriptor: SiteDescriptor | None = None
    cached_drive_names: deque[str] | None = None
    current_drive_name: str | None = None
    current_drive_delta_next_link: str | None = None
    seen_hierarchy_node_raw_ids: set[str] = Field(default_factory=set)
    seen_document_ids: set[str] = Field(default_factory=set)
```

So Onyx tracks per-site-per-drive cursor state inside ONE logical checkpoint blob, not as separate cursor rows. Slack mirrors this: `SlackCheckpoint` carries `channel_ids: list[str]`, `channel_completion_map: dict[str, str]` (channel_id → earliest-processed-ts), `current_channel`.

**Comparison to our proposal**: we proposed `connector_containers` table with `(connector_name, container_id, cursor_token)` rows. Onyx puts the equivalent in a JSONB blob inside one checkpoint row per cc_pair. Pros of Onyx's shape: easier to evolve schema (no migration when adding fields). Pros of ours: queryable (operator can SQL "which containers are stale?"). **Recommended hybrid**: keep our `connector_containers` table as the normalised primary; serialise per-cc_pair checkpoint blob for any in-flight processing state (current_drive_name, seen_document_ids per-batch) — that lives in a JSONB column on the cc_pair status row.

`HierarchyNode` (in `connectors/models.py`) is Onyx's first-class scope-tree emission:

```python
@dataclass
class HierarchyNode:
    raw_node_id: str
    raw_parent_id: str | None
    display_name: str
    link: str | None
    node_type: HierarchyNodeType    # 12-value enum
    external_access: ExternalAccess | None

class HierarchyNodeType(Enum):
    FOLDER, SOURCE, SHARED_DRIVE, MY_DRIVE, SPACE, PAGE, PROJECT,
    DATABASE, WORKSPACE, SITE, DRIVE, CHANNEL
```

Connectors emit parent nodes before child documents (parent-before-child invariant) so the graph is well-formed.

**This is new territory for us.** Our 5-layer model has scope-aware ingestion but no first-class emission of the source's own folder tree. **Recommendation**: adopt `HierarchyNode` and `HierarchyNodeType` as first-class emissions. Unlocks "files in this folder", "siblings of this doc", "all docs under site:X" navigation in the retrieval surface without re-deriving structure from `source_uri` prefixes. Persist in a `hierarchy_nodes` table parallel to `documents`.

---

## 4. Sensitivity / permissions model

Onyx propagates per-item ACL via `ExternalAccess`:

```python
@dataclass(frozen=True)
class ExternalAccess:
    external_user_emails: set[str]
    external_user_group_ids: set[str]
    is_public: bool
    MAX_NUM_ENTRIES = 5000
```

Every `Document`, `SlimDocument`, and `HierarchyNode` carries optional `external_access: ExternalAccess | None`. `Document` SQLAlchemy persists this as three columns: `external_user_emails: ARRAY(String)`, `external_user_group_ids: ARRAY(String)`, `is_public: bool`. `DocExternalAccess` and `NodeExternalAccess` bind those sets to a specific `doc_id` or `raw_node_id` for the perm-sync pipeline.

**There is NO symbolic sensitivity tier in Onyx**. Visibility is `is_public: bool` + explicit ACL set. The closest tiering is `AccessType` on the cc_pair: `PUBLIC | PRIVATE | SYNC` — per-source-instance, not per-document. `SYNC` means "pull ACLs from source and enforce"; `PRIVATE` means "anyone with access to this cc_pair sees everything in it"; `PUBLIC` means "any Onyx user".

**Our F39 sensitivity tier is strictly richer.** Onyx can't express "this chunk is `confidential` regardless of which group it's shared with" — they'd have to encode that in group membership. We can. **Recommendation**: keep F39 as-is; additionally adopt `external_access` (or equivalent) as a complementary axis for permission-sync-from-source. The two together: F39 tier (policy-driven, operator-controlled) PLUS ACL set (source-enforced membership), intersected at search time.

`AccessType.SYNC` on the cc_pair is worth adopting as a per-source-instance setting: "this Confluence instance enforces source ACLs; that file-share instance is 'engagement-public'". Complements our scope-profile model.

---

## 5. Change detection — four orthogonal capability mix-ins

No single push/poll abstraction; instead:

- `LoadConnector` — full reload (FileConnector, full Notion dumps)
- `PollConnector` — `poll_source(start, end)` window pull (deprecated for new code)
- `CheckpointedConnector` — resumable per-batch checkpointed pull (strategic shape)
- `EventConnector` — `handle_event(event)` for webhook pushes

Scheduling lives outside the connector: cc_pair has `refresh_freq: int | None` (indexing) and `prune_freq: int | None` (slim-doc reconciliation). The orchestrator schedules per-cc_pair attempts and feeds the checkpoint forward. **There's no built-in webhook subscription lifecycle manager** in Onyx's connector framework — webhook connectors are rare; Slack uses long-polling rather than RTM/Socket Mode in the indexing path.

Reconciliation via slim-doc surface: `SlimConnector.retrieve_all_slim_docs()` returns id-only documents covering the full scope. Orchestrator diffs against the index to prune deletions. `CheckpointedConnectorWithPermSync` does the same for ACLs.

Failed-document re-extraction via `Resolver.reindex(errors, include_permissions)` — re-pulls only failed docs.

**Recommended adoptions**:
- `SlimConnector`-equivalent capability — id-only enumeration for prune cycles. This is the canonical deletion-detection pattern we were missing. Pair with our existing `list_changes` (cursor-based ingest) as a SEPARATE capability with its own schedule.
- `Resolver.reindex(failures)` capability — per-doc failure replay. Cheaper than re-running full window.
- Per-cc_pair `refresh_freq` + `prune_freq` separate from each other.

**Reject**: Onyx's lack of universal webhook lifecycle manager. Our proposal (framework-managed subscriptions with renewal) is correct; Onyx's gap is a known limitation.

---

## 6. Cross-connector collections — `DocumentSet`

Yes — `DocumentSet` is exactly our `Collection` concept:

- Aggregates many `ConnectorCredentialPair`s through `DocumentSet__ConnectorCredentialPair` junction
- Can include `FederatedConnector` mappings (external search-index pointers)
- Maps to `UserGroup` via `DocumentSet__UserGroup` for access control
- Search-time filters narrow results to a set's cc_pairs (and federated connectors)

Operators build "Engineering Knowledge" = (GitHub cc_pair) ∪ (Confluence cc_pair) ∪ (Slack cc_pair) and expose as one filterable bucket.

**Onyx's DocumentSet validates our Collection concept.** The aggregation pattern is sound. Two refinements they have we should adopt:

1. **Federated connectors** in the same set — they treat external search indices (e.g. a Vespa cluster, an internal Elastic) as collection members alongside ingested connectors. This lets a collection aggregate "stuff in our index" + "stuff in your existing index without re-ingesting". Useful for kairix when an operator already has a Notion search and wants to compose it without re-indexing Notion.
2. **`DocumentSet__UserGroup` mapping** — collections grant read to groups, not individuals. Our scope-profile model does this per-actor; per-group is a strict improvement when teams scale (no per-person profile maintenance).

---

## 7. Access control — per-document ACL sets, not per-actor scope profiles

ACL is enforced at vector-search time via **prefix-encoded ACL strings**:

```python
def _get_acl_for_user(user: User, db_session: Session) -> set[str]:
    if user.is_anonymous:
        return {PUBLIC_DOC_PAT}
    return {prefix_user_email(user.email), PUBLIC_DOC_PAT}
```

`PUBLIC_DOC_PAT = "PUBLIC"`. Each document's `DocumentAccess.to_acl()` produces a set like `{"user_email:alice@x.com", "group:eng", "PUBLIC"}`. Vespa/index filters require non-empty intersection between query user's ACL set and document's ACL set.

`DocumentAccess` (subclass of `ExternalAccess`) adds `user_emails: set[str | None]` and `user_groups: set[str]` for *internal Onyx* user/group membership — so the same set carries external (source-side) AND internal (Onyx-side) principals.

**This is structurally different from our per-actor scope-profile model.** Onyx is per-document ACL; we're per-actor scope-profile.

Trade-offs:
- **Per-document ACL** (Onyx): scales with doc count × principal count. Index storage grows. Filter is an O(1) set intersection at search time.
- **Per-actor scope-profile** (ours): scales with actor count × collection count. YAML config grows but flat. Filter is collection-name lookup at search time.

For an engagement-scope deployment (≤ 100 actors, ≤ 50 collections), per-actor is operator-tractable. For a tenant-scope deployment (10k+ actors, 1M+ docs each with their own ACL), per-document wins.

**Recommended hybrid**: keep our per-actor scope-profile as the primary policy layer (it's what operators reason about). For sources where `AccessType.SYNC` is on (the source enforces its own ACLs), additionally enforce per-doc ACL at search-time. The two layers compose: actor → reach collections (scope-profile) → within each collection, source ACLs further filter (per-doc).

---

## 8. Document model — typed Section union

`Document`:

```python
class DocumentBase(BaseModel):
    id: str | None
    sections: Sequence[TextSection | ImageSection | TabularSection]
    source: DocumentSource | None
    semantic_identifier: str          # UI title
    metadata: dict[str, str | list[str]]
    doc_updated_at: datetime | None
    primary_owners: list[BasicExpertInfo] | None
    secondary_owners: list[BasicExpertInfo] | None
    title: str | None
    external_access: ExternalAccess | None
    doc_metadata: dict[str, Any] | None
    parent_hierarchy_raw_node_id: str | None
    file_id: str | None
```

Documents are made of **typed `Section`s** (discriminated union): `TextSection`, `ImageSection`, `TabularSection`. Each section has a `link: str | None` — source URL at section granularity, not just document. Chunking is downstream (in `onyx.indexing.*`); connector emits `Document` objects, indexer produces chunks. **Onyx does NOT surface per-chunk sensitivity at the connector boundary** — sensitivity is per-document via `ExternalAccess`, propagates to all chunks of that document.

`content_hash()` provides MD5 fingerprint for dedup fallback.

**Adopt**: the typed `Section` union (TextSection / ImageSection / TabularSection) at our extractor boundary. Today our `RawArtefact` carries raw bytes + MIME; extractors emit plain text. Adding a typed section model lets the chunker handle each kind differently (a TabularSection chunks per-row; a TextSection chunks per-paragraph) — which is exactly what your PS asked about.

**Section-level link** is also worth adopting. Per-chunk `source_uri` already exists in our F39 surface but Onyx's per-section `link` is the right intermediate granularity for image-anchored or table-anchored retrieval.

**Reject**: per-document-only sensitivity. Our per-chunk F39 is the right shape; preserve it.

---

## 9. Failure-mode handling

Onyx separates failures by exception class in `connectors/exceptions.py`:

- `ConnectorValidationError` (base)
- `CredentialInvalidError`
- `CredentialExpiredError`
- `InsufficientPermissionsError`
- `UnexpectedValidationError` — transient, NOT used to disable connector

cc_pair has `status: ConnectorCredentialPairStatus` enum (`SCHEDULED | INITIAL_INDEXING | ACTIVE | PAUSED | DELETING | INVALID`) and `in_repeated_error_state: bool` flag — credential/source failures surface as state on the operational unit.

**Rate-limit handling is per-connector**, not framework-wide. Examples:
- Confluence wraps every method via `__getattr__` → `_make_rate_limited_confluence_method`, parses 401/403/429/500 distinctly, Redis-backed OAuth refresh at 50% TTL via `_renew_credentials`.
- Slack uses `OnyxRedisSlackRetryHandler` (Redis-coordinated so concurrent workers don't all hammer a throttled tier) + `ConnectionErrorRetryHandler`; both honour Slack's `Retry-After`.
- SharePoint does exponential backoff with jitter (5/10/20s capped at 30s, drawn from `[base/2, base]` to avoid thundering herd), honours Graph `Retry-After`.
- Google Drive leans on official client's built-in retry + `retry_builder(tries=3, delay=1)` for validation.

Per-document failures are first-class data: connectors yield `ConnectorFailure(failed_document=DocumentFailure(...), failure_message=...)` mid-stream rather than aborting; runner collects them, `Resolver.reindex()` replays.

**Recommended adoptions**:
- Typed exception hierarchy (`CredentialInvalidError` / `CredentialExpiredError` / `InsufficientPermissionsError` / etc.) at the framework boundary — gives the runner type-narrowed handling.
- Per-document failures as first-class mid-stream emissions (not exceptions that abort the batch). Pairs with our existing `dead_letter` table — failures route to dead-letter, runner continues.
- **Redis-coordinated rate-limit handlers** for multi-worker deployments. The "concurrent workers hammering a throttled tier" problem is real; centralising the rate-limit bucket in Redis prevents thundering herd. Reject for single-worker dogfood; design for multi-worker as a future deployment shape.

---

## 10. What Onyx does that our proposed model does NOT

1. **`HierarchyNode` as first-class connector emission.** Adopt with the 12-value `HierarchyNodeType` vocabulary. Unlocks "files in this folder" navigation without re-deriving from `source_uri` prefixes.
2. **`Resolver.reindex(errors)` protocol** for per-doc failure replay. Cheap and useful.
3. **`SlimConnector` for prune** — split ID-only enumeration from full retrieval; run on a different schedule.
4. **Dynamic credential rotation with Redis lock** — `OnyxDBCredentialsProvider` writes back rotated OAuth tokens under a tenant-scoped lock. Matters for any OAuth source with TTL < indexing-run-duration.
5. **`CheckpointedConnectorWithPermSync` as distinct capability** — ACL sync is its own scheduled job with its own cursor (`last_time_perm_sync`), not bolted onto content ingestion.
6. **`AccessType.SYNC / PRIVATE / PUBLIC` at cc_pair level** — per-instance access mode complements per-chunk F39 tier.
7. **`HierarchyNodeType` enum** — 12 normalised container shapes. Adopt as `RawArtefact.container_type` vocabulary.
8. **`Section` typed union (Text / Image / Tabular)** — mode-aware document handling without losing structure. Adopt for chunker dispatch (see §08-chunking-and-entity-strategies.md).
9. **`DocumentSet__UserGroup` mapping** — collections grant read to groups, not individuals. Adopt as per-team scope-profile (we already proposed teams as actor_kind; this is the access shape).
10. **Federated connector membership** in DocumentSet — external search indices as collection members alongside ingested data. Useful for "compose existing operator searches without re-indexing".

## 11. What our proposed model does that Onyx does NOT (preserve)

1. **F39 explicit sensitivity tier** (`public | internal | confidential | restricted`) as required at write. Strictly richer than Onyx's `is_public: bool` + ACL set.
2. **Fact-layer entity resolution** with cross-source identity. Genuinely new territory.
3. **Per-chunk source URI + sensitivity propagation.** Onyx's `Section.link` is the closest equivalent but isn't enforced at write time.
4. **Protocol-typed boundaries with `@dataclass(frozen=True)` returns (F42).** Onyx uses Pydantic + `dict[str, Any]`. Our frozen-dataclass discipline is a real type-safety upgrade.
5. **First-class extractor plugin separation (F38).** Onyx fuses extraction into the connector; we split connector (raw artefact) from extractor (artefact → chunks + entity signals) — makes "swap PDF extractor" mechanical.
6. **Mechanical plugin isolation (F26/F27/F34/F35).** Onyx connectors freely cross-import (`google_utils/` shared); our F35 forbids this. Correctness win for independently-shippable plugins.
7. **Two-scope split (F44).** Engagement vs firm scope is a hard architectural line; Onyx assumes single multi-tenant deployment with `tenant_id` threading through.

## 12. Concrete code-shape references

| Pattern | File | Class / function |
|---|---|---|
| Connector base ABC | `backend/onyx/connectors/interfaces.py` | `BaseConnector[CT]` |
| Capability mix-ins | `backend/onyx/connectors/interfaces.py` | `LoadConnector`, `PollConnector`, `SlimConnector`, `CheckpointedConnector[CT]`, `CheckpointedConnectorWithPermSync[CT]`, `EventConnector`, `Resolver`, `HierarchyConnector`, `OAuthConnector`, `CredentialsConnector` |
| Credential providers | `backend/onyx/connectors/credentials_provider.py` | `OnyxDBCredentialsProvider`, `OnyxStaticCredentialsProvider` |
| Registry | `backend/onyx/connectors/registry.py` | `ConnectorMapping`, `CONNECTOR_CLASS_MAP` |
| Factory | `backend/onyx/connectors/factory.py` | `identify_connector_class`, `instantiate_connector`, `validate_ccpair_for_user` |
| Runner | `backend/onyx/connectors/connector_runner.py` | `ConnectorRunner[CT]`, `CheckpointOutputWrapper[CT]` |
| Document / Section / SlimDoc / HierarchyNode | `backend/onyx/connectors/models.py` | `Document`, `DocumentBase`, `TextSection`/`ImageSection`/`TabularSection`, `SlimDocument`, `HierarchyNode`, `ConnectorCheckpoint`, `ConnectorFailure` |
| Exceptions | `backend/onyx/connectors/exceptions.py` | `ConnectorValidationError`, `CredentialInvalidError`, `CredentialExpiredError`, `InsufficientPermissionsError` |
| ACL model | `backend/onyx/access/models.py` | `ExternalAccess`, `DocExternalAccess`, `NodeExternalAccess`, `DocumentAccess` |
| ACL build (query-time) | `backend/onyx/access/access.py` | `_get_acl_for_user` |
| cc_pair schema | `backend/onyx/db/models.py` | `Connector`, `Credential`, `ConnectorCredentialPair`, `DocumentSet__ConnectorCredentialPair` |
| Enums | `backend/onyx/db/enums.py` | `AccessType`, `ConnectorCredentialPairStatus`, `IndexingMode`, `ProcessingMode`, `HierarchyNodeType` |
| Confluence (5-mix-in example) | `backend/onyx/connectors/confluence/connector.py` | `ConfluenceConnector`, `ConfluenceCheckpoint` |
| Confluence rate-limit + OAuth refresh | `backend/onyx/connectors/confluence/onyx_confluence.py` | `_make_rate_limited_confluence_method`, `_renew_credentials` |
| Slack per-channel cursor + Redis rate handler | `backend/onyx/connectors/slack/connector.py` | `SlackConnector`, `SlackCheckpoint`, `OnyxRedisSlackRetryHandler`, `ConnectionErrorRetryHandler` |
| SharePoint per-site-per-drive checkpoint | `backend/onyx/connectors/sharepoint/connector.py` | `SharepointConnector`, `SharepointConnectorCheckpoint`, `SiteDescriptor` |
| Google Drive multi-drive + OAuth | `backend/onyx/connectors/google_drive/connector.py` | `GoogleDriveConnector`, `GoogleDriveCheckpoint`, `_manage_oauth_retrieval`, `_manage_service_account_retrieval` |

---

## Net changes to fold into the ADR

These adoptions revise the proposed model in `ADR.md`:

| Adoption | Replaces / extends |
|---|---|
| cc_pair triad (Connector × Credential × CCPair) | `ConnectorInstance` (flat instance + credential_ref) |
| `HierarchyNode` first-class emission with `HierarchyNodeType` enum | (net new — we had nothing equivalent) |
| Capability mix-ins (Slim / Resolver / EventConnector etc.) | Flat `SourceConnector` Protocol becomes a set of optional capability Protocols |
| `Section` typed union (Text / Image / Tabular) at extractor boundary | Plain text body in `RawArtefact` |
| `AccessType.SYNC / PRIVATE / PUBLIC` per cc_pair | Complements scope-profile; not a replacement |
| Group-as-actor in scope profiles | Per-actor profiles only (extend to `actor_kind=team` already, this just adds group as a first-class identity layer) |
| `last_time_perm_sync` separate from `last_successful_index_time` | Single cursor per container |
| `Resolver.reindex(failures)` | Per-doc dead-letter exists; reindex capability is new |
| Typed exception hierarchy at connector boundary | Single generic `Exception` propagation |
| Redis-coordinated rate-limit handlers | Per-process rate budgets only |
| Federated connector membership in DocumentSet | (net new — useful for "compose external search index without re-ingest") |

The ADR's 5-layer model becomes 6 explicit concerns:
1. **Connector** (kind + config)
2. **Credential** (auth shape)
3. **ConnectorCredentialPair** (binding + status + cursor scope + access type)
4. **Container** (per-cc_pair internal scope unit; hierarchy node + cursor)
5. **Collection** (`DocumentSet`-style — aggregate cc_pairs + filters + federated members + group access)
6. **Scope profile + skill** (per-actor / per-group + per-skill strategy)

This is a real shift; I'll fold it into ADR.md once `08-chunking-and-entity-strategies.md` lands (the chunking dimension may further inform the capability surface — e.g. a dedicated `CodeAwareChunker` extractor capability).
