# ADR-024 — Test pyramid redesign: from coverage % to defect-class coverage

**Status:** Proposed 2026-05-28 — implementation deferred until F68–F72 land
**Issues:** N/A (cross-cutting; references the defect catalogue in this ADR)
**Related:** F7 (per-file coverage floor — being repositioned, not retired), F45 (BDD+outcome per new capability), F46/F47 (composition discipline), F62 (multi-tick idempotency), F67 (staging-drain symmetry, shipped this session)
**Supersedes:** the implicit "F7 is the quality gate" assumption baked into safe-commit.sh + CI Stage 2

## Context — the defects told us where the pyramid is wrong

In the 48 hours preceding this ADR, eight production-impact defects were identified, ranging from the worker restart-looping every 10 minutes (#335) to 2.3M entity signals never reaching Neo4j across 4 years (#334). Every one of them passed the existing test pyramid (8,000+ tests green on every commit).

The defects and what would have caught each:

| Defect | Production impact | What test layer should have caught it | What we actually had |
|---|---|---|---|
| Bug 1 — cursor stuck on full-resync | 8,783 items re-fetched every 15 min | Multi-tick connector integration test | Single-tick happy-path only |
| Bug 2 — SharePoint 429 dead-lettered every item | Whole drive dead | 429-response failure-injection contract test | 200-response happy-path only |
| Bug 3 — unbounded `_prune_orphans` | Disk IO saturation at 2.1M rows | Scale-bound integration test (~10⁵ rows) | N=10 fixture tests |
| #335 worker OOM at 1.27M vectors | Worker in 10-min restart loop | E2E at production corpus scale | E2E at ~100-vector fixtures |
| #334 Neo4j drain never built | 2.3M signals, 4 years, never flagged | Schema-writer-symmetry contract | F67 didn't exist until 2026-05-28 |
| #336 documents_media never written | Per-extractor analytics blank since Wave 1 | Same schema-writer contract | Same gap |
| Preflight masking 2.3M backlog as "1000" | Operator never saw true scale | Preflight-truthfulness assertion | Test asserted `count > 0` only |
| ~5,200 SP items in bronze-but-not-content limbo | Bronze had them; content + DLQ didn't | Bronze-coverage parity invariant | No invariant test crossed layers |

The common pattern: every defect was either (a) **scale-related**, (b) **failure-injection-related**, (c) **schema-writer symmetry**, or (d) **cross-layer integrity**. The existing pyramid (Unit / Contract / Integration / BDD / E2E) covers happy-path shape compliance and isolated unit logic, but none of those four classes systematically.

**F7 is the wrong gate to optimize.** A 90% line-coverage floor is a smell signal for "did anyone write tests" — not for "do those tests catch the failure modes that hit production". This ADR repositions F7 from goal to floor, and introduces five new F-rules + one new test tier that target the actual failure classes.

## Decision

Introduce five new fitness functions and one new test tier. Each named gap from the defect table above maps to one new mechanical rule with an agent-actionable affordance.

### F68 — Protocol failure-injection coverage

**Detection:** For every method on every Protocol declared in `kairix/core/protocols.py` (and any `*/protocols.py` under `kairix/`), the file `tests/contracts/test_<protocol_name>_failure_modes.py` must exist and contain at least one test of the form `test_<method>_<failure_class>_<observable_outcome>`. Failure classes from a fixed enum: `raises`, `times_out`, `returns_partial`, `returns_empty`, `unauthorized`, `unavailable`.

**Mechanism:** AST scan finds Protocol classes; module-level regex on the matching test file enforces the function-name pattern. Per-Protocol baseline file enumerates which combinations are required.

**Affordance template:**

```
F68: Protocol <Name>.<method> has no failure-injection coverage.

fix: add a test to tests/contracts/test_<protocol_lower>_failure_modes.py
following the shape:

    def test_<method>_raises_at_boundary_is_observable_by_caller():
        impl = Fake<Name>(raise_on=<method_name>)
        caller = build_thing_that_uses_<protocol_lower>(<protocol_lower>=impl)
        with pytest.raises(<ExpectedExceptionClass>):
            caller.do_the_thing(...)
        # OR: assert the caller observed the failure via a structured
        # result envelope (e.g. result.failed_count == 1)

next: re-run python3 scripts/checks/check_f68_protocol_failure_modes.py
run: bash scripts/safe-commit.sh "test(contracts): add <method> failure-mode contract"

Pass example: tests/contracts/test_source_connector_failure_modes.py
  def test_fetch_raises_propagates_to_dead_letter():
      connector = FakeSourceConnector(fail_on_fetch={"item1"})
      pipeline = build_connector_pipeline(...)
      pipeline.run_batch(connector, FakeExtractor())
      assert db.execute("SELECT COUNT(*) FROM connector_deadletter").fetchone()[0] == 1

Forbidden example: tests/contracts/test_source_connector_protocol.py
  def test_fetch_returns_raw_artefact():
      # This proves SHAPE compliance only. F68 requires BEHAVIOUR
      # under failure too.
      ...
```

**Baseline:** Generate at landing — every existing Protocol gets a baseline entry with its current methods grandfathered. Each new Protocol method requires a failure-mode test in the same commit. Existing Protocol methods can pay down the baseline over time.

**Catches:** Bug 2 (429 handling), failure-injection gaps generally.

### F69 — Scale-bound test requirement

**Detection:** For every test under `tests/integration/` whose body contains either a `for ... in connector.list_changes(...)` pattern OR a `.fetchall()` / `.execute("SELECT...").fetchall()` over a kairix table, at least one variant of the test must use a fixture size ≥ 10⁴ rows OR carry a `# F69-small-scale-only: <rationale>` comment.

**Mechanism:** AST scan over test bodies looking for the iteration patterns; cross-reference against a fixture-size helper (`tests.fakes.fixture_size_n(N)`) or an inline constant. Per-file baseline.

**Affordance template:**

```
F69: integration test <file>::<test_name> iterates over a kairix
table or connector stream at fixture-only scale.

fix: add a scale variant — parametrize on N and include one variant
at N >= 10000:

    @pytest.mark.parametrize("n_rows", [100, 10_000])
    def test_prune_orphans_bounded(db: sqlite3.Connection, n_rows: int):
        _seed_orphan_rows(db, n=n_rows)
        result = scheduler.tick()
        assert result.pruned <= 1000  # F66 budget cap holds at any scale

next: re-run python3 scripts/checks/check_f69_scale_bound_tests.py
run: bash scripts/safe-commit.sh "test(integration): add 10k-row variant to <test>"

When scale-only-small is the right answer (rare):
  # F69-small-scale-only: function is constant-time; scale doesn't change behaviour
  def test_constant_time_thing(): ...
```

**Baseline:** Existing tests grandfathered; new integration tests must include a scale variant or rationale.

**Catches:** Bug 3 (unbounded fetchall at production scale).

### F70 — Schema-writer symmetry (extended F67)

**Detection:** F67 today catches `pushed_to_<sink>=0` columns with no writer. Extend the rule to: every CREATE TABLE in `kairix/core/db/schema.py` must have either (a) at least one `INSERT INTO <table>` site in `kairix/` production code (excluding tests/ and migrations/), OR (b) a `# table-is-derived: <derivation>` comment on the CREATE TABLE line declaring the table as a view/cache/derived-state rationale.

**Mechanism:** AST scan over `schema.py` to find CREATE TABLE names; grep `INSERT INTO <name>` across `kairix/` for each.

**Affordance template:**

```
F70: table <name> declared in schema.py has no INSERT site in
production code.

fix: implement a writer for the table. Common shapes:

    # In silver / pipeline / use_case:
    db.execute(
        "INSERT INTO <name> (col1, col2) VALUES (?, ?)",
        (val1, val2),
    )

    # OR if the table is genuinely a derived view, add the
    # rationale comment to schema.py:
    CREATE TABLE IF NOT EXISTS <name> (
        ...
    )  # table-is-derived: rebuilt nightly from <other_table>

next: re-run python3 scripts/checks/check_f70_schema_writer_symmetry.py

Pass example: kairix/core/db/schema.py:81 (content_vectors) →
  kairix/core/embed/embed.py:_stage_batch_embeddings()

Forbidden example: declaring documents_media with no INSERT site
(this is the #336 anti-pattern).
```

**Baseline:** Empty at landing (the gate fires on any existing violations — paydown is the implementation work for #336 and any siblings).

**Catches:** #334 (Neo4j drain — already caught by F67), #336 (documents_media writer missing).

### F71 — Preflight-truthfulness

**Detection:** Every preflight check function in `kairix/core/db/integrity.py` that returns an `IntegrityGap` with a `count:` field must have a paired contract test in `tests/contracts/test_integrity_truthfulness.py` asserting `gap.count == db.execute("SELECT COUNT(*) FROM <table> WHERE <predicate>").fetchone()[0]` for the same `<table>` + `<predicate>` the preflight uses internally.

**Mechanism:** AST scan over `integrity.py` to find preflight functions; module-level regex on the matching contract test asserts the truthfulness pattern.

**Affordance template:**

```
F71: preflight check <function_name> reports a count without a
truthfulness contract test.

fix: add the paired test in tests/contracts/test_integrity_truthfulness.py:

    def test_<function_name>_count_equals_ground_truth(tmp_path):
        db = _open_db(tmp_path)
        _seed_n_rows(db, n=1500, predicate_match=True)
        gap = <function_name>(db)
        ground_truth = db.execute(
            "SELECT COUNT(*) FROM <table> WHERE <predicate>"
        ).fetchone()[0]
        assert gap.count == ground_truth, (
            f"preflight reported {gap.count}; SELECT COUNT(*) reports {ground_truth}"
        )

next: re-run python3 scripts/checks/check_f71_preflight_truthfulness.py
```

**Baseline:** Empty at landing — sweep existing preflight checks in the same PR.

**Catches:** The "preflight masked 2.3M as 1000" failure mode (#334).

### F72 — Cross-layer integrity invariants

**Detection:** A new test file `tests/integrity_invariants/` with one file per named invariant. Each invariant file:
- Declares a docstring describing the invariant in operator-language (e.g. "every bronze row maps to either content+1+ rows OR a dead-letter entry")
- Implements a fixture-based test that seeds state across multiple layers, runs the composed pipeline, then asserts the invariant
- Implements a production-scale soak variant (F69-compliant)

The check enumerates a registry of named invariants in `scripts/checks/_integrity_invariants_registry.py` and asserts each has a matching file + assertions.

**Mechanism:** Per-invariant registry; AST scan over the matching test files.

**Invariants to seed at landing:**

1. `bronze_coverage_parity`: `|bronze| == |content_distinct_hashes ∪ dead_letter_distinct_items ∪ in_flight|`
2. `content_vectors_alignment`: `|content_vectors.distinct(hash)| <= |content.distinct(hash)|` (every vector traces to a chunk)
3. `staging_drain_progress`: `pushed_to_<sink>=0` counts only grow when the sink is unreachable (preflight assertion)
4. `documents_media_extractor_completeness`: every successfully-extracted `content` row has a corresponding `documents_media` row with non-null `extractor_name`
5. `cc_pair_lifecycle_consistency`: every `topology_cc_pairs.status` transition is in `_ALLOWED_TRANSITIONS` (already F57 — extend to assert invariant under multi-tick)

**Affordance template:**

```
F72: integrity invariant '<name>' has no matching test file at
tests/integrity_invariants/test_<name>.py.

fix: create the file with the canonical shape:

    """Invariant: <one-line operator-language description>.

    Why: <link to the defect that motivated the invariant if any>
    """
    import pytest

    @pytest.mark.invariant
    def test_invariant_holds_at_fixture_scale(tmp_path):
        # seed cross-layer state, run pipeline, assert invariant
        ...

    @pytest.mark.invariant
    @pytest.mark.soak
    def test_invariant_holds_at_soak_scale(tmp_path):
        # same as above but N=10**4+
        ...
```

**Catches:** The "5,200 SP items in bronze-but-not-content limbo" failure mode — bronze_coverage_parity invariant fires before that state can ship.

### Soak tier (new) — `@pytest.mark.soak`

**Definition:** Tests carrying `@pytest.mark.soak` are excluded from CI Stage 2 (unit + bdd + contract) and CI Stage 3 (integration). They run via a new CI workflow `soak-suite.yml` triggered nightly on `main` AND on-demand via `gh workflow run soak-suite.yml`.

**Constraints:**

- Soak tests must use the F66 `per_tick_max_items` mechanism (no unbounded iteration even at scale)
- Soak tests run against a real (containerised) Neo4j when available; fall back to `FakeDrainGraphRepository` with a documented behavioural-equivalence assertion
- Soak fixtures live in `tests/fixtures/soak/` and use generators (not committed multi-MB files)
- Each soak test has a wall-clock budget asserted; regressions on budget fail the test

**Why CI doesn't run these per-commit:** Soak suite wall-clock is 20-60 min depending on scope. Running per-commit slows feedback to unacceptable levels. Nightly + on-demand is the right cadence; the per-commit gates (F68–F72) catch the *shape* problems that would otherwise only show under soak.

## Repositioning F7

F7's per-file coverage floor stays — but it's no longer treated as the primary quality gate. Specifically:

- F7's failure message updated to point at F68–F72: "coverage gap is a smell; the underlying problem is usually missing failure-injection or scale tests — see ADR-024 §F68/F69 before adding happy-path tests to game the floor"
- safe-commit.sh stops failing on F7 *deltas* below 0.5% — adds a warning instead. Lets refactors that legitimately move code around the file without changing behaviour ship cleanly.
- Net-new F7 violations (files dropping below floor for the first time) continue to block. Existing grandfathered files still need paydown — but only when the paydown is structural (extract testable logic into a use-case class per F7's own affordance text), not when it's coverage-padding tests against private functions.

## Agent UX / affordance principles (carried into every new F-rule)

Per F21, every new check ships with `fix:`, `next:`, `run:` markers. F68–F72 go further:

1. **The affordance includes a copy-paste-adaptable test template.** Not just "add a test"; literally show the function signature, the assertion shape, and one pass example.
2. **The affordance references a Pass example from the actual repo.** Subagents can grep for the example and pattern-match.
3. **The affordance lists a Forbidden example** (the anti-pattern). Subagents learn what NOT to do.
4. **Failure messages name the defect class.** "Bug 2 was bronze-side rate-limit handling without a 429 contract test" — agents see the lineage and the cost of skipping.

Mechanically: extend F21's detection to require each new check's REMEDIATION string contain `Pass example:` AND `Forbidden example:` substrings. The existing F66 + F67 messages already follow this — codify it.

## Acceptance criteria

- [ ] ADR approved (this document); user agrees with the F68–F72 + soak-tier shape
- [ ] F68 detection script + baseline + affordance text + wired in run-all.sh
- [ ] F69 detection script + baseline + affordance text + wired in run-all.sh
- [ ] F70 detection script + baseline + affordance text + wired in run-all.sh
- [ ] F71 detection script + baseline + affordance text + wired in run-all.sh
- [ ] F72 detection script + baseline + invariant-registry + 5 seed invariants + affordance text + wired in run-all.sh
- [ ] Soak workflow `soak-suite.yml` shipped; first 3 soak tests scaffolded (bronze_coverage_parity, vector_index_drift, drain_progress_at_10k)
- [ ] F7 affordance rewritten to reference ADR-024 §F68/F69
- [ ] safe-commit.sh F7-delta-warning behaviour (delta < 0.5% → warn, not fail)
- [ ] F21 detection extended to require `Pass example:` + `Forbidden example:` substrings in REMEDIATION
- [ ] CLAUDE.md F-rule list updated through F72
- [ ] Defect catalogue updated — each future defect's post-mortem must name which F-rule catches it (if no rule catches it, propose F73+ in the same post-mortem)

## Implementation phasing

**Phase 1 (this PR — ADR + foundations):** This document. F21 extension to require Pass + Forbidden examples (small).

**Phase 2 (parallel subagent bundles, low collision risk):**

- **Bundle A — F68 Protocol failure-injection:** detection script + baseline + first 3 Protocol failure-mode test files (SourceConnector, Extractor, EntityGraphSink). Per-Protocol test files are independent → low collision.
- **Bundle B — F70 schema-writer symmetry:** detection script + extension over F67 + sweep existing schema for missing writers (#336 surfaces here; addresses it in same bundle).
- **Bundle C — F71 preflight-truthfulness:** detection script + sweep existing preflights + paired truthfulness tests.

These three bundles touch separate file trees (`tests/contracts/`, `kairix/core/db/`, `scripts/checks/`) — parallelizable in worktrees.

**Phase 3 (sequential — depends on Phase 2 patterns being established):**

- **Bundle D — F69 scale-bound integration tests:** detection script + sweep `tests/integration/` for iteration patterns + add 10⁴-variant per test. Higher collision risk because every integration test author would touch their own files; do sequentially OR partition by test directory.
- **Bundle E — F72 cross-layer integrity invariants:** invariant registry + 5 seed invariants + matching test files. Each invariant is self-contained → parallelizable within the bundle.
- **Bundle F — Soak workflow + first 3 soak tests:** CI workflow + soak runner + scaffold tests. Distinct from per-commit gates → can run any time.

**Phase 4 (red/green refactor of code surfaced by gates):**

Each subagent in Phase 2/3 follows red → green → refactor:

1. **Red:** Stand up the F-rule. It fires on existing offenders. Capture the baseline.
2. **Green:** Pay down the baseline by adding the missing tests / writers / assertions. Each paydown sabotage-proven.
3. **Refactor:** Where a paydown surfaces a DRY/SOLID/encapsulation smell (e.g. "this preflight check's predicate is duplicated in three places"), extract the canonical helper. New helper is testable; old duplication goes away.

**Phase 5 (verification + retro):**

- Run the full new F-rule suite against all the recent defects in this session — assert each would now be caught at gate time
- Update the defect catalogue with the F-rule mapping
- Sunset F7's "primary gate" status in operator-facing docs

## Operational implications

**For dev / subagents:** Pre-commit and safe-commit.sh take ~10-20 seconds longer per run as the new gates execute (each is grep + small AST scan, not test runs). F69's check is the most expensive (AST scan over `tests/integration/`); cache its result by mtime in `.architecture/cache/` to keep iteration fast.

**For CI:** Stage 0 (arch fitness) gains ~5-10 seconds. Stage 2/3 unchanged. New Stage 7 (`soak`) introduced — nightly cadence only.

**For operators:** No behaviour change visible. The integrity preflight may surface new gaps as F71 lands (because previously-silent-because-capped counts become real). Operator runbooks (`integrity-and-preflight.md`) get one paragraph each per new invariant.

**For agents writing new code:** The affordance templates are the UX. A subagent landing a new Protocol method sees F68 fire with the exact test shape to copy. A subagent landing a new schema table sees F70 fire with the writer template. The cost of "I need to write the test" stops being "figure out what shape" and becomes "copy + adapt". This is the principle the user named: high agent UX / affordance to adopt.

## Migration

- **Phase 1-2:** Land foundational rules + first wave of bundles. Roughly 3-5 days of subagent work in parallel worktrees.
- **Phase 3:** Sequential bundles where collision risk exists. 2-3 more days.
- **Phase 4:** Red/green/refactor runs as part of each bundle landing — not a separate phase.
- **Phase 5:** Verification + retro at the end. ~1 day.
- **Total:** ~5-9 days of focused work. Operational alpha (#334/#335/Wave E.5) can ship in parallel with Phase 2 starting; the new gates apply to *future* PRs, not retroactively to the operational alpha that's already on `main`.

## Notes / discussion

- This ADR explicitly rejects the "ship more tests until F7 hits 90%" reflex. Coverage % isn't the goal — defect-class coverage is.
- The five new F-rules are not exhaustive; future defect post-mortems can propose F73+ when a new class surfaces. The discipline is "name the class, propose the rule".
- The soak tier is the most architecturally significant change. Operators who currently rely on production canary as the soak signal get an earlier signal that's machine-checkable.
- F7 stays because it's a useful smell signal — but failing F7 below 0.5% delta is no longer the right response. Adding a "test that calls the function once" to pad the percentage is the anti-pattern this ADR rules out.
