# BDD scenarios — pinning the topology behaviour

Gherkin scenarios that pin the layered topology's expected behaviour
across (a) each use case from `02-use-cases.md` and (b) each
connector kind from `01-source-analysis.md`. These are design-stage
scenarios — they define what the implementation must satisfy.

Once the ADR commits to a specific schema + interface set, these
land under `tests/bdd/features/` as `.feature` files paired with step
implementations (per F36 + F46).

## Convention

- `@placeholder` tag = scenario describes a connector not yet shipped;
  step impl is stub-only until plugin lands.
- `@topology` tag = scenario pins a topology-layer behaviour
  (collections, scope profiles, search strategies). All net-new.
- `@uc-<id>` tag = links to the use case in `02-use-cases.md`.

---

## Topology-level scenarios (the core layered behaviour)

### Feature: connector instances are credential boundaries

```gherkin
@topology
Feature: a connector instance owns one credential boundary and many internal containers
  As an operator deploying kairix against a tenant-credential source (SharePoint, Notion, M365, Slack, GitHub, Drive)
  I want ONE connector instance to enumerate every container reachable with the credential
  So that I don't need N copies of the same credential block

  Scenario: one SharePoint connector enumerates all sites the credential can read
    Given the operator has configured a connector instance named "sharepoint-corp"
      with kind "sharepoint"
      and credential_ref "kv://kairix/sharepoint-app-tenant-credentials"
      and the credential has Sites.Read.All app-only consent
    When the worker runs the connector sync for "sharepoint-corp"
    Then the connector enumerates every site the credential can read
    And each site's drives produce their own delta cursor scoped to (connector_name, container_id)
    And the chunks land in the configured collection(s) per site

  Scenario: per-vault credential keeps multiple Obsidian instances separate
    Given the operator has configured a connector instance named "obsidian-personal"
      with kind "obsidian"
      and vault_root "/data/vaults/personal"
    And the operator has configured a connector instance named "obsidian-archive"
      with kind "obsidian"
      and vault_root "/data/vaults/archive"
    When the worker runs both connector instances
    Then they share NO state (cursor / deadletter / collection)
    And each instance's chunks land in its own configured collection
```

### Feature: collections are decoupled from connector instances

```gherkin
@topology
Feature: collections are retrieval buckets, not connector outputs
  As an operator
  I want to define collections independently of connectors
  So that one collection can aggregate multiple sources AND one source can feed multiple collections

  Scenario: aggregation — multiple sources contribute to one collection
    Given a collection "client-x-engagement" is defined with sources:
      | connector_instance   | source_path_filter           |
      | obsidian-personal    | 01-Projects/Client-X/**      |
      | sharepoint-corp      | site:client-x/**             |
      | dex-crm-personal     | orgs/client-x                |
    When the worker runs sync across each source
    Then every chunk routed by the listed filters lands in the "client-x-engagement" collection
    And chunks from other paths in the same connectors land in their own configured collections

  Scenario: decomposition — one connector feeds multiple collections
    Given a connector instance "sharepoint-corp" is configured
    And collection "vault-legal" is defined with sources:
      | connector_instance | source_path_filter           |
      | sharepoint-corp    | site:legal/**                |
    And collection "vault-engineering" is defined with sources:
      | connector_instance | source_path_filter           |
      | sharepoint-corp    | site:engineering/**          |
    When the worker runs sync for "sharepoint-corp"
    Then chunks from site:legal land in "vault-legal"
    And chunks from site:engineering land in "vault-engineering"
    And neither collection contains chunks from the other site

  Scenario: collection without any source mapping is empty (not an error)
    Given a collection "team-shape-builder/lessons-learned" is defined with no sources
    Then the collection exists in the retrieval layer
    And searches against it return zero results without raising
    And operators can populate it later via direct CLI / MCP writes (internal-store connector)
```

### Feature: scope profiles enforce per-actor collection access

```gherkin
@topology
Feature: a scope profile is the resolution layer between actor and collection set
  As a retrieval caller (CLI / MCP / skill)
  I want the actor's scope profile to determine which collections participate
  So that access control is enforced at search time, not filtered post-hoc

  Scenario: agent search honours its scope profile's read-permitted set
    Given agent "agent-shape" has scope profile:
      | collection                       | read | write |
      | agent-shape/private-memory       | yes  | yes   |
      | team-shape-builder/decisions     | yes  | yes   |
      | team-shape-builder/lessons       | yes  | no    |
      | reference-library                | yes  | no    |
      | team-legal/contracts             | no   | no    |
    When agent-shape searches for "vendor contract terms"
    Then the search runs against the four collections marked read=yes
    And no result is drawn from team-legal/contracts even though it contains matching chunks

  Scenario: per-chunk sensitivity filter applies within a read-permitted collection
    Given agent "agent-shape" has scope profile:
      | collection         | read | max_sensitivity |
      | sharepoint-corp    | yes  | internal        |
    And the sharepoint-corp collection contains chunks at "public", "internal", and "confidential" tiers
    When agent-shape searches for any term
    Then results include "public" and "internal" chunks
    And results exclude "confidential" chunks
    And no metadata leak surfaces the existence of confidential chunks
```

### Feature: skills compose collections into a task-scoped search strategy

```gherkin
@topology @uc-knw-2 @uc-cmp-1
Feature: a skill defines an ordered collection set and a ranking strategy
  As an agent invoking a named skill
  I want the skill to specify which collections to search and how to rank
  So that the calling agent doesn't reinvent the strategy per task

  Scenario: prepare-sow skill resolves to its task collection set
    Given a skill "prepare-sow" is defined with task_collections:
      | name                          | sources                                                   |
      | client-x-engagement           | obsidian-personal:01-Projects/Client-X/**, sharepoint:client-x, dex_crm:orgs/client-x |
      | reference-superannuation-au   | reference-library/industry/super, sharepoint:research/super-au |
      | ai-operating-model-pattern    | reference-library/ai-tom, obsidian-personal:03-Resources/AI-TOM |
      | team-engagement-lessons       | team-engagement/lessons-learned                           |
    And the skill's ranking is "fuse_then_rerank_by_skill_priors"
    When an agent invokes the prepare-sow skill with task "SoW for Client-X AI TOM engagement"
    Then the resolver queries each task_collection separately for top-K
    And the result envelope shows per-collection contribution
    And the final ranking applies the skill's priors (authority for reference-library, recency for lessons)

  Scenario: skill collection set is filtered by actor's scope profile before search
    Given the prepare-sow skill includes "team-engagement-lessons"
    And the calling agent "agent-builder" has scope profile that excludes "team-engagement-lessons"
    When agent-builder invokes prepare-sow
    Then the search runs over the other three task_collections only
    And the result envelope notes the excluded collection (so the agent can decide whether to escalate)
```

### Feature: graph-anchored retrieval composes entity → chunks → scope filter

```gherkin
@topology @uc-knw-3 @uc-cmp-3 @uc-grp-2
Feature: an entity-anchored query traverses the graph then filters chunks by scope
  As an agent or human searching by entity
  I want the graph to be the canonical identity layer
  And chunk-level access control to filter what I actually see

  Scenario: "brief me on Client-X" pulls chunks from every source where entity resolves
    Given the entity "Client-X" exists in Neo4j with back-references to chunks in:
      | collection                  | chunk_count |
      | sharepoint-corp:client-x    | 142         |
      | obsidian-personal:client-x  | 38          |
      | dex_crm:orgs/client-x       | 6           |
      | m365_email:client-x-threads | 89          |
    And the requesting actor has read access to all four collections
    When the agent searches anchored by entity "Client-X"
    Then the result candidate set is the union of back-referenced chunks (275 total)
    And the hybrid ranker ranks within those 275 (not across the entire index)
    And the result envelope shows per-collection contribution counts

  Scenario: entity-anchored query honours scope profile per chunk
    Given the same Client-X setup
    But the actor's scope profile excludes "m365_email:client-x-threads"
    When the agent searches anchored by entity "Client-X"
    Then the candidate set excludes the 89 m365_email chunks (down to 186)
    And the entity itself still appears in the result (it's the same identity)
    But chunks from the excluded collection do not surface
```

---

## Per-connector scenarios (one feature per kind)

### Feature: obsidian — local vault watched + reconciled

Already shipped at `tests/bdd/features/connector_obsidian.feature`.
Topology-relevant additions:

```gherkin
@topology @connector-obsidian
Scenario: one obsidian instance supports per-folder collection routing
  Given a connector instance "obsidian-personal" is configured with vault_root "/data/vaults/personal"
  And a collection-mapping routes:
    | source_path_filter      | collection           |
    | 01-Projects/**          | vault-projects       |
    | 02-Areas/**             | vault-areas          |
    | 03-Resources/**         | vault-resources      |
    | 04-Agent-Knowledge/**   | vault-agent-knowledge|
  When the connector runs sync
  Then each chunk lands in the collection whose filter its item_id matches
  And no chunks land in the connector's default collection (which is bypassed by routing)

@topology @connector-obsidian
Scenario: per-file sensitivity from frontmatter overrides connector default
  Given a connector instance "obsidian-personal" has default sensitivity "internal"
  And a vault file "01-Projects/sensitive/contract-draft.md" has frontmatter "kairix_sensitivity: confidential"
  When the connector runs sync over that file
  Then the resulting chunks carry sensitivity = "confidential" (not "internal")
  And F39 enforcement at chunk write succeeds
```

### Feature: sharepoint — per-drive cursors, per-item sensitivity labels

```gherkin
@placeholder @connector-sharepoint
Feature: sharepoint connector tracks per-drive delta tokens + per-item sensitivity

  Scenario: connector enumerates all drives the credential reaches and tracks per-drive cursors
    Given a connector instance "sharepoint-corp" is configured with app-only consent
    And the credential reaches 12 sites with 47 drives total
    When the worker runs the first sync
    Then 47 delta cursors are persisted, one per drive, keyed by (connector_name, container_id)
    And each subsequent sync per-drive resumes from its own cursor

  Scenario: Sites.Selected fence is respected
    Given a connector instance "sharepoint-corp-restricted" uses Sites.Selected
    And only 3 sites have been granted via /sites/{id}/permissions
    When the worker runs sync
    Then only those 3 sites' drives produce delta cursors
    And no enumeration is attempted against ungranted sites

  Scenario: Purview sensitivity label maps to F39 tier
    Given a SharePoint DriveItem has sensitivity label GUID "abc-public"
    And the operator config has label_map: { "abc-public": "public", "abc-internal": "internal", "abc-confidential": "confidential" }
    When the connector ingests that item
    Then the resulting chunks carry sensitivity = "public"
    And the source default tier is overridden by the per-item label

  Scenario: Sites.Selected grant revoked mid-soak
    Given a connector instance has been syncing site "site:x" via Sites.Selected
    When the site admin revokes the grant
    Then the next sync detects 403 / no-access on the next delta call
    And the cursor for (connector_name, site-x-drive-1) is marked inaccessible (not deleted)
    And operator surfaces a clear message: "fix: re-grant via /sites/{id}/permissions; next: re-run kairix worker run-once"
```

### Feature: notion — page-by-page integration access + archive vs delete

```gherkin
@placeholder @connector-notion
Feature: notion connector reads pages explicitly shared with the integration

  Scenario: integration sees only pages it has been Connected to
    Given a connector instance "notion-workspace-acme" is configured with an internal integration token
    And the integration is Connected to 4 pages in a 1000-page workspace
    When the connector runs sync
    Then the connector enumerates only the 4 connected pages + their subtrees
    And ungranted pages do not appear in chunks even if matching search terms

  Scenario: per-teamspace sensitivity policy applies
    Given operator config maps teamspace → tier:
      | teamspace      | tier         |
      | Engineering    | internal     |
      | Legal          | confidential |
      | Marketing      | internal     |
    When the connector ingests pages from each teamspace
    Then engineering and marketing chunks carry "internal"
    And legal chunks carry "confidential"

  Scenario: archived page is distinguished from deleted page
    Given a connector instance is synced
    And a page is archived (page.archived = true)
    When the next sync runs
    Then the connector emits a "modified" event with archived state captured
    And the chunks remain in the index (recoverable)
    And the document is tagged "archived: true" in metadata

  Scenario: hard-deleted page surfaces via reconcile sweep
    Given a connector instance has been syncing 4 pages
    And one page is hard-deleted (404 on retrieve)
    When the periodic full-search reconcile runs
    Then the connector emits a "deleted" event for that page
    And chunks are tombstoned in the index

  Scenario: rate-limit 429 with Retry-After is honoured
    Given the integration is at its 3 req/s budget
    When a sync issues another request
    Then the connector backs off per Retry-After header
    And does not exceed the rate budget across concurrent worker threads
```

### Feature: m365 email headers — per-mailbox delta, body never read

Already shipped at `tests/bdd/features/connector_m365_email_headers.feature`.
Topology-relevant additions:

```gherkin
@topology @connector-m365-email
Scenario: per-mailbox delta cursor stored separately from connector cursor
  Given a connector instance "m365-email-corp" is configured with app-only consent
  And 50 mailboxes are in scope
  When the worker runs the first sync
  Then 50 delta tokens are persisted, one per mailbox, keyed by (connector_name, container_id)

@topology @connector-m365-email
Scenario: connector NEVER reads message bodies even if a misconfigured plugin asks
  Given a connector instance "m365-email-corp" is configured
  When a downstream chunker requests body content
  Then the connector returns header-only RawArtefacts
  And F15 secret-leak surface stays narrow (no body text in logs / chunks / vectors)
  And ADR-004 is honoured at the connector boundary
```

### Feature: m365 calendar — series + occurrences

Already shipped at `tests/bdd/features/connector_m365_calendar.feature`. No additions beyond per-calendar cursor (mirrors m365 email).

### Feature: dex_crm — three record kinds, one tenant per credential

Already shipped at `tests/bdd/features/connector_dex_crm.feature`.
Topology-relevant additions:

```gherkin
@topology @connector-dex-crm
Scenario: multiple Dex instances for multi-tenant operators
  Given operator has two Dex tenants (their personal CRM + their team's shared CRM)
  When two connector instances are configured:
    | name              | kind     | credential_ref            |
    | dex-personal      | dex_crm  | kv://kairix/dex-key-1     |
    | dex-team-shared   | dex_crm  | kv://kairix/dex-key-2     |
  Then the two instances share NO state (cursor / deadletter / collection)
  And entity_signals from each instance route to distinct entity-graph back-references (provenance preserved)
```

### Feature: teams — chat + files reuse SharePoint plumbing

```gherkin
@placeholder @connector-teams
Feature: teams connector ingests chat + reuses SharePoint for channel files

  Scenario: chat messages and channel files are correlated by channel
    Given a connector instance "m365-teams-corp" is configured
    And a Teams channel "channel-y" has 1200 messages and an attached file
    When the worker runs sync
    Then the chat messages and the attached file land in collections per the operator's mapping
    And cross-source identity (channel-y) is preserved in entity_signals so a graph query for channel-y finds both

  Scenario: per-channel privacy → F39 tier
    Given a public team "team-research" has a private channel "channel-vendor-x"
    When the connector ingests messages from both
    Then public-team-public-channel messages carry "internal"
    And public-team-private-channel messages carry "confidential"
```

### Feature: slack — three channel-privacy tiers

```gherkin
@placeholder @connector-slack
Feature: slack connector honours channel privacy tier per F39

  Scenario: public, private, and DM channels map to internal/confidential/restricted
    Given a connector instance "slack-acme" is configured
    And the workspace has channels:
      | channel               | privacy        |
      | #general              | public         |
      | #team-shape-builder   | private        |
      | DM: alice ↔ bob       | dm             |
    When the connector ingests one new message in each
    Then chunks from #general carry "internal"
    And chunks from #team-shape-builder carry "confidential"
    And chunks from the DM carry "restricted"

  Scenario: message edit does not double-count
    Given a connector instance has synced message ts=1700000000.000100 with text "hello"
    When the message is edited (Events API emits message_changed subtype)
    Then the chunk is upserted with the new text (one chunk, not two)
    And the previous_message payload is ignored for indexing purposes

  Scenario: 90-day free-tier visibility limit
    Given the workspace is on the free tier
    And messages older than 90 days are no longer API-visible
    When the connector runs sync
    Then no error is raised; the absence is silent (per Slack API behaviour)
    And operator config carries a "free_tier_acknowledged: true" flag so kairix can warn at config-load time
```

### Feature: github — repo-visibility tier + force-push reconcile

```gherkin
@placeholder @connector-github
Feature: github connector tracks per-repo deltas + handles force-push reconcile

  Scenario: public/internal/private repos map to F39 tiers
    Given a connector instance "github-org-acme" is configured with org-wide App install
    And the org has repos:
      | repo                | visibility |
      | acme/docs           | public     |
      | acme/internal-docs  | internal   |
      | acme/secrets        | private    |
    When the connector ingests one new commit in each
    Then chunks from acme/docs carry "public"
    And chunks from acme/internal-docs carry "internal"
    And chunks from acme/secrets carry "confidential"

  Scenario: force-push triggers reconcile, not duplicate-write
    Given a connector instance has synced commits up to SHA "abc123"
    When the branch is force-pushed and SHA "abc123" no longer exists
    Then the next sync detects the missing SHA via the events API
    And reconciles the branch state (re-walks current HEAD tree)
    And tombstones any chunks that no longer have a backing commit

  Scenario: archived repo stays readable but webhook-quiet
    Given a connector instance has synced repo "acme/old-project"
    And the repo is archived
    When future syncs run
    Then no new events fire for the repo
    And the reconcile cadence (less frequent than active repos) detects any out-of-band changes
```

### Feature: google_drive — DWD vs user-OAuth scope

```gherkin
@placeholder @connector-google-drive
Feature: google drive connector handles per-user vs whole-domain auth

  Scenario: user-OAuth instance enumerates one user's Drive view
    Given a connector instance "gdrive-alice" is configured with user OAuth (refresh_token bound to alice@acme.com)
    When the worker runs sync
    Then the connector enumerates alice's My Drive, Shared Drives she's in, and Shared-with-me
    And no other user's Drives are touched

  Scenario: DWD instance enumerates whole Workspace
    Given a connector instance "gdrive-acme-domain" is configured with DWD service account
    And the SA has been granted domain-wide delegation by an admin
    When the worker runs sync
    Then the connector enumerates every user's My Drive in the acme.com domain
    And per-user cursors are persisted (one cursor per user)

  Scenario: sensitivity tier from sharing visibility
    Given a connector instance has access to files with:
      | sharing                | F39 tier     |
      | private                | confidential |
      | restricted (named ACL) | confidential |
      | domain-with-link       | internal     |
      | anyone-with-link       | public       |
      | public                 | public       |
    When the connector ingests one file at each tier
    Then chunks carry the mapped tier
    And the resulting tier is recorded in document metadata
```

---

## Use-case-level scenarios (one feature per UC)

### UC-MEM-1, UC-MEM-2, UC-MEM-3 — memory tier behaviour

```gherkin
@uc-mem-1 @topology
Feature: agent recalls own private memory

  Scenario: private memory collection is read-write to owner only
    Given agent "agent-shape" has scope profile granting rw on "agent-shape/private-memory"
    And the collection has 50 chunks
    When agent-shape searches the collection
    Then results return
    When agent-builder (different actor) searches "agent-shape/private-memory"
    Then results are empty (collection not in agent-builder's profile)
    And the result envelope reports "access denied" without leaking chunk count

@uc-mem-2 @topology
Feature: team member finds shared notes

  Scenario: team-collections are readable by team members
    Given team-shape-builder has 2 collections:
      | collection                        |
      | team-shape-builder/lessons        |
      | team-shape-builder/decisions      |
    And members include agent-shape, agent-builder, human-dan
    When any team member queries "vendor consolidation lessons"
    Then both collections are searched
    And results dedupe by chunk-hash
```

### UC-CMP-1 — prepare-sow skill

(See the topology-level scenario "prepare-sow skill resolves to its
task collection set" above — that's the canonical pin for this UC.)

```gherkin
@uc-cmp-1 @topology
Feature: prepare-sow skill resilience

  Scenario: one task_collection missing access does not abort the whole search
    Given the prepare-sow skill includes "team-engagement-lessons"
    And the calling agent's scope profile excludes that collection
    When prepare-sow is invoked
    Then the search runs over the other three task_collections
    And the agent receives a structured result envelope with:
      | included_collections | 3                         |
      | excluded_collections | [team-engagement-lessons] |
      | total_results        | top-K of fused 3-coll set |

  Scenario: one connector being down does not abort the whole search
    Given the prepare-sow skill's "client-x-engagement" task_collection draws on sharepoint-corp + obsidian-personal + dex-crm
    And dex-crm credential is currently invalid (auth failure on last sync)
    When prepare-sow is invoked
    Then the search runs over the chunks already-indexed from each source
    And freshness is reported per-source in the envelope (so the agent can flag "Client-X CRM data is N hours stale")
```

### UC-ACS-1, UC-ACS-2, UC-ACS-3 — access control

```gherkin
@uc-acs-1 @topology
Feature: collection-level read enforcement is pre-search not post-hoc

  Scenario: search latency does NOT depend on excluded collections
    Given an actor's profile excludes "team-legal/*"
    And team-legal/* contains 500k chunks
    When the actor searches
    Then the search wall-time is identical (within noise) whether team-legal/* exists or not
    And no entries from team-legal/* are loaded into the ranking pipeline

@uc-acs-2 @topology
Feature: per-chunk sensitivity filter applies inside collection scope

  Scenario: actor sees public + internal but not confidential
    Given collection "vault-research" contains chunks tagged public, internal, confidential, restricted
    And actor's scope profile sets max_sensitivity="internal" on this collection
    When the actor searches
    Then results include public + internal chunks
    And results exclude confidential + restricted chunks
    And the result envelope does NOT leak the count of excluded chunks (no side channel)

@uc-acs-3 @topology
Feature: multi-principal aggregated query intersects scopes

  Scenario: composed query (on-behalf-of multiple principals) returns least-permissive intersection
    Given two principals X (sees A, B) and Y (sees B, C)
    When a composed query runs on behalf of [X, Y]
    Then the search scopes to collection B only (the intersection)
    Unless the caller passes "scope_composition: union" with appropriate authorisation tag
```

---

## Coverage matrix

| UC / Connector | Happy path | Permission boundary | Sensitivity boundary | Cross-source | Graph anchor |
|---|---|---|---|---|---|
| UC-MEM-1 | ✓ | ✓ | — | — | — |
| UC-MEM-2 | ✓ | ✓ | — | — | — |
| UC-MEM-3 | ✓ | ✓ | — | ✓ | — |
| UC-KNW-1 | ✓ | ✓ | ✓ | ✓ | — |
| UC-KNW-2 | ✓ | ✓ | — | ✓ | — |
| UC-KNW-3 | ✓ | ✓ | ✓ | ✓ | ✓ |
| UC-CMP-1 | ✓ | ✓ | — | ✓ | partial |
| UC-CMP-2 | ✓ | ✓ | — | ✓ | — |
| UC-CMP-3 | ✓ | — | — | ✓ | ✓ |
| UC-GRP-1/2/3 | ✓ | ✓ | — | ✓ | ✓ |
| UC-ACS-1/2/3 | ✓ | ✓ | ✓ | — | — |
| obsidian | shipped | ✓ | ✓ topology-add | — | — |
| dex_crm | shipped | — | — | — | ✓ |
| m365 email/cal | shipped | — | — | — | ✓ |
| sharepoint | @placeholder | ✓ | ✓ | ✓ | partial |
| notion | @placeholder | ✓ | ✓ | — | — |
| teams | @placeholder | — | ✓ | ✓ | — |
| slack | @placeholder | — | ✓ | — | — |
| github | @placeholder | — | ✓ | — | — |
| google_drive | @placeholder | — | ✓ | — | — |

Gaps highlight where the §04 simulation should focus pressure-testing.
