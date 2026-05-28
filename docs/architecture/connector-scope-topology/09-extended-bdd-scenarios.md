# Extended BDD scenarios — actor perspectives + missing-BDD analysis

`03-bdd-scenarios.md` pinned topology + per-connector + per-UC behaviour. This doc extends it from **actor perspectives** — operator / agent / human team member / auditor / sysadmin / external user — and identifies the BDD gaps the v1 scenarios missed.

## Actor inventory

| Actor | Identity | Primary surfaces touched | Primary concerns |
|---|---|---|---|
| **Operator** | Human (deployer of kairix) | YAML config, `kairix cc-pair` CLI, `kairix features status` | "How do I configure / pause / rotate / debug?" |
| **Agent** | LLM agent (per skill or general) | MCP tools, `kairix search`, `kairix skill prepare-sow` | "Did I get the right context? Was anything excluded?" |
| **Human team member** | Person using kairix daily | CLI / web / Slack-bot / IDE plugin | "Find me X; show me what shape decided last week" |
| **Auditor** | Compliance / security reviewer | Audit log queries, `kairix audit sensitivity-coverage`, ACL audit | "Who can see what? When was X re-classified? Are sensitivities populated?" |
| **Sysadmin** | Operator's IT / DevOps | Container logs, systemd journal, `kairix worker status` | "Is the worker healthy? Are credentials about to expire? Is anything throttled?" |
| **External user** | Consumer of federated search | MCP tool calls into kairix from another platform | "Did kairix return results my federation expects?" |

`03-bdd-scenarios.md` is heavily agent-perspective. Operator / auditor / sysadmin / external-user scenarios are under-covered.

---

## Operator-perspective scenarios

### Feature: cc_pair lifecycle (create / pause / rotate-credential / delete)

```gherkin
@topology @operator @cc-pair-lifecycle
Feature: operator manages cc_pair lifecycle without losing operational state

  Scenario: create a new cc_pair binds an existing Connector to an existing Credential
    Given the operator has declared a Connector "sharepoint-corp" (kind: sharepoint)
    And the operator has registered a Credential "sharepoint-app-tenant" (kind: sharepoint)
    When the operator declares a cc_pair "kairix-team-sharepoint" binding "sharepoint-corp" to "sharepoint-app-tenant" with access_type=SYNC
    Then `kairix cc-pair list` shows the cc_pair with status=SCHEDULED
    And the next worker cycle moves it to INITIAL_INDEXING

  Scenario: pause a cc_pair stops new ingest but preserves cursor state
    Given a cc_pair "kairix-team-sharepoint" is ACTIVE with 47 containers and cursors at varying timestamps
    When the operator runs `kairix cc-pair pause kairix-team-sharepoint`
    Then the cc_pair status moves to PAUSED
    And no new list_changes calls fire for any of its containers
    And the container cursors remain at their last-set values
    When the operator runs `kairix cc-pair resume kairix-team-sharepoint`
    Then status moves back to ACTIVE
    And each container resumes from its preserved cursor without re-indexing the gap

  Scenario: rotate a Credential without disrupting cc_pair operational state
    Given a cc_pair "kairix-team-sharepoint" is ACTIVE with credential "sharepoint-app-tenant-v1"
    When the operator registers a new credential "sharepoint-app-tenant-v2" (kind: sharepoint, same tenant)
    And updates the cc_pair to bind to the new credential
    Then the cc_pair's cursors / status / last_indexed timestamps are preserved
    And the next sync uses the new credential
    And the old credential can be revoked from the secret store without affecting kairix state

  Scenario: delete a cc_pair tombstones its chunks without losing other cc_pairs' data
    Given two cc_pairs "kairix-team-sharepoint" and "obsidian-personal" both feeding "client-x-engagement" collection
    When the operator runs `kairix cc-pair delete kairix-team-sharepoint --confirm`
    Then status moves to DELETING
    And the worker tombstones all documents where cc_pair_id matches
    And the obsidian-personal documents in "client-x-engagement" remain unchanged
    And after tombstone completes, cc_pair row is removed
    And the next `kairix search` against "client-x-engagement" returns only obsidian-personal results
```

### Feature: collection definition + validation

```gherkin
@topology @operator @collection-validation
Feature: operator declares collections with validated source mappings

  Scenario: collection referencing a non-existent cc_pair fails at config-load
    Given the operator declares:
      | collection                | source                            |
      | client-x-engagement       | { cc_pair: sharepoint-typo, ... } |
    And no cc_pair named "sharepoint-typo" exists
    When the worker boots
    Then config-load fails with a typed validation error
    And the error names the offending collection + the missing cc_pair
    And the worker does NOT start in a degraded state

  Scenario: most-specific filter wins for items matching multiple collection mappings
    Given collection "vault-projects" maps `01-Projects/**`
    And collection "client-x-engagement" maps `01-Projects/Client-X/**`
    When the connector emits an item with item_id `01-Projects/Client-X/SoW-draft.md`
    Then the chunks land in "client-x-engagement" (more specific filter)
    And NOT in "vault-projects" (the less-specific match is bypassed)

  Scenario: item matching no collection mapping respects on_unmapped_item policy
    Given collection "vault-projects" maps `01-Projects/**`
    And the connector's `on_unmapped_item: drop` policy is set
    When the connector emits an item with item_id `99-Untracked/orphan.md`
    Then the chunk is silently dropped
    And the worker's `dropped_unmapped` counter increments
    And the operator sees this count via `kairix worker status`
```

### Feature: chunker registry diagnostics + version-bump re-chunk

```gherkin
@topology @operator @chunker-registry
Feature: operator can introspect chunker dispatch + force re-chunk on version bump

  Scenario: kairix worker chunker-registry shows the active dispatch table
    When the operator runs `kairix worker chunker-registry`
    Then the output lists every (kind, mime) → (Chunker class, version) entry
    And the default fallback chunker is identified
    And the output shows how many existing chunks were written by each registered version

  Scenario: chunker version bump triggers lazy re-chunk on next-touch
    Given a chunker "MarkdownStructuralChunker" was at version "1" and 5000 chunks carry chunker_version="1"
    When the operator deploys a new image with MarkdownStructuralChunker at version "2"
    Then the registry shows version "2" as active
    And existing chunks carry chunker_version="1" until their parent item is next modified
    And on next modify, the connector re-emits the item, Silver re-chunks with version "2", and old chunks tombstone

  Scenario: operator can force eager re-chunk for a kind
    Given chunker version bumped for "wiki-doc-store"
    When the operator runs `kairix worker rechunk --kind wiki-doc-store --confirm`
    Then every chunk where (chunker_version, kind) matches the old version is queued for re-chunk
    And the worker processes them in bounded concurrency without blocking other ingest
    And the operator can monitor via `kairix worker status` (re-chunk queue depth)
```

### Feature: federated connector adds external search-index as collection member

```gherkin
@topology @operator @federated
Feature: operator composes external search indices into kairix collections

  Scenario: collection includes federated MCP endpoint alongside ingested cc_pairs
    Given collection "client-x-engagement" includes:
      | source kind  | identity                                                          |
      | cc_pair      | kairix-team-sharepoint, filter: site:client-x/**                  |
      | cc_pair      | obsidian-personal, filter: 01-Projects/Client-X/**                |
      | federated    | { kind: external-mcp, endpoint: https://other-search.tld/mcp, ... } |
    When an actor searches "client-x-engagement"
    Then kairix queries the local cc_pair sources via its own hybrid pipeline
    And kairix queries the federated endpoint via the configured query_strategy
    And results are merged in the response envelope with per-source attribution
    And federated results carry a "federated: true" flag so the actor can decide whether to trust the unranked tier
```

---

## Agent-perspective scenarios

### Feature: agent receives ResultEnvelope with per-source freshness + excluded collections

```gherkin
@topology @agent @result-envelope
Feature: agent inspects ResultEnvelope to make informed retrieval decisions

  Scenario: envelope shows per-collection contribution count
    Given a skill includes 4 task_collections
    When the agent invokes the skill
    Then ResultEnvelope.included_collections has 4 entries
    And each entry has a result_count (some may be 0)
    And ResultEnvelope.excluded_collections is empty if all are reachable

  Scenario: envelope reports stale freshness per source
    Given cc_pair "kairix-team-sharepoint" last_successful_index_time = 2 hours ago
    And the cc_pair's freshness_strategy.poll.interval = 5m (so 2h is stale, > 24× the cadence)
    When the agent invokes any skill that includes "kairix-team-sharepoint" sources
    Then ResultEnvelope.freshness for that source is { state: stale, age_seconds: 7200 }
    And the agent can decide whether to proceed or request operator action

  Scenario: envelope reports excluded collection with escalation_hint
    Given the prepare-sow skill includes "team-engagement-lessons"
    And the calling agent's scope profile lacks read on that collection
    When the agent invokes prepare-sow
    Then ResultEnvelope.excluded_collections has one entry:
      | name                    | reason             | escalation_hint                                                              |
      | team-engagement-lessons | actor_lacks_read   | fix: request team-lead grant via `kairix scope grant ...`; next: re-invoke skill |
```

### Feature: agent honours sensitivity cap per scope-profile entry

```gherkin
@topology @agent @sensitivity-cap
Feature: per-collection max_sensitivity cap is enforced before ranking

  Scenario: chunks above cap are excluded from ranking
    Given collection "sharepoint-corp" contains 1000 chunks: 600 internal, 300 confidential, 100 restricted
    And actor "agent-shape" has scope entry { collection: sharepoint-corp, read: yes, max_sensitivity: internal }
    When agent-shape searches the collection
    Then the ranking input set is 600 chunks (the 600 internal)
    And no confidential or restricted chunks are loaded into the ranker
    And the result count reported in envelope is computed from the 600-chunk set

  Scenario: cap intersection across multi-actor composed query
    Given query is on-behalf-of [agent-shape, agent-builder]
    And agent-shape's cap on "sharepoint-corp" is max_sensitivity=internal
    And agent-builder's cap on "sharepoint-corp" is max_sensitivity=confidential
    When the composed query runs
    Then the effective cap is F39-min(internal, confidential) = internal
    And ranking input includes only internal+below chunks
```

### Feature: agent traverses HierarchyNode for "files in this folder"

```gherkin
@topology @agent @hierarchy
Feature: agent navigates the source's own folder tree via HierarchyNode

  Scenario: "show me other files in this folder" expands from a result chunk
    Given a result chunk has parent_hierarchy_raw_node_id = "drive:abc/folder:client-x-engagement"
    When the agent calls `kairix hierarchy siblings --node-id drive:abc/folder:client-x-engagement`
    Then the response lists every HierarchyNode whose parent_id matches
    And lists every document anchored at that node
    And filters by the actor's scope-profile (no access-leak via hierarchy)

  Scenario: "everything under site:X" navigates parent → children transitively
    When the agent calls `kairix hierarchy descendants --node-id site:client-x`
    Then the response includes every descendant HierarchyNode + document
    And the response is paginated (large sites)
    And scope-profile filtering applies at every level
```

### Feature: agent uses Resolver to retry per-doc failures cheaply

```gherkin
@topology @agent @resolver
Feature: agent can replay per-doc failures without rerunning a window

  Scenario: agent reads dead-letter and triggers targeted reindex
    Given a cc_pair has 12 documents in `connector_deadletter` with failure_kind="extract"
    When the agent calls `kairix cc-pair retry-failures kairix-team-sharepoint --kind extract`
    Then the worker invokes the Resolver capability on the cc_pair's connector with those 12 failures
    And only those 12 docs are re-pulled (not the whole window)
    And on success, the deadletter rows are removed
    And on repeated failure, the failure_message updates with the new error
```

---

## Human-team-member scenarios

### Feature: human queries via Slack-bot with team-scoped profile

```gherkin
@topology @human @team-scoped
Feature: human team member's queries are scoped by team membership

  Scenario: human's Slack identity maps to team scope profile
    Given a human "alice@example.com" is a member of "team-shape-builder" group
    And the team has a group_grant on "client-x-engagement"
    When the human queries via the Slack bot
    Then kairix resolves the team-shape-builder group's grants
    And the human's query sees the same collections an agent in that team sees

  Scenario: human can switch active skill explicitly
    Given the human is by-default scoped to team-shape-builder
    When the human runs `/kairix-skill prepare-sow` in Slack
    Then subsequent queries route through the prepare-sow skill's task_collection set
    And the result envelope distinguishes skill-driven results from default-profile results
```

---

## Auditor-perspective scenarios

### Feature: auditor verifies sensitivity-coverage across all chunks

```gherkin
@topology @auditor @sensitivity-coverage
Feature: auditor runs sensitivity-coverage audit + sees gaps

  Scenario: every chunk has a populated sensitivity
    When the auditor runs `kairix audit sensitivity-coverage`
    Then the response reports per-collection:
      | collection           | total_chunks | with_sensitivity | gap_count |
    And the F39 invariant is checked (gap_count must be 0 per F39)
    And any gaps are listed with chunk_id + cc_pair_id for remediation

  Scenario: per-cc_pair AccessType audit
    When the auditor runs `kairix audit access-types`
    Then the response lists every cc_pair with its access_type (PUBLIC | PRIVATE | SYNC)
    And cc_pairs with access_type=SYNC additionally report last_time_perm_sync + age
    And auditor can flag any SYNC cc_pair whose perm_sync is stale (> 2× perm_sync_freq)

  Scenario: sensitivity-mapping audit (per Purview / per channel-privacy etc.)
    When the auditor runs `kairix audit sensitivity-mapping --connector sharepoint-corp`
    Then the response lists every Purview label observed in the cc_pair's documents
    And maps each to its F39 tier via the connector's sensitivity_label_map
    And flags any label that doesn't have a mapping (would fall back to connector default)
```

### Feature: auditor traces a result back to its source provenance

```gherkin
@topology @auditor @provenance
Feature: every result chunk traces back to its source

  Scenario: chunk provenance includes cc_pair, container, hierarchy path, and chunker version
    When the auditor inspects a chunk via `kairix audit provenance --chunk-id <id>`
    Then the response shows:
      | field                          | value                                          |
      | cc_pair_id                     | kairix-team-sharepoint                         |
      | container_id                   | drive:abc                                      |
      | hierarchy_path                 | site:client-x > library > folder-x > doc.docx  |
      | item_id                        | drives/abc/items/xyz                           |
      | source_uri                     | https://contoso.sharepoint.com/.../doc.docx    |
      | extractor                      | python-docx, version 1                         |
      | chunker                        | MarkdownStructuralChunker, version 2           |
      | sensitivity_tier               | internal                                       |
      | sensitivity_source             | Purview label "abc-internal"                   |
      | first_indexed_at               | 2026-05-22T10:14:30Z                           |
      | last_re_chunked_at             | 2026-05-23T09:02:11Z                           |
```

---

## Sysadmin-perspective scenarios

### Feature: sysadmin diagnoses worker health

```gherkin
@topology @sysadmin @worker-health
Feature: sysadmin diagnoses connector health + credential lifecycle

  Scenario: cc_pair health summary
    When the sysadmin runs `kairix worker cc-pair-health`
    Then the response lists every cc_pair with:
      | cc_pair | status | last_index | last_perm_sync | in_repeated_error | containers_accessible | containers_revoked |

  Scenario: credentials near expiry surface for rotation
    Given a Credential's underlying OAuth refresh_token expires in 7 days
    When the sysadmin runs `kairix worker credentials-status`
    Then the response flags the credential with status=expiring + days_remaining=7
    And the sysadmin can run `kairix credential rotate <name>` to walk through rotation

  Scenario: rate-limited cc_pair surfaces with backoff info
    Given a cc_pair is currently in ContainerTransient state with retry_after=300 seconds
    When the sysadmin runs `kairix worker cc-pair-health`
    Then the cc_pair's status shows "throttled" + the cc_pair-level retry_after timer
    And subsequent syncs respect the backoff
```

---

## External-user-perspective scenarios

### Feature: external user federates into kairix via MCP

```gherkin
@topology @external @federated-mcp
Feature: kairix is itself federatable; external search platforms compose it as a member

  Scenario: external MCP tool calls kairix's federated search endpoint
    Given an external system has registered "kairix" as a federated member
    And the external system passes a query + actor identity (resolvable to a kairix scope profile)
    When the external system calls `mcp__kairix__federated_search` with { query, actor }
    Then kairix resolves the actor's scope profile
    And runs the search across the actor-permitted collections
    And returns a uniform federated-response shape (chunks + metadata + freshness)
    And does NOT leak excluded-collection details to the external system (those stay internal)
```

---

## Composition-rule edge cases (missing from v1)

```gherkin
@topology @composition @edge-cases
Feature: scope-profile composition handles edge cases without surprises

  Scenario: empty intersection — composition yields zero collections
    Given two principals X (sees A) and Y (sees B)
    When a composed query runs on behalf of [X, Y]
    Then the intersection is empty
    And the query returns an empty result set
    And the envelope explicitly notes "no overlap in scope profiles" so the caller knows why

  Scenario: F39 min-cap rule under conflicting tiers
    Given X has max_sensitivity=confidential on collection A
    And Y has max_sensitivity=restricted on collection A
    When composed query runs on [X, Y]
    Then the effective cap is F39-min(confidential, restricted) = confidential
    And restricted chunks are filtered out

  Scenario: union composition requires explicit authz
    Given a composed query passes scope_composition: "union"
    But no authz token is attached
    Then the query fails with InsufficientPermissionsError
    And the envelope contains a "fix: pass scope_composition_token=..." hint
```

---

## Failure-injection scenarios (missing from v1)

```gherkin
@topology @failure-injection
Feature: connector framework absorbs source-side failures without aborting other work

  Scenario: one cc_pair's auth failure does not stop other cc_pairs
    Given cc_pair A has a valid credential
    And cc_pair B has an expired credential
    When the worker's maintenance cycle runs
    Then cc_pair A syncs normally
    And cc_pair B raises CredentialExpiredError + moves to INVALID status
    And the operator sees the failure surface but cc_pair A is unaffected

  Scenario: one container's 429 does not block other containers in the same cc_pair
    Given cc_pair has 5 containers
    And container 3 returns 429 with retry_after=60s
    When the worker runs sync
    Then containers 1, 2, 4, 5 process normally
    And container 3 is marked TRANSIENT_ERROR with retry_after=60s
    And container 3 is retried after the backoff
    And no thread is blocked waiting on container 3

  Scenario: chunker raise on one document does not stop the batch
    Given an extractor returns a malformed Section for one document
    And the chunker registry's dispatch raises ValueError
    When ConnectorPipeline processes the batch
    Then that document goes to dead_letter with failure_kind="silver"
    And other documents continue processing
    And Resolver.reindex can retry later (perhaps with a different chunker version)
```

---

## Coverage delta vs `03-bdd-scenarios.md`

| New scenario family | Reason missing from v1 | Why it matters |
|---|---|---|
| cc_pair lifecycle | v1 had no cc_pair concept (flat ConnectorInstance) | Operator's primary daily surface |
| Chunker registry + version bump | v1 had no chunker registry | Source-kind chunking divergence (PS) |
| Federated members | v1 had no federation concept | Compose external search without re-ingest |
| HierarchyNode navigation | v1 had no hierarchy | "Files in this folder" queries |
| Resolver.reindex | v1 had no per-doc replay | Cheap failure recovery |
| Group-grants composition | v1 only had per-actor | Team scope at scale |
| Sensitivity-coverage audit | v1 didn't model auditor | F39 invariant verification |
| Provenance audit (per-chunk full trace) | v1 didn't model auditor | Compliance + debug |
| Credential rotation + expiry surfacing | v1 had static credentials | OAuth TTL < indexing-run |
| Rate-limit per-container backoff | v1 had per-connector cap only | Tenant-credential connectors |
| Empty-intersection / union-with-authz composition edge cases | v1 had only happy-path composition | Real multi-principal queries |
| External-user federation INTO kairix | v1 only had federation OUT | Kairix-as-MCP-tool composition |
| Chunker raise → dead_letter routing | v1 had connector-raise only | Per-kind chunker failure mode |

The above scenarios land as net-new `.feature` files in `tests/bdd/features/`:

- `cc_pair_lifecycle.feature`
- `collection_validation.feature`
- `chunker_registry.feature`
- `federated_collection.feature`
- `result_envelope_freshness.feature`
- `sensitivity_cap_enforcement.feature`
- `hierarchy_navigation.feature`
- `resolver_reindex.feature`
- `team_scoped_search.feature`
- `audit_sensitivity_coverage.feature`
- `audit_provenance_trace.feature`
- `cc_pair_health.feature`
- `credential_rotation.feature`
- `external_federation.feature`
- `composition_edge_cases.feature`
- `failure_injection.feature`

Plus extensions to existing per-connector features for AccessType, HierarchyNode emission, sensitivity-hint propagation, chunker-dispatch — touched as additional Scenario rows in `connector_<kind>.feature`.

---

## Total BDD scenario inventory (post-extension)

| Scope | v1 (03-bdd-scenarios.md) | v2 (this doc) | Total |
|---|---:|---:|---:|
| Topology-level | 5 features | 4 features | 9 |
| Per-connector | 9 features (4 shipped, 5 placeholder) | extensions to 9 | 9 |
| Per-use-case | 5 features | 0 (already covered) | 5 |
| Actor-perspective | 0 | 16 features | 16 |
| Failure-injection / edge-case | 1 feature | 2 features | 3 |
| **Total** | **20** | **22 net-new + extensions** | **42** |

Each feature has 1-5 scenarios, so the full BDD set lands ~150-200 scenarios. Coverage matrix in §10 ties each to its contract / integration / E2E test home.
