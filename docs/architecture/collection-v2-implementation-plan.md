# Topology_v2 Collection Model — Implementation Plan + Test Coverage

**Status:** ✅ Implemented — spent plan (2026-06-02). The topology_v2 collection model shipped (#372/#373 CLOSED; `kairix/core/search/topology_v2_resolver.py`, `default_in_scope`). Retained as design provenance — the topology_v2 / collection-v2 test suite cites it. Design of record: `docs/architecture/collection-structure-design.md`. Not active work.
**Drives:** #373 (cutover), #372 (resolver — already on main, needs `default_in_scope` extension)
**Design doc:** `docs/architecture/collection-structure-design.md`
**Audience:** subagents implementing the changes; reviewer cherry-picking

## What we're building

Per the design doc (decided 2026-06-02 with operator):

- 8 logical collections: 7 in-default broad sources (sharepoint / obsidian / slack / email / calendar / github / own-memory) + 1 opt-in (reflib)
- Per-agent memory collections (6) — own-memory included in agent's default; other agents' memory unreachable
- New `default_in_scope: bool` field on `topology_scope_entries` controls inclusion in unspecified-default search
- Wildcard `applies_to: ["*"]` in scope_profile fans out to every registered agent
- Per-row sensitivity tier (not per-collection split) handles DM-vs-channel and public-vs-private repo distinctions

## Code surface to change

### Wave 1 — Schema + Resolver (~400 LOC + tests)

1. **`kairix/core/db/schema.py`** — add `default_in_scope INTEGER NOT NULL DEFAULT 1` to `topology_scope_entries` definition (additive migration; default 1 = back-compat).
2. **`kairix/core/connectors/scope_profile_resolver.py`** — add `default_only: bool = False` kwarg to `ScopeProfileResolver.resolve()`. When True, filter loaded entries by `default_in_scope=1`. Drops to existing behavior when False.
3. **`kairix/core/search/topology_v2_resolver.py`** — wire the `default_only` flag:
   - `collections=None` path → call ScopeProfileResolver with `default_only=True` → return only `default_in_scope` entries
   - `collections=[...]` explicit path → call with `default_only=False` (full scope), validate each provided name is in scope
4. **`kairix/core/db/schema.py`** migration runner — handle existing DBs by `ALTER TABLE … ADD COLUMN … DEFAULT 1`.

### Wave 2 — Config loader + wildcard (~250 LOC + tests)

5. **`kairix/core/search/config_loader.py`** (or equivalent — wherever scope_profiles parse from YAML) — accept the new `default_in_scope` field on each scope entry; default True if missing.
6. **Wildcard `applies_to` expansion** — when a scope_profile lists `applies_to: ["*"]`, expand to every agent name registered in the AgentRegistry / agents block at load time. Materialize as concrete rows in `topology_scope_profiles` + `topology_scope_entries` so the resolver doesn't need wildcard awareness.
7. **Validation**:
   - Every `collection_name` referenced in scope_entries must exist in the collections list (F21 error otherwise)
   - `default_in_scope` must be a bool (reject strings / ints / None with F21)
   - `applies_to` must be a non-empty list of agent names OR `["*"]`
   - At least one scope_profile must apply to every registered agent (F21 if any agent is unreachable from all profiles — common misconfiguration)

## Test coverage — every layer

### Unit tests (Wave 1)

`tests/unit/test_scope_profile_resolver_default_only.py` — new file:

| Test | What it pins |
|---|---|
| `test_default_only_false_returns_all_entries` | Back-compat: existing callers (default_only=False) see every entry regardless of default_in_scope |
| `test_default_only_true_filters_to_default_in_scope_true_entries` | When 3 of 5 entries have default_in_scope=1, only those 3 surface |
| `test_default_only_true_excludes_default_in_scope_false_entries` | Inverse: 2 of 5 with default_in_scope=0 are dropped |
| `test_default_only_true_with_no_default_in_scope_true_returns_empty` | Edge: every entry has default_in_scope=0 → empty result |
| `test_default_in_scope_default_value_is_one_for_back_compat` | Fresh-row constraint: `INSERT INTO topology_scope_entries(...)` without default_in_scope → row gets default_in_scope=1 |
| `test_max_sensitivity_cap_still_honored_with_default_only` | Sensitivity tier filter composes with default_only |
| `test_failure_injection_db_missing_default_in_scope_column_falls_back_gracefully` | F68: if the schema migration hasn't run, resolver still works (LEFT JOIN / COALESCE handles it) |
| `test_intersection_composition_with_default_only` | When multiple actors have overlapping scope, default_only intersects across actors |
| `test_union_composition_with_default_only` | scope_composition=union still filters by default_in_scope when passed |

`tests/unit/test_topology_v2_resolver_default_in_scope.py` — new file:

| Test | What it pins |
|---|---|
| `test_no_collections_specified_returns_default_in_scope_superset` | The headline behavior: collections=None → only default_in_scope entries fan out |
| `test_explicit_collection_in_scope_returns_that_collection` | collections=["reflib"] when reflib is in scope (any default_in_scope) → returns ["reflib"] |
| `test_explicit_collection_not_in_scope_returns_none_with_f21_error` | collections=["foo"] when foo isn't in agent's scope → None + F21-shaped error logged |
| `test_explicit_collection_opt_in_works_even_when_default_in_scope_false` | reflib has default_in_scope=False but is in scope → explicit naming retrieves it |
| `test_default_only_true_excludes_other_agents_memory` | agent="shape" + collections=None → does not include builder-memory |
| `test_explicit_other_agent_memory_returns_none` | agent="shape" + collections=["builder-memory"] → None + F21 (cross-agent isolation) |
| `test_agent_none_all_agents_path_unaffected_by_default_only` | agent=None, scope=ALL_AGENTS → still returns public cc_pair collections (no scope_profile lookup) |
| `test_factory_branch_on_topology_v2_collection_resolver_flag` | Feature flag OFF → DefaultCollectionResolver; ON → TopologyV2CollectionResolver |

### Contract tests (Wave 1) — Protocol-shape proofs

`tests/contracts/test_topology_v2_default_in_scope_contract.py`:

| Test | What it pins |
|---|---|
| `test_topology_v2_resolver_satisfies_collection_resolver_protocol_with_default_only` | isinstance check still passes after the API extension |
| `test_default_in_scope_default_search_returns_superset_load_bearing` | **The load-bearing test from #372 — extended for default_in_scope**: seeded scope_profile with 7 default + 1 opt-in → collections=None returns 7. Sabotage-prove. |
| `test_scope_profile_resolver_default_only_propagates_to_topology_v2_resolver` | The composition: TopologyV2CollectionResolver.resolve(collections=None) → ScopeProfileResolver.resolve(default_only=True) (one assertion mock the call) |
| `test_f68_db_row_missing_default_in_scope_treats_as_default_true` | Failure-injection: a row pre-dating the migration → resolver treats it as default_in_scope=True (back-compat) |

### Integration tests (Wave 2)

`tests/integration/test_collection_v2_search_default_superset.py` — F47: construct via `kairix.core.factory.build_search_pipeline`:

| Test | What it pins |
|---|---|
| `test_search_with_no_collections_returns_results_from_every_in_default_source` | End-to-end: 6 docs across 6 sources, all in-default, agent search with no collections → all 6 ranked |
| `test_search_with_no_collections_excludes_opt_in_collection` | reflib doc has 100% keyword match for query, but with default_only=True it's not returned |
| `test_search_with_explicit_opt_in_collection_returns_it` | Same setup, collections=["reflib"] → reflib doc returned |
| `test_search_returns_agent_own_memory_in_default` | agent=shape with default scope → shape-memory docs returned without explicit naming |
| `test_search_does_not_return_other_agent_memory_in_default` | agent=shape default search → no builder-memory docs even if keyword matches |
| `test_search_explicit_other_agent_memory_returns_empty_with_error_logged` | agent=shape + collections=["builder-memory"] → empty results + F21 error logged |
| `test_flag_off_uses_legacy_default_collection_resolver` | Feature flag OFF → SearchPipeline routes through DefaultCollectionResolver (back-compat) |
| `test_flag_on_uses_topology_v2_collection_resolver` | Feature flag ON → uses v2 resolver |

### BDD scenarios (Wave 2) — F45 + F54

`tests/bdd/features/collection_v2_default_in_scope.feature`:

```gherkin
Feature: topology_v2 collection model — default in-scope and opt-in retrieval

  Scenario: Agent's default search returns the broad superset
    Given the topology_v2_default_in_scope flag is ON
    And the operator has configured 7 in-default collections and 1 opt-in collection
    And agent "shape" has a scope_profile covering all 8 collections
    When agent "shape" issues a search with no collections specified
    Then the search returns hits from all 7 in-default collections
    And the search does not return hits from the opt-in collection

  Scenario: Agent can opt-in to a non-default collection explicitly
    Given the topology_v2_default_in_scope flag is ON
    And agent "shape" has reflib in scope with default_in_scope=false
    When agent "shape" issues a search with collections=["reflib"]
    Then the search returns hits from reflib only

  Scenario: Agent cannot retrieve another agent's memory
    Given the topology_v2_default_in_scope flag is ON
    And agent "shape" does not have builder-memory in scope
    When agent "shape" issues a search with collections=["builder-memory"]
    Then the search returns no results
    And the operator-facing error message contains "fix:" and "next:" markers

  Scenario: Feature flag OFF preserves legacy resolver behaviour
    Given the topology_v2_default_in_scope flag is OFF
    And the legacy collections.shared block declares 5 in-default collections
    When agent "shape" issues a search with no collections specified
    Then the search routes via DefaultCollectionResolver
    And returns hits from the 5 in-default legacy collections

  Scenario: Wildcard applies_to expands to every registered agent
    Given a scope_profile with applies_to=["*"]
    And 6 registered agents in the agents block
    When the config loader materialises the scope_profiles
    Then every agent has the wildcard profile's collections in their scope
```

### E2E composed path test (Wave 2) — F48

`tests/e2e/test_composed_collection_v2_path.py`:

| Test | What it pins |
|---|---|
| `test_composed_v2_path_search_returns_default_superset` | Full real-path: config YAML → factory.build → ingest 6 docs across 6 sources → query → assertion on superset. No mocks. Carries `@pytest.mark.e2e`. |

### Soak test (Wave 2)

`tests/soak/test_scope_resolver_at_scale.py`:

| Test | What it pins |
|---|---|
| `test_default_only_resolve_under_10k_scope_entries_p95_50ms` | 10,000 scope_entries seeded across 100 agents → 1000 `resolve(default_only=True)` calls → p95 latency ≤ 50ms |

### Config-loader unit tests (Wave 2)

`tests/unit/test_config_loader_collection_v2.py`:

| Test | What it pins |
|---|---|
| `test_default_in_scope_missing_defaults_to_true` | YAML entry without default_in_scope → row has default_in_scope=1 |
| `test_default_in_scope_explicit_false_persists` | YAML entry with default_in_scope: false → row has default_in_scope=0 |
| `test_default_in_scope_non_bool_raises_f21` | default_in_scope: "yes" / 1 / null → ValueError with fix:/next:/run: markers |
| `test_wildcard_applies_to_expands_to_all_registered_agents` | applies_to: ["*"] + 6 agents → 6 materialized profile rows |
| `test_wildcard_applies_to_with_zero_agents_raises_f21` | applies_to: ["*"] but agents block empty → loud config error |
| `test_collection_name_referenced_but_not_defined_raises_f21` | Scope entry references "foo" not in collections list → loud error naming the missing collection |
| `test_agent_unreachable_from_all_profiles_raises_f21` | One agent has no profile covering them → loud error (common misconfig) |
| `test_applies_to_list_supports_explicit_agent_names` | applies_to: ["shape","builder"] → 2 materialized rows |

## Test discipline (mandatory for both waves)

- **F1**: No `@patch`/`monkeypatch` on kairix internals. Inject `FakeScopeProfileResolver` from `tests/fakes.py` (extend if needed).
- **F2**: No `monkeypatch.setenv("KAIRIX_*")`. Pass deps as constructor kwargs.
- **F45**: New collection-v2 capability requires BDD feature + outcome test in same commit.
- **F46**: BDD steps compose via CLI / MCP / factory — never construct resolvers/pipelines directly inside step impls.
- **F47**: Integration tests construct via `kairix.core.factory.build_*`.
- **F48**: E2E composed-path test exists, carries `@pytest.mark.e2e`, exercises the real factory.
- **F54**: Every feature flag (`topology_v2_default_in_scope`) has BOTH OFF and ON BDD scenarios.
- **F68**: Failure-injection contract test for the new field's failure modes (missing column, NULL value, type mismatch).
- **F69**: Resolver tests with `.fetchall()` have a 10K-row scale variant in soak.
- **Sabotage-prove every new test** — mutate prod → confirm fail → restore → confirm pass. Verbatim failure messages in the agent's final report.

## Delegation breakdown

Two parallel worktrees:

### Worktree A — Schema + Resolver wiring (Wave 1)

- Schema migration (`kairix/core/db/schema.py`)
- ScopeProfileResolver `default_only` flag
- TopologyV2CollectionResolver wiring (collections=None → default_only=True)
- Feature flag `topology_v2_default_in_scope` declared
- Unit + contract tests above
- Sabotage proofs

### Worktree B — Config loader + BDD/E2E/Soak (Wave 2)

- Config loader extensions (default_in_scope parsing + validation + wildcard expansion)
- BDD scenarios in `tests/bdd/features/collection_v2_default_in_scope.feature`
- E2E composed-path test
- Integration tests for end-to-end search behaviour
- Soak test for resolver perf at 10K-scope scale
- Config-loader unit tests above
- Sabotage proofs

Both worktrees commit to their own branch. Orchestrator cherry-picks A first (foundation), then B. Tests in B depend on A's schema field being present.

### What both worktrees share

- `tests/fakes.py` — may need extensions in both. **Resolution at cherry-pick: union both sides.**
- `kairix.core.features.registry` — the flag declaration. **A owns it; B reads it.**
- `kairix/core/connectors/scope_profile_resolver.py` — A owns; B's integration tests consume its public API.

## Cutover after both waves land

(Orchestrator does this — not a subagent task.)

1. Cut a release picking up #371, #372, #377, #378, #380, github-allowlist, Wave 1+2 (~9 commits)
2. Pull new image on the production VM
3. Capture pre-cutover baseline via the now-fixed `scripts/cutover/capture_baseline.py`
4. Add topology_v2 collections + scope_profiles block to deployed `kairix.config.yaml`
5. Flip flags: `topology_v2_runtime: true`, `topology_v2_collection_resolver: true`, `topology_v2_default_in_scope: true`, per-connector `topology_v2_<obsidian|sharepoint|slack|m365_calendar|m365_email_headers|github>: true`
6. Restart kairix-1 + worker
7. Run post-flip baseline; diff against pre via `scripts/cutover/diff_baseline.py --strict`
8. Soak 24h, run eval daily; week-long soak for the eval-decision gate

## Acceptance for the implementation work

- All unit + contract + integration + BDD + E2E + soak tests pass (~50+ new tests across both waves)
- Coverage on new files ≥ 95%
- All sabotage proofs executed with verbatim failure messages in agent reports
- Schema migration runs idempotently on the production DB without data loss
- `kairix onboard check` introduces a new check `topology_v2_default_in_scope_field_present` that asserts the schema migration applied
- Feature flag declared with `target_retire_in: v2027.6.30`

## Effort estimate

- Worktree A: ~1.5 days of focused work
- Worktree B: ~1 day of focused work (parallelisable)
- Cherry-pick + verify on main: ~30 min
- Release cut + production cutover: ~1 day
- 1-week eval soak: 7 days waiting

**Total: ~3 days of build + 1 day of cutover + 1 week soak.**
