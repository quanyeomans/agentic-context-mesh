# Use cases — what we actually retrieve and why

Companion to `01-source-analysis.md`. The source-side answers *what
can be ingested*; this answers *what gets retrieved and on whose
behalf*. The layered topology (connector / collection / scope profile
/ search strategy) only earns its complexity if it cleanly serves real
retrieval shapes.

## Actor taxonomy

| Actor kind | Identity | Scope characteristics |
|---|---|---|
| **Agent** (per-skill) | e.g. `agent-shape`, `agent-builder` | Stable identity. Has a default scope profile + may activate skill-specific overlays. |
| **Agent** (research / curator / brief / prep) | bounded function — runs and exits | Scope is task-driven, often broader than per-skill agents (cross-team / cross-source). |
| **Human team member** | mapped to one or more agents OR queried directly via CLI / MCP | Inherits a scope profile from team membership. |
| **Team** | a named group of agents + humans | Shared collections (lessons learned, decisions, contracts). |
| **Skill** | a capability bound to a search strategy | Specifies collection set + ranking weights + retrieval iteration shape. |

A retrieval request always carries `(actor, [skill], task)` — the
resolver maps these to an ordered collection set + ranking strategy.

## Memory tier model

Five tiers, deliberately mirroring the user's articulation in the
2026-05-23 conversation:

| Tier | Owner | Read-by-default | Write semantics |
|---|---|---|---|
| **Private memory** | One agent | Owner only | Owner writes (conversational state, scratchpad). |
| **Team memory** | A team | All team members + their agents | Anyone in team writes (shared notes, decisions, lessons learned). |
| **General knowledge** | Org / engagement | Everyone in engagement | Curated / restricted writes (reference library, vault, archived projects). |
| **Task-aligned** | A task / engagement | Task participants | Materialised view across other tiers; ephemeral or persistent. |
| **Skill-aligned** | A skill | Skill consumers | Defines WHICH of the above tiers participate + at what weight. Not a storage tier — a *strategy*. |

These tiers map to **collections** in the layered topology — each
collection is owned at one tier. A scope profile bundles collections
across tiers for one actor.

---

## Use cases

Each use case names: actor, goal, retrieval shape, topology layers
exercised, and missing-from-current-code gaps.

### UC-MEM-1 — Agent recalls own past conversations

**Actor**: `agent-shape` (or any per-skill agent).
**Goal**: Continue work where it left off — find its own notes, prior
search results, recent decisions.

**Retrieval shape**:
- Collection: `agent-shape/private-memory` (single collection)
- Scope: read-only for everyone except `agent-shape`; read-write for self
- Ranking: recency-weighted hybrid (BM25 + vector + RRF with date boost)
- Iteration: typically one query, top-K

**Topology layers**:
- Connector: an internal-store connector (no external source — kairix writes its own memory)
- Collection: `agent-shape/private-memory`
- Scope profile (for `agent-shape`): `[agent-shape/private-memory: rw]`

**Current code gap**: agent registry in `kairix.config.yaml:agents`
declares `collection` + `write_path` per agent, but there's no
expression of "this collection is private". Effective scoping happens
via search `--agent` flag; needs first-class `scope: private`
attribute on collection definition.

---

### UC-MEM-2 — Team member finds shared notes / lessons learned

**Actor**: any team member (human or agent).
**Goal**: Find what the team has already learned about a topic.

**Retrieval shape**:
- Collections: `team-shape-builder/lessons-learned`,
  `team-shape-builder/decisions`, `team-shape-builder/shared-notes`
- Scope: read by team members; write requires team membership
- Ranking: hybrid, no recency bias (lessons are durable)
- Iteration: one query, top-K (deduped across the three team collections)

**Topology layers**:
- Connector: same internal-store connector + (eventually)
  shared-source connectors (team SharePoint, team Notion teamspace)
- Collection: per-team collection set
- Scope profile (for each team member): `[team-X/*: r]` or `rw`

**Current code gap**: no concept of "team" as a first-class scope.
Operators currently set one collection per agent; a team-shared
collection has no enforcement layer.

---

### UC-MEM-3 — Agent picks up where another agent left off

**Actor**: `agent-builder` looking at `agent-shape`'s last task state.
**Goal**: Continue a handoff — see what shape decided, what was
in-flight, what's unresolved.

**Retrieval shape**:
- Collections: `team-shape-builder/handoffs` + `agent-shape/private-memory`
  if shape opted-in to share private memory
- Scope: handoff collection is team-readable by design; private memory
  read access requires explicit owner opt-in
- Ranking: most-recent first
- Iteration: chain of related search calls — "what was the last task?"
  → "what was shape's last note?" → "what files did shape touch in
  Notion / Obsidian for this task?"

**Topology layers**:
- Multi-collection: spans private + team tiers
- Cross-source: the handoff search MAY chain into the agent's
  workspace (Obsidian / Notion / SharePoint) — exercises connector
  aggregation in one collection
- Search strategy: iterative — search → expand → re-search

**Current code gap**: no iterative search strategy primitive.
Current MCP `search` returns one top-K; chaining is the calling
agent's responsibility. A `tool_iterative_search` primitive would
let the strategy live in kairix.

---

### UC-KNW-1 — Search across general knowledge

**Actor**: any agent, any task.
**Goal**: Find anything in the engagement's accumulated knowledge.

**Retrieval shape**:
- Collections: every collection in the engagement (vault-*, obsidian,
  reference-library, sharepoint sites, notion workspaces, …)
- Scope: filtered by actor's scope profile (some collections are
  team-restricted; the actor sees only what they're entitled to)
- Ranking: hybrid + intent classification + entity boost
- Iteration: one query, top-K

**Topology layers**:
- Aggregation: many collections compose into the "general" search
  surface
- Scope profile: filters collections by visibility per actor
- Search strategy: the default profile

**Current code gap**: the CLI / MCP `search` accepts `--scope`
flag with `shared / agent / shared+agent / all-agents / everything`
but the resolver behind it does not honour collection visibility per
requesting principal. Today it's an opt-in filter, not an enforcement
layer.

---

### UC-KNW-2 — Skill-driven retrieval

**Actor**: agent executing a specific skill (e.g. `prepare-sow`,
`triage-morning`, `entity-brief`).
**Goal**: Retrieve in the way the skill expects — specific
collections, specific ranking, specific iteration.

**Retrieval shape**:
- Collections: skill-specific ordered set (e.g.
  `prepare-sow.task_collections = [client-x, reference-superannuation-au,
  ai-tom-pattern]`)
- Scope: still filtered by actor's profile, but the skill's
  collection-set is the WORKING set for this task
- Ranking: skill-specific (e.g. `prepare-sow` weights
  reference-library higher than agent memory)
- Iteration: skill-specific (e.g. `triage-morning` does N sequential
  searches across different collections, weighted summary)

**Topology layers**:
- Skill definition: a first-class config artefact
- Search strategy: per-skill, declarative
- Aggregation: many collections + ranking strategy

**Current code gap**: skills don't exist as first-class kairix
config. Agent registry has a `name` + `collection` + `write_path` per
agent but no "skill" entity. The skill concept is currently embedded
in agent-side prompts.

---

### UC-KNW-3 — Cross-source query for one entity

**Actor**: any.
**Goal**: "Bring together everything we have on Client-X" — pulls
from every connector that has touched Client-X (SharePoint /
Notion / Obsidian / Dex CRM / M365 mail / M365 calendar).

**Retrieval shape**:
- Collections: the union of all collections that have chunks
  back-referenced from the Client-X entity in Neo4j
- Scope: per-actor profile-filtered; result set composed of only
  chunks the actor can read
- Ranking: entity-anchored — chunks that mention Client-X most
  centrally rank higher
- Iteration: entity-first then expand via graph relationships
  (Client-X's people, projects, contracts, meetings)

**Topology layers**:
- Graph (global): entity resolution makes "Client-X" the same
  identity across sources
- Collection: spans many; aggregation happens at the entity layer,
  not at the collection set
- Scope profile: filters which chunks-back-referenced-from-entity
  the actor sees
- Search strategy: graph-anchored hybrid (Neo4j traversal feeds
  candidate chunks; hybrid ranks within candidates)

**Current code gap**: graph-anchored retrieval is partial — entities
exist (`entity_signals` + Neo4j) but the search pipeline doesn't yet
take an entity as the anchor with collection-filtered chunks. Today
search is text-anchored ("client x") which conflates entity-name
matching with text-recall.

---

### UC-CMP-1 — "Prepare a SoW for an AI operating-model engagement"

The user's named example. Multi-source, skill-driven, task-aligned.

**Actor**: `agent-prep` (the prep skill) running on behalf of a human.
**Goal**: Synthesise a Scope-of-Work document drawing on:
- Client-X's history (Dex CRM contacts, prior projects, prior
  engagements in Obsidian/SharePoint/Notion)
- AI-TOM pattern library (reference-library/ai-tom + obsidian/03-Resources/AI-TOM)
- Industry reference for superannuation in Australia
  (reference-library/industry/super, SharePoint /research/super-au)
- Team-internal lessons-learned on similar engagements
  (`team-engagement/lessons-learned`)

**Retrieval shape**:
- The skill defines `task_collections` as a named, ordered set:
  ```yaml
  skill: prepare-sow
    task_collections:
      - { name: client-x-engagement, sources: [obsidian, sharepoint, dex_crm], filter: "entity:client-x" }
      - { name: reference-superannuation-au, sources: [reference-library/industry/super, sharepoint/research/super-au] }
      - { name: ai-operating-model-pattern, sources: [reference-library/ai-tom, obsidian/03-Resources/AI-TOM] }
      - { name: team-engagement-lessons, sources: [team-engagement/lessons-learned] }
    ranking: fuse_then_rerank_by_skill_priors
    iteration:
      - query each task_collection separately for top-K
      - dedupe by chunk-hash across results
      - rerank using skill-specific priors (e.g. recency for
        lessons-learned, authority for reference-library, recency +
        author-weight for client-history)
  ```

**Topology layers**: ALL of them.

**Current code gap**: every layer beyond the connector framework is
either missing or partial. This use case is the canonical test for
the design — if the layered topology cleanly expresses prepare-sow,
the design is right.

---

### UC-CMP-2 — "Triage my morning"

**Actor**: human, via a dedicated `triage-morning` skill.
**Goal**: Show me, in priority order: new mail (headers only),
today's calendar, anything new in Slack #team-shape-builder, recent
Notion edits in active engagements, any commits in our repos.

**Retrieval shape**:
- Sequential per-source query, NOT one fused result set:
  1. `m365_email_headers` collection, last-24h, by sender importance
  2. `m365_calendar` collection, today + tomorrow
  3. `slack/team-shape-builder` collection, last-12h
  4. `notion/active-engagements` collection, last-24h modified
  5. `github/our-repos` collection, last-24h commits + open PRs
- Ranking: per-source (each source has its own "what's new" semantics)
- Iteration: N independent searches, presented as a structured report

**Topology layers**: connector / collection / scope profile / search
strategy. No graph anchor (this is feed-style, not entity-centric).

**Current code gap**: the per-source "what's new" semantics differ
(time-windowed search, last-modified ranking, change-feed cursor
read). Today there's no `kairix feed` or per-source `since=`
primitive — the search pipeline isn't shaped for this.

---

### UC-CMP-3 — "Brief me on Client-X"

**Actor**: human or `agent-brief`.
**Goal**: Generate a structured briefing on Client-X drawing on every
source touched.

**Retrieval shape**: graph-anchored (UC-KNW-3) → composed into a
narrative. Equivalent to UC-KNW-3 but with downstream LLM synthesis;
retrieval shape itself is the same.

**Topology layers**: same as UC-KNW-3.

---

### UC-GRP-1 — Entity resolution across sources

**Actor**: any retrieval that touches a named entity.
**Goal**: "Acme Pty Ltd" mentioned in Obsidian / SharePoint / Dex
CRM is the SAME entity even if the strings vary.

**Retrieval shape**:
- Not a retrieval at all — a precondition for graph-anchored
  retrieval (UC-KNW-3, UC-CMP-3) to work.
- Curator (separate worker, ADR-018) consumes EntitySignal rows
  from `entity_signals` staging table, resolves identities, writes to
  Neo4j with provenance back-references to chunks.

**Topology layers**:
- Graph: global identity layer, no collection scoping
- Provenance: each Neo4j node carries back-references to the
  collection + chunk-id that established it; access control happens
  at the chunk layer, not the entity layer

**Current code gap**: entity resolution is partial. Dex CRM
provides clean Person/Org IDs; Obsidian / SharePoint / Notion derive
entity mentions via NER which has fuzzy precision. Cross-source
entity merge isn't yet bulletproof.

---

### UC-GRP-2 — Relationship traversal

**Actor**: `agent-research`, `agent-curator`.
**Goal**: "Who works at Client-X?" / "What projects has Client-X been
involved with?" — graph traversal then chunk-retrieval per node.

**Retrieval shape**:
- Graph traversal in Neo4j returns related entities (people, projects,
  contracts)
- For each related entity, fetch back-referenced chunks (filtered by
  actor's scope profile)
- Compose into a relationship report

**Topology layers**: graph + collection + scope profile.

**Current code gap**: graph traversal primitive exists (curator uses
it); the composed `traverse + back-ref-chunks + scope-filter` is not
yet a first-class search primitive.

---

### UC-GRP-3 — Skill-routed expert finding

**Actor**: human or agent looking for "who in our team has done AI
TOM work for super funds before?"

**Retrieval shape**:
- Query Person entities via Neo4j tagged with relevant skills /
  engagements
- Filter to team members (versus external contacts)
- For each candidate, surface evidence chunks (which projects, what
  artefacts)

**Topology layers**: graph + scope profile (team membership) + chunk-back-references.

**Current code gap**: skill / experience tagging on Person entities is
not yet modelled. Curator extracts entities but not "this person has
skill X". The fact layer (`docs/architecture/fact-layer.md`) is the
right home — facts about people, with provenance.

---

### UC-ACS-1 — Agent honours per-collection read rights

**Actor**: any agent searching.
**Goal**: An agent in `team-shape-builder` should NOT see
`team-legal/*` collections even if they're in the engagement.

**Retrieval shape**: filter at the collection-resolution layer
BEFORE search runs. Don't filter results post-hoc — that wastes
ranking budget AND leaks side-channels.

**Topology layers**: scope profile MUST be the enforcement layer.

**Current code gap**: search today filters by collection name explicitly
(operator says `--collection X`). There's no resolver from
`(actor, skill, task) → allowed collection set`. This is the
biggest single gap to close.

---

### UC-ACS-2 — Agent honours per-item sensitivity tier

**Actor**: any agent.
**Goal**: An agent in `team-shape-builder` can see `internal` chunks
but not `confidential` chunks from a SharePoint site, even if the
agent's profile grants read on the site's collection.

**Retrieval shape**: per-chunk sensitivity filter applied after
collection scoping. Each chunk carries `sensitivity` (F39 invariant).
The actor's profile carries a `max_sensitivity` per collection.

**Topology layers**: scope profile + per-item sensitivity tier.

**Current code gap**: F39 enforces `sensitivity` is populated at chunk
write — but the search pipeline doesn't filter by sensitivity. Needs
to be wired through `ChunkRecord → ranking → result envelope`.

---

### UC-ACS-3 — Aggregated query returns subset based on actor's scope profile

**Actor**: any.
**Goal**: A search executed on behalf of multiple agents (e.g. a team
brief composed by agent-brief on behalf of team-shape-builder)
returns results filtered to the LEAST-PERMISSIVE scope unless the
caller explicitly authorises broader.

**Retrieval shape**: intersection of scope profiles across
authorised principals. Conservative by default.

**Topology layers**: scope profile composition (least-permissive
intersection) + per-item sensitivity filter.

**Current code gap**: scope-profile composition doesn't exist
because scope profiles don't exist.

---

## What the use cases collectively demand of the topology

Mapping use cases to layer requirements:

| Layer | Demanded by |
|---|---|
| Connector instance with `kind` + `name` distinct | UC-MEM-1 (internal-store), UC-CMP-1/2 (multi-source) |
| Source path filter inside one connector | UC-KNW-3, UC-CMP-1 (entity scope inside SharePoint sites) |
| Collection with read/write scope | every UC-MEM, every UC-ACS |
| Collection mapping `(connector, filter) → collection` | UC-CMP-1, UC-CMP-2 |
| Scope profile per-agent | every UC-MEM, every UC-KNW, every UC-ACS |
| Scope profile per-team | UC-MEM-2, UC-MEM-3, UC-ACS-1, UC-ACS-3 |
| Skill as first-class entity | UC-KNW-2, UC-CMP-1, UC-CMP-2 |
| Search strategy per-skill | UC-KNW-2, UC-CMP-1, UC-CMP-2 |
| Iterative search primitive | UC-MEM-3, UC-CMP-1 |
| Graph anchoring | UC-KNW-3, UC-CMP-3, UC-GRP-1/2/3 |
| Per-chunk sensitivity filtering | UC-ACS-2, UC-ACS-3 |
| Scope-profile composition (multi-principal intersection) | UC-ACS-3 |

Three of these (skill, iterative search, scope-profile composition)
don't exist in current code at all. The rest exist partially. The
ADR's job is to define the data shapes + interfaces that make them
all expressible without reinvention per use case.

---

## What the BDD scenarios (§03) will pin

For each use case above, the §03 BDD scenarios will pin:

- **Happy path** — actor + collections + scope → expected result shape
- **Permission boundary** — actor lacking a collection → result excludes that source
- **Sensitivity boundary** — actor permitted collection but not the tier → result excludes the chunk
- **Cross-source merge** — N collections → fused result, dedup, ranking
- **Graph anchor** — entity-first query → chunks back-ref'd from entity → scope-filtered

These get encoded as `tests/bdd/features/use_case_*.feature` per
use case. The §04 simulation will walk each scenario against the
proposed layered model and identify where the model breaks.
