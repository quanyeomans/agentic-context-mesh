# ADR-026 — Cross-cutting primitive abstractions

**Status:** Proposed.
**Supersedes (in part):** ADR-025 §4 Pattern B (per-call-site `emit_for(...)` instrumentation at every pipeline stage entry-point).
**Drives:** F74 (status_emit coverage — restated as a structural check over `Stage` subclasses), F77 (every fitness check is a `FitnessRule` subclass), F78 (every feature flag is a `FlagGatedCapability` subclass).

**Related audits (executed 2026-05-29):**
- Stage + Pipeline abstraction audit (12 stages identified across `ConnectorPipeline` + `MaintenanceScheduler`; strong existing analog in `MaintenanceScheduler._safe_*` wrappers).
- F-rule check harness audit (25 check scripts; 19 fit a 3-line subclass shape; existing nucleus at `scripts/checks/_arch_lib.py`).
- Feature-flag pattern audit (19 flags; 8 fit a `FlagGatedCapability` ABC cleanly; 11 need pre-work).

## 1. Context

The 2026-05-29 review of ADR-025 §4 §Pattern B exposed that the proposed per-call-site `emit_for(...)` wrapping creates a blast radius the architecture itself shouldn't tolerate. The smell traced to three missing primitives — none of which is exclusive to observability:

1. **No `Stage` abstraction.** The codebase has six per-item pipeline steps (fetch, bronze, extract, silver, chunk-write, entity-sink) plus six per-tick maintenance steps (prune_orphans, gc_pruned, usearch_rebuild, fts_heal, bronze_reap, bronze_ttl_gc). Each step is a Protocol or a method, but they share no parent type. The `MaintenanceScheduler._safe_*` wrappers (six near-identical try/log/return-zero blocks) are the most direct evidence: an abstraction has been reinvented six times rather than declared once.

2. **No `FitnessRule` ABC.** The 25 check scripts under `scripts/checks/check_f*.py` repeat a six-stage skeleton (load baseline → enumerate files → scan each → categorise net-new → emit F21 remediation → exit code). An existing nucleus exists at `scripts/checks/_arch_lib.py` (`gate()`, `python_files()`, `repo_relative()`), but the wrapper around it isn't a class — every check re-implements the same `main()` body. ~200-250 lines of duplication across the suite.

3. **No `FlagGatedCapability` ABC.** F54 mandates that every flag in `REGISTRY` ships with BDD (OFF + ON scenarios), integration tests (`FakeFeatureFlagResolver().with_flag(name, True/False)`), and matching production call-site branching. The current `dispatch_<name>_sync(read_flag, on_branch, off_branch)` pattern repeats verbatim across 8 of 19 flags (~80% shared scaffold per flag, ~265 lines per flag including E2E). The F54 detector enforces the shape via regex; the shape itself isn't expressed in code.

The three primitives are independent — each pays down its own debt — but they share a design principle: **abstractions that the docs already name as if they existed should also exist in code**. The audit findings (§11) showed every primitive has an existing analog (latent class or repeated pattern); none is greenfield design.

This ADR captures all three as a single coordinated work-stream because:

- Phase 1 of ADR-025 is blocked on Primitive A (Stage). Landing it removes the per-call-site emit_for instrumentation tax.
- Primitives B and C unblock F74 (the new fitness rule for ADR-025 itself) and the `pipeline_status_emit` flag's BDD/test scaffold — meaning ADR-025 Phase 1 is materially smaller if A+B+C land first.
- Doing them together prevents three near-duplicate ADRs each defining "an ABC for the X repetition class".

## 2. Decision

Three primitives, three independently shippable tracks, sequenced for dependency:

```
Track A (Stage + StageRunner)        Track B (FitnessRule)              Track C (FlagGatedCapability)
─────────────────────────────         ──────────────────────             ──────────────────────────────
Unblocks ADR-025 Phase 1 instrum.    Independent; collapses 19/25       Depends on Track A
StageRunner is the ONE emit site     checks to 3-line subclasses        Auto-generates F54 artefacts

P1 → P2 → P3 of ADR-025 then          F74 (status_emit coverage)         Migration: 8 flags clean,
become structurally enforced          becomes a FitnessRule subclass     11 need pre-work
```

Each track has its own DoD, own rollout flag, own retirement window. Cross-cutting principle: **the abstraction must be reverse-engineered from existing implementers, not designed greenfield**. Every line of the proposed ABCs below maps to existing code.

## 3. Cross-cutting principles

### P1 — Reverse-engineered shape, not greenfield design

Every abstraction's surface (method names, parameter types, return types) is drawn from the common denominator of existing implementers. New abstractions don't invent new vocabulary; they collapse vocabulary that already exists in N places into one place. The audit reports (§11) name the existing analog for each primitive.

### P2 — Migration via subclass, not rewrite

Existing implementers become subclasses of the new ABC. The body of the old code becomes the body of one abstract-method override. Tests survive as-is by importing the subclass instead of the function. F50 prohibits net-new baseline accretion, so the migration itself can't grow debt.

### P3 — Old patterns retire, not coexist

Each primitive ships with a `target_retire_in` window for the old pattern. F-rules block the old pattern from being used in net-new code while permitting the existing baseline to drain. Coexistence is bounded.

### P4 — Structural enforcement replaces regex enforcement

F54 currently scans test files for `with_flag("<name>", False)` and `with_flag("<name>", True)` regex matches — fragile against refactors. Track C makes F54 a structural check: every flag is a `FlagGatedCapability` subclass; the OFF/ON tests are generated from the subclass declaration; the regex check is retired. Same shape applies to F74 (Track A) and other gates Track B enables.

### P5 — Naming collisions resolved up front

`EntityGraphSink.stage()` currently means "write to the staging table". With `Stage` becoming a first-class abstraction in Track A, the method is renamed to `EntityGraphSink.buffer()` in a pre-work commit. Naming conflicts are debt; resolving them costs less than living with them.

### P6 — Cross-cutting concerns live in the runner, not the stage

`StageRunner.run()` is the only call site that touches `emit_for`. `FitnessRule.run()` is the only call site that touches `gate()`. `FlagGatedCapability.dispatch()` is the only call site that reads `flag(...)`. Subclasses stay free of cross-cutting concerns — pure transforms. Adding a new cross-cutting concern (e.g. distributed tracing) touches one file per primitive, not N call sites.

## 4. Primitive A — `Stage` + `StageRunner`

### Audit findings (summary)

The codebase has two stage families with different failure semantics:

| Family | Members | Failure semantics | Existing analog |
|---|---|---|---|
| Connector pipeline | fetch, bronze, extract, silver, chunk_write, entity_sink | Asymmetric: fetch+extract → dead-letter + continue; silver+chunk+entity → propagate + batch rollback | `ConnectorPipeline._process_item` (kairix/core/connectors/pipeline.py:412-495) |
| Maintenance scheduler | prune_orphans, gc_pruned, usearch_rebuild, fts_heal, bronze_reap, bronze_ttl_gc | Uniform: absorb into log warning + return zero-value; continue tick | `MaintenanceScheduler._safe_*` wrappers (kairix/core/maintenance/scheduler.py:443-536) |

Obstacles surfaced by the audit:
- Heterogeneous input shapes (item_id: str, raw: bytes+mime, BronzeRef+ExtractedDocument, sqlite3.Connection, nothing)
- Transaction ownership is outside individual stages
- `ChunkWriter` is local to `pipeline.py`, not in `protocols.py`
- `EntityGraphSink.stage()` naming collision

### Shape

```python
# kairix/core/observability/stage.py  (NEW)

@dataclass(frozen=True)
class StageContext:
    """Whatever the stage needs to run. Subclasses can carry typed fields."""
    source_name: str
    item_id: str
    # Concrete contexts add typed fields: BronzeStageContext carries raw bytes etc.

@dataclass(frozen=True)
class StageOutcome:
    """The unified result envelope. Replaces None/int/bool returns from existing
    code; informs both status_emit AND control flow."""
    code: StatusCode
    output: Any | None = None         # the stage's "useful" return value
    detail: dict[str, Any] = field(default_factory=dict)

class Stage(Protocol):
    """A pure transform from StageContext to StageOutcome. No telemetry awareness,
    no emit calls, no log statements. The StageRunner handles all cross-cutting
    concerns."""
    name: str                          # matches the existing STAGE_* constants

    def process(self, ctx: StageContext) -> StageOutcome: ...

    def classify_exception(self, exc: BaseException) -> StatusCode:
        """Map a raised exception to a StatusCode. Default impl returns
        PIPELINE_STAGE_NO_EMIT; subclasses override for stage-specific
        exception → code mappings (DiskFullError → EXTRACT_DISK_FULL, etc.).
        Centralises the mapping per stage rather than per call site."""
        ...
```

Two runner variants, mirroring the existing failure semantics:

```python
class IsolatedStageRunner:
    """Absorbs all failures into the status timeline; never raises.
    Mirrors MaintenanceScheduler._safe_* semantics."""

    def __init__(self, stage: Stage, *, db: sqlite3.Connection | None) -> None: ...

    def run(self, ctx: StageContext) -> StageOutcome:
        with emit_for(ctx.source_name, ctx.item_id, self._stage.name, db=self._db) as emit:
            try:
                outcome = self._stage.process(ctx)
            except BaseException as exc:
                code = self._stage.classify_exception(exc)
                outcome = StageOutcome(code=code, detail={"exception_class": type(exc).__name__})
            emit_for_outcome(emit, outcome)
            return outcome


class BatchTransactionStageRunner:
    """Per-item failures up to a threshold absorb into dead_letter; raises on
    the final batch-level commit failures. Mirrors ConnectorPipeline._process_item
    semantics — the fetch/extract stages get IsolatedStageRunner; the silver/
    chunk/entity stages get BatchTransactionStageRunner."""

    def run_per_item(self, ctx: StageContext) -> StageOutcome:
        # Catches fetch/extract failures; writes to dead_letter; returns
        # SKIPPED outcome so the caller breaks the chain for this item.
        ...

    def run_batch_critical(self, ctx: StageContext) -> StageOutcome:
        # Catches silver/chunk/entity failures; emits then re-raises so the
        # caller rolls back the SQLite transaction.
        ...
```

### Pre-work (lands before Track A)

| # | Pre-work | Reason |
|---|---|---|
| A.0a | Rename `EntityGraphSink.stage()` → `EntityGraphSink.buffer()` (kairix/core/protocols.py + all implementers + all tests) | Resolve naming collision before `Stage` becomes a top-level class |
| A.0b | Move `ChunkWriter` Protocol from `kairix/core/connectors/pipeline.py:112` to `kairix/core/protocols.py` | Make all six pipeline stages visible at the same Protocol layer |
| A.0c | Extract typed `StageContext` subclasses (FetchContext, ExtractContext, SilverContext, etc.) from the existing `_process_item` parameter passing | Replace the heterogeneous-input obstacle with explicit typed contexts |

### Migration

Existing implementers become `Stage` subclasses. The body of `connector.fetch()` becomes the body of `FetchStage.process()`. The exception classes the existing code raises become entries in `FetchStage.classify_exception()`.

`ConnectorPipeline.run_batch` shrinks from ~80 lines to ~10:

```python
def run_batch(self, connector, extractor) -> RunBatchResult:
    for change in connector.list_changes(self._cursor):
        ctx = self._build_context(change)
        for stage_runner in self._stages:
            outcome = stage_runner.run(ctx)
            if outcome.code.severity == Severity.ERROR:
                break
            ctx = ctx.with_output(outcome.output)
```

`MaintenanceScheduler.tick` similarly shrinks: the six `_safe_*` methods all become subclasses of `IsolatedStage` running through `IsolatedStageRunner`.

### Track A definition of done

| # | Criterion | Verification |
|---|---|---|
| A.1 | A.0a/b/c pre-work landed and committed | git log shows three pre-work commits |
| A.2 | `kairix/core/observability/stage.py` exports `Stage`, `StageContext`, `StageOutcome`, `IsolatedStageRunner`, `BatchTransactionStageRunner` | tests/contracts/test_stage_protocol.py exists |
| A.3 | All 6 connector pipeline stages migrated to Stage subclasses; `ConnectorPipeline.run_batch` is ≤30 lines | tests/integration/test_connector_pipeline.py passes with new shape |
| A.4 | All 6 maintenance scheduler stages migrated; `MaintenanceScheduler.tick` is ≤30 lines | tests/integration/test_maintenance_scheduler.py passes |
| A.5 | F74 ships as a structural check: "every Stage subclass is only invoked via a StageRunner" | scripts/checks/check_f74_stage_runner_only.py + tests/checks/test_f74_*.py |
| A.6 | `pipeline_status_emit` flag wiring works: flag-ON routes through StageRunner with db; flag-OFF runs StageRunner with db=None | tests/integration/test_feature_flag_pipeline_status_emit.py passes (already exists) |
| A.7 | ADR-025 §4 Pattern B explicitly marked superseded by this ADR | ADR-025 edit + ADR-026 link |
| A.8 | Staging soak: 24h with all stages running through the new runners; zero `PIPELINE_STAGE_NO_EMIT` codes recorded | Operator-attested |

### Track A phase gate

A.1-A.8 green AND ADR-026 accepted by operator review. ADR-025 Phase 1 then resumes against the new abstraction (Phase 1 DoD 1.3 becomes structural rather than per-call-site).

## 5. Primitive B — `FitnessRule` ABC

### Audit findings (summary)

Of 25 check scripts under `scripts/checks/`:
- 19 follow a 6-stage skeleton (load baseline → enumerate files → scan each → categorise net-new vs grandfathered → emit F21 remediation → exit code)
- 5 are genuine outliers (F50 cross-baseline, F7/F9 XML input, F21 meta-check, F14 single-file backward scan, sonar-new-code HTTP API)
- 1 (F33) is shell-only and delegates to the rationale check

Existing nucleus at `scripts/checks/_arch_lib.py` provides `gate()`, `python_files()`, `repo_relative()` — but the wrapper around it is hand-rolled in every check.

Variation points:
- Detection logic (irreducibly unique per rule)
- Scope predicate (4 shapes: tuple of (prefix, ext), rglob over subtree, `python_files()`, single-file)
- Exemption mechanism (EXEMPT_FILES vs ALLOWLIST_PATHS vs baseline-only)
- Stale-baseline detection (present in F32/F73, absent from `gate()`-delegating checks)

### Shape

```python
# scripts/checks/_fitness_rule.py  (NEW)

class FitnessRule(ABC):
    """Concrete subclasses declare a small handful of class attributes plus
    one `file_has_violation` method. The 6-stage skeleton is implemented in
    `run()` once and inherited."""

    name: ClassVar[str]                           # gate name → baseline filename
    remediation: ClassVar[str]                    # F21-compliant REMEDIATION string
    roots: ClassVar[tuple[str, ...]] = ("kairix",)
    extensions: ClassVar[tuple[str, ...]] = (".py",)
    exempt_files: ClassVar[frozenset[str]] = frozenset()
    check_stale_baseline: ClassVar[bool] = False  # F32/F73 turn on; others don't

    @abstractmethod
    def file_has_violation(self, path: Path) -> bool | list[str]:
        """The only thing every subclass MUST override. Returns bool (any
        violation) or list[str] (per-violation detail lines). Detail lines
        appear in failure output above the REMEDIATION block."""

    def is_in_scope(self, rel: str) -> bool:
        """Default: startswith one of `roots` AND endswith one of `extensions`.
        Override for non-standard scope predicates."""

    def run(self) -> int:
        """Concrete: enumerate → scan → gate → exit. Calls _arch_lib.gate()
        for net-new detection, adds stale-baseline detection if enabled."""
```

Migration shape — most existing checks collapse to:

```python
# scripts/checks/check_f44_engagement_firm_boundary.py  (NEW shape)

class F44(FitnessRule):
    name = "f44-engagement-firm-boundary"
    remediation = REMEDIATION  # the existing constant
    roots = ("kairix",)
    extensions = (".py",)
    exempt_files = _EXEMPT_FILES

    def file_has_violation(self, path: Path) -> bool:
        # The 19-line existing AST walk, unchanged.
        ...

if __name__ == "__main__":
    raise SystemExit(F44().run())
```

The 19 checks that fit this shape collapse from ~50-80 lines each to ~25 lines each. Estimated savings: ~200-250 lines of structural boilerplate removed.

### Migration plan

| Group | Count | Migration cost |
|---|---|---|
| Clean fit (F-rules where only detection differs) | 19 | ~1 day for all (mechanical refactor) |
| Modest accommodation (F32, F73 — stale-baseline + scope variants) | 2 | Override 1-2 hook methods |
| Special case (F6 — allow-list baseline, per-param reporting) | 1 | Override `format_violation_output()` |
| F28 — plugin-name violation key | 1 | Override `is_in_scope()` to walk directories |
| Outliers — stay standalone | 5 | F50, F7/F9, F21, F14, sonar-new-code unchanged |

### Track B definition of done

| # | Criterion | Verification |
|---|---|---|
| B.1 | `scripts/checks/_fitness_rule.py` exports `FitnessRule` ABC + tests/checks/test_fitness_rule.py pins the contract | Unit tests green |
| B.2 | Migrate 5 representative checks (F44, F26, F8, F19, F32) to subclass shape; each new check's behaviour matches the old check's behaviour | Bit-for-bit diff of pre/post failure output |
| B.3 | Migrate remaining 14 clean-fit checks | All check_f*.py runs return identical exit codes to pre-migration |
| B.4 | F77 (every `scripts/checks/check_f*.py` whose first executable statement is a class declaration must inherit from `FitnessRule`) ships with baseline at zero | scripts/checks/check_f77_fitness_rule_subclass.py |
| B.5 | Stale-baseline detection optional hook works (F32 + F73 turn it on) | tests/checks/test_f32_stale.py, tests/checks/test_f73_stale.py |
| B.6 | Old `_arch_lib.gate()` becomes a thin shim over `FitnessRule().run()` | _arch_lib.py shrinks to ~20 lines |

### Track B phase gate

B.1-B.6 green. ~200 lines net deletion across `scripts/checks/`.

## 6. Primitive C — `FlagGatedCapability` ABC

### Audit findings (summary)

19 flags in `REGISTRY`. Four call-site patterns observed:
- Pattern 1 (8 flags): named `dispatch_<name>_sync(read_flag, on_branch, off_branch)` function
- Pattern 2 (1 flag — `maintenance_loop`): Deps-injected `flag_reader` in a Deps dataclass
- Pattern 3 (1 flag — `topology_v2_runtime`): inline `flag()` call passing boolean argument
- Pattern 4 (1 flag — `pipeline_status_emit`): local context-modifier (chooses db vs None)

Per-flag scaffold: ~265 lines for a connector-gating flag (registry + dispatch fn + OFF noop + ON wrapper + BDD + integration test + E2E), of which ~75% is the same shape repeated.

F54 detector is regex-based: scans tests for `with_flag(<name>, False)` and `with_flag(<name>, True)`. Fragile against renames; doesn't enforce the dispatcher shape itself.

### Shape

```python
# kairix/core/features/capability.py  (NEW)

T = TypeVar("T")

class FlagGatedCapability(Generic[T], ABC):
    """A capability that runs different code paths depending on a flag.
    Subclasses declare the flag name, the ON branch, and the OFF branch."""

    flag_name: ClassVar[str]
    on_marker: ClassVar[str]    # INFO log emitted when ON branch runs
    off_marker: ClassVar[str]   # INFO log emitted when OFF branch runs

    @abstractmethod
    def run_on(self) -> T:
        """The ON-branch implementation. Real work happens here when
        flag(flag_name) returns True."""

    @abstractmethod
    def run_off(self) -> T:
        """The OFF-branch implementation. Default behaviour, no-op, or
        legacy path."""

    def dispatch(
        self,
        read_flag: Callable[[str], bool] = _default_flag_value,
    ) -> T:
        if read_flag(self.flag_name):
            logger.info(self.on_marker)
            return self.run_on()
        logger.info(self.off_marker)
        return self.run_off()
```

A new flag = one ~15-line subclass. The BDD feature + integration test get auto-generated from class metadata by a Jinja template + a `parametrize_both_branches` pytest fixture (added to `tests/fakes.py`).

### Migration plan

| Group | Count | Migration cost |
|---|---|---|
| Clean fit (8 connector-gating flags via Pattern 1) | 8 | Each `dispatch_<name>_sync` becomes a 15-line subclass |
| Deps-injected variant (maintenance_loop) | 1 | Subclass returning `None` instead of `ConnectorSyncResult` (`FlagGatedCapability[None]`) |
| Context-modifier variant (pipeline_status_emit) | 1 | Subclass that returns the chosen `db` value (`FlagGatedCapability[sqlite3.Connection \| None]`) |
| Inline-call variant (topology_v2_runtime) | 1 | Pre-work: extract `resolve_chunk_writer_for_entry`'s branching into named methods, then subclass |
| Topology Protocol-method gating (9 topology_v2_* flags) | 9 | Pre-work: each connector grows a `dispatch_topology_v2_*` entry-point; then subclass |

The 9 topology flags need the most pre-work because they gate inside Protocol method bodies rather than at a dispatch boundary. Track C's first iteration handles the 11 flags that fit cleanly or with modest accommodation; the 9 topology flags wait for a follow-up wave.

### Track C definition of done

| # | Criterion | Verification |
|---|---|---|
| C.1 | `kairix/core/features/capability.py` exports `FlagGatedCapability` ABC | tests/contracts/test_capability_protocol.py |
| C.2 | 8 connector-gating flags migrated to subclasses; behaviour preserved | tests/integration/test_feature_flag_*.py pass |
| C.3 | `maintenance_loop` + `pipeline_status_emit` migrated with appropriate generic types | tests/integration/test_feature_flag_maintenance_loop.py + test_feature_flag_pipeline_status_emit.py |
| C.4 | `topology_v2_runtime` pre-work + migration | tests/integration/test_feature_flag_topology_v2_runtime.py |
| C.5 | F54 replaced by F78 (structural check: every flag in REGISTRY has a corresponding `FlagGatedCapability` subclass) | scripts/checks/check_f78_flag_capability_subclass.py |
| C.6 | `parametrize_both_branches(capability_cls)` pytest fixture added to tests/fakes.py | Used by at least 5 migrated tests |
| C.7 | BDD feature template generation: a `tools/scaffold_flag.py` script generates `feature_flag_<name>.feature` + `test_feature_flag_<name>.py` skeletons from a `FlagGatedCapability` subclass declaration | New flag wave needs ~5 lines of code + script run |
| C.8 | 9 topology_v2_* flags tracked as a follow-up wave under a separate ADR (cite from here) | Cross-reference added |

### Track C phase gate

C.1-C.8 green. Per-flag scaffolding drops from ~265 lines to ~30 lines.

## 7. Order of work

Three tracks, sequenced:

1. **Track A first** (pre-work A.0a/b/c, then `Stage`/`StageRunner`, then migrate 12 stages, then F74 swap).
   - Unblocks ADR-025 Phase 1 instrumentation
   - Highest leverage: removes the blast-radius smell

2. **Track B in parallel with A's migration** (independent — touches `scripts/checks/`, not `kairix/`).
   - Lowest risk: pure refactor with bit-for-bit behaviour preservation
   - Pays down ~200 lines of duplication

3. **Track C last** (depends on Track A for the `pipeline_status_emit` flag's StageRunner-aware migration).
   - Per-flag scaffold collapse + auto-generation
   - Defers the 9 topology_v2_* flags to a follow-up wave

## 8. Open questions

1. **Cross-cutting concerns beyond emit_for.** Does `StageRunner` also become the home for: per-stage tracing spans? Per-stage metric emission? Rate-limiting? Yes in principle, but Track A scope is *only* emit_for + exception classification. Other concerns are follow-ups; the runner is designed to absorb them later.

2. **Maintenance scheduler stages and the `entity_drain` follow-up.** The 2026-05-29 audit found 2.26M `entity_signals` pending Neo4j drain. That drain is currently neither a connector pipeline stage nor a maintenance scheduler stage — it's a worker function. Track A's stage abstraction creates the natural home for it. Drain becomes a maintenance Stage with IsolatedStageRunner. Tracked separately.

3. **FlagGatedCapability for the topology_v2 fleet.** 9 flags gate behaviour inside connector Protocol methods. The right pre-work is making each connector expose a `dispatch_topology_v2_*()` entry-point. That's a separate wave because it touches connector plugins (every one), not framework code. Tracked as a follow-up ADR.

4. **Should `FitnessRule` migrate the 5 outliers eventually?** F50 and F7/F9 have legitimate reasons to stay standalone (cross-baseline, XML input). F21 is a meta-check on the FitnessRule subclasses themselves — could be a FitnessRule but its detection is fundamentally different. F14 is single-file. Sonar parity isn't a gate at all. Recommendation: leave the 5 standalone permanently; FitnessRule isn't the only shape.

## 9. References

- ADR-025 — Pipeline observability + agent-actionable status surface (§4 Pattern B superseded by Track A)
- `docs/architecture/connector-ingestion-architecture.md` — pipeline-stage prose that Track A makes structural
- `docs/architecture/fitness-functions.md` — F-rule canon (Track B consolidates the implementation)
- `docs/architecture/feature-flag-architecture.md` — flag spec (Track C makes the both-branch requirement structural)
- `scripts/checks/_arch_lib.py` — existing nucleus that becomes the body of `FitnessRule.run()`
- `kairix/core/maintenance/scheduler.py:443-536` — the `_safe_*` wrappers that become Stage subclasses
- `kairix/core/features/registry.py` — current flag registry that Track C migrates
- Phase 1A audit findings — ADR-025 §11 (the 2026-05-29 production audit that surfaced the blast-radius smell)
