# Collection Structure Design — production rollout for topology_v2

**Status:** Implemented (shipped via #372 / #373; legacy reap completed via #374 — all CLOSED)
**Shipped:** TopologyV2CollectionResolver (`kairix/core/search/topology_v2_resolver.py`), the `default_in_scope` scope-entry field + schema migration (`kairix/core/connectors/scope_profile_resolver.py`, `tests/integration/test_topology_v2_applier_schema_migration.py`), wildcard `applies_to: ["*"]` fan-out (`kairix/core/connectors/topology_v2_applier.py`), and the config loader/validators (`kairix/config/topology_v2.py`, `kairix/config/topology_v2_validators.py`).
**Forward note:** the follow-up [`connector-architecture-refactor.md`](connector-architecture-refactor.md) collapses the subsystem to a single canonical model and renames `topology_v2` → `topology` (de-versioned: `topology_v2.py` → `topology.py`, `_v2` suffixes dropped, inert `topology_v2_*` config residue retired). Read this doc as the design-of-record for the structure that shipped; treat the `topology_v2` naming throughout as the historical name that the refactor supersedes.
**Audience:** the kairix operator, the dashboard wireframer, the engineering team

## Goal

Replace the ad-hoc 10-collection legacy state with a **deliberate, source-driven structure** + a **clear default-search behaviour** that:

- Surfaces every active source by default (sharepoint, obsidian, slack, email, calendar, github)
- Excludes specialised / private content (reference library, agent memory, archive) from the default — they're opt-in via explicit collection naming
- Gives each agent a sensible per-agent memory scope (write-isolated, read-private)
- Lets operators visualise + manage the structure from the (forthcoming) dashboard

## Starting state (pre-cutover legacy structure)

Before the cutover, a deployment carried roughly a dozen collections, mostly legacy-driven:

| Collection | Source | In default before cutover? |
|---|---|---|
| sharepoint | new (SP connector) | yes (implicit) |
| default | leak (#371, since fixed) | yes |
| obsidian | new (obsidian connector) | yes (implicit) |
| reference-library | reflib connector | **no** (in_default: false) |
| areas / projects / knowledge / resources | legacy path-based | yes |
| archive | legacy | **no** |
| agent-knowledge | legacy per-agent | **no** |
| home | legacy | yes |
| slack | new (Slack connector) | yes (implicit) |

The legacy path-based collections were reaped post-cutover (#374, CLOSED). The connector-driven collections that persist are sharepoint / obsidian / reflib / slack, plus email / calendar / github / per-agent-memory added under the v2 structure below.

## The v2 structure (shipped)

Two layers of collections + two layers of scope profiles. This is the structure that landed in production via #372 / #373.

### Layer 1 — Source collections (1 per connector)

```yaml
topology_v2:
  collections:
    # ── In default (every agent's broad search returns these) ─────────────
    - name: sharepoint
      sources:
        - cc_pair: sharepoint-agent-exchange-pair
      sensitivity_floor: internal
      description: "Team document library — meeting notes, decisions, partner materials"

    - name: obsidian
      sources:
        - cc_pair: obsidian-personal-pair
      sensitivity_floor: internal
      description: "Personal knowledge vault — work notes, project planning, dashboards"

    - name: slack
      sources:
        - cc_pair: slack-master-pair
      sensitivity_floor: personal  # DMs default to personal; channel content lifts to internal
      description: "Slack messages — DMs and channels visible to the master bot"

    - name: email
      sources:
        - cc_pair: m365-email-pair
      sensitivity_floor: personal
      description: "Email headers (from/to/subject/date — no body per ADR-004)"

    - name: calendar
      sources:
        - cc_pair: m365-calendar-pair
      sensitivity_floor: personal
      description: "Calendar events — meetings, reminders, busy/free"

    - name: github
      sources:
        - cc_pair: github-3-repos-pair
      sensitivity_floor: client-confidential
      description: "Code, issues, PRs from your configured repositories"

    # ── Opt-in (not in default — agent must name explicitly) ──────────────
    - name: reflib
      sources:
        - cc_pair: reference-library-pair
      sensitivity_floor: public
      description: "Reference library — specialised retrieval, opt-in"

    # ── Per-agent memory (one per agent — write-isolated) ─────────────────
    - name: shape-memory
      sources:
        - cc_pair: obsidian-personal-pair
          path_filter: "04-Agent-Knowledge/shape/**"
      sensitivity_floor: personal
      description: "shape's working memory + fact log"
    - name: builder-memory
      sources:
        - cc_pair: obsidian-personal-pair
          path_filter: "04-Agent-Knowledge/builder/**"
      sensitivity_floor: personal
    - name: consultant-memory
      sources:
        - cc_pair: obsidian-personal-pair
          path_filter: "04-Agent-Knowledge/consultant/**"
      sensitivity_floor: personal
    - name: growth-memory
      sources:
        - cc_pair: obsidian-personal-pair
          path_filter: "04-Agent-Knowledge/growth/**"
      sensitivity_floor: personal
    - name: coach-memory
      sources:
        - cc_pair: obsidian-personal-pair
          path_filter: "04-Agent-Knowledge/coach/**"
      sensitivity_floor: personal
    - name: family-memory
      sources:
        - cc_pair: obsidian-personal-pair
          path_filter: "04-Agent-Knowledge/family/**"
      sensitivity_floor: personal
```

### Layer 2 — Scope profiles (one per agent, plus a shared default)

```yaml
  scope_profiles:
    # ── Shared default — every agent gets this scope ──────────────────────
    - name: agent-default
      actor_kind: agent
      applies_to: ["*"]  # wildcard — every registered agent gets this profile
      entries:
        # In-default collections
        - {collection_name: sharepoint, mode: read, default_in_scope: true}
        - {collection_name: obsidian,   mode: read, default_in_scope: true}
        - {collection_name: slack,      mode: read, default_in_scope: true}
        - {collection_name: email,      mode: read, default_in_scope: true}
        - {collection_name: calendar,   mode: read, default_in_scope: true}
        - {collection_name: github,     mode: read, default_in_scope: true}
        # Opt-in (reachable when explicitly named, not in default search)
        - {collection_name: reflib,     mode: read, default_in_scope: false}

    # ── Per-agent memory profiles — owner gets read_write to own memory ───
    - name: shape-memory-profile
      actor_kind: agent
      applies_to: [shape]
      entries:
        - {collection_name: shape-memory, mode: read_write, default_in_scope: true}
    - name: builder-memory-profile
      actor_kind: agent
      applies_to: [builder]
      entries:
        - {collection_name: builder-memory, mode: read_write, default_in_scope: true}
    # ... ×6 for each agent
```

## How "default search" works in this model

A new field `default_in_scope: bool` on `topology_scope_entries` (default `true` for back-compat) controls whether the collection is included when an agent queries without specifying collections.

**The behaviour:**

| Caller invocation | What the resolver returns |
|---|---|
| `agent=shape, collections=None` | Union of `default_in_scope=true` collections from every profile attached to `shape`: `{sharepoint, obsidian, slack, email, calendar, github, shape-memory}` (7 collections) |
| `agent=shape, collections=["reflib"]` | `["reflib"]` if it's in `shape`'s scope (any mode), else F21 error |
| `agent=shape, collections=["builder-memory"]` | F21 error — `builder-memory` is not in shape's scope |
| `agent=shape, collections=["reflib","obsidian"]` | `["reflib","obsidian"]` — both in shape's scope |

This way:
- The default is broad enough to be useful (sharepoint + obsidian + email + calendar + slack + github + own memory)
- Specialised content (reflib) requires explicit naming
- Cross-agent memory is protected (shape can't see builder-memory)
- Operator can fine-tune by toggling `default_in_scope` per entry

## What landed in code

All of the following shipped as part of #372 / #373:

**1. Schema migration** — `default_in_scope INTEGER NOT NULL DEFAULT 1` was added to `topology_scope_entries`. Default 1 = back-compat (existing rows go to default). Migration coverage: `tests/integration/test_topology_v2_applier_schema_migration.py`.

**2. ScopeProfileResolver** (`kairix/core/connectors/scope_profile_resolver.py`) — when the caller passes `default_only=True`, entries are filtered by `default_in_scope=1`.

**3. TopologyV2CollectionResolver** (`kairix/core/search/topology_v2_resolver.py`) — calls ScopeProfileResolver with `default_only=True` when `collections=None`, and with `default_only=False` (full scope) when validating an explicit collection name.

**4. Wildcard `applies_to: ["*"]` support** — the scope_profile applier (`kairix/core/connectors/topology_v2_applier.py`) expands `"*"` into the full agent list at config load, mirroring the legacy "default" agent fan-out.

**5. Per-collection `path_filter`** — supported by the connector pipeline (used for slicing obsidian sub-paths). In production use for the per-agent memory carve-out.

**6. Config loader + validators** (`kairix/config/topology_v2.py`, `kairix/config/topology_v2_validators.py`) — parse the v2 shape, validate that every `collection_name` referenced in a scope_entry exists in the collections list, and validate `sensitivity_floor` literals.

**7. Dashboard surface** — still forward-looking. Once the dashboard from [`dashboard-spec.md`](dashboard-spec.md) lands, the Collections page renders this hierarchy with edit controls.

## Cutover protocol followed (#373, CLOSED)

The cutover executed the standard capture → flip → soak → diff → gate sequence:

1. Capture baseline via `scripts/cutover/capture_baseline.py` (state digest + eval scores + latency)
2. Land the schema + resolver changes on main behind the `topology_v2_default_in_scope` feature flag
3. Deploy via release tag
4. Add the topology_v2 collections + scope_profiles block to the deployed `kairix.config.yaml` (parsed, with legacy still authoritative)
5. Flip `topology_v2_runtime: true` + the per-connector `topology_v2_<obsidian|sharepoint|slack|m365_calendar|m365_email_headers|github>: true` flags
6. Soak with both schemas live (dual-write — legacy reads still work as fallback)
7. Diff post-cutover baseline against pre via `scripts/cutover/diff_baseline.py --strict` — gated on eval ±2pp, latency ±20%, state ±2%
8. Extended eval soak with the eval suite running daily
9. Promote: delete legacy `collections:` + `agents:` blocks (#374 reap, CLOSED)

The `topology_v2_*` flags this protocol flipped are now inert config residue, retired in the [`connector-architecture-refactor.md`](connector-architecture-refactor.md) de-versioning.

## Design decisions (resolved at cutover)

The six review questions raised in the draft were resolved as follows; the shipped structure above reflects these decisions.

1. **Wildcard `applies_to: ["*"]`** — adopted. The shared `agent-default` profile uses the wildcard and the applier fans it out to every registered agent at config load, avoiding per-agent repetition.

2. **Slack collection sensitivity** — kept as a single `slack` collection with `sensitivity_floor: personal` (DMs set the floor; channel content lifts at the document level). The `slack-channels` / `slack-dms` split was not taken; it remains a possible future refinement if a cleaner per-collection privacy story is needed.

3. **GitHub collection sensitivity** — kept as a single `github` collection with `sensitivity_floor: client-confidential` (private repos are the default). The `github-public` / `github-private` split was not taken, for the same reason as Slack.

4. **Per-agent memory in default** — kept `default_in_scope: true` for each agent's own memory, so an agent searching without specifying collections retrieves its own memory by default. Cross-agent memory stays protected by scope.

5. **Calendar sensitivity** — kept `sensitivity_floor: personal`. Calendar events are visible to the team but describe an individual's day, and `personal` is the safer floor.

6. **Operator-curated cross-source collections** — deferred to a future Layer 3 (operator-defined task/skill collections spanning multiple sources). Not part of this cutover; tracked under topology_v2 skill-driven retrieval (Wave D, see "What's NOT in scope" below).

## Migration UX (the operator's view)

After the cutover (the structure now in production):

- `kairix collections list` → 13 collections (7 in-default + 1 opt-in + 6 per-agent-memory) — vs the dozen legacy collections before
- Default search spans 7 sources — sharepoint + obsidian + slack + email + calendar + github + own-memory
- Reference library opt-in via `kairix prep --collection reflib`
- Per-agent memory write-isolated — `shape` can't write to `builder-memory` even if it tries

## What's NOT in scope for this doc

- The dashboard UI for managing collections (covered in `dashboard-spec.md` §4.2 Collections)
- The lifecycle / retention rules per collection (covered in #379)
- The eval-suite changes to score retrieval quality per collection (separate work)
- Topology_v2 skill-driven retrieval (Wave D — operator-defined task collections)

## Delivery record (original effort estimate)

The work was delivered across the phases below (estimate retained for historical context). The soak was the gating step.

| Phase | Work | Effort |
|---|---|---|
| 1 | Schema migration + ScopeProfileResolver `default_only` flag | half a day |
| 2 | TopologyV2CollectionResolver wiring + tests | half a day |
| 3 | Config loader + wildcard `applies_to` | 1 day |
| 4 | Production config edit + cutover protocol | half a day |
| 5 | 24h soak + diff | 1 day (mostly waiting) |
| 6 | Extended eval soak | ~1 week (waiting) |
| 7 | Legacy reap (#374) | half a day |
