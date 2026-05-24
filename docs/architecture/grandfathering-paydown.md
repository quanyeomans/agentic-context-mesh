# Grandfathering paydown — state + plan

`.architecture/baseline/*.txt` was a temporary measure to land fitness
functions without rewriting every pre-existing offender at landing time.
**It is not a permanent home for tech debt.** Every non-empty baseline
file is open work; this doc tracks it.

## Resolution shapes (positive patterns)

Three valid resolutions for any baseline entry:

1. **Refactor.** Apply the rule's positive-pattern recipe (rename `param` → `_param`, extract a helper, inject a fake, etc.). The default.
2. **Exempt in rule.** Add the file to an `_ALLOWLIST_PATHS` set in the rule's detector with a one-paragraph rationale comment. Only valid when the entry represents an **architectural invariant** that the rule shouldn't try to enforce — e.g. the composition root crossing a layer boundary. Sabotage-prove the exemption is file-specific (a sibling file with the same shape still gets flagged). Canonical example: `kairix/core/factory.py` in `check_provider_layer_imports.py` (F26).
3. **Structural change.** Move the file or refactor the production surface so the rule no longer applies. Canonical example: refactor `paths.py` to accept an injected `env` dict so `test_paths.py` no longer needs `monkeypatch.setenv`.

`(1)` is the default. `(2)` is for invariants. `(3)` is for systemic
tech debt that surfaces as multiple baseline entries with the same root
cause.

## State (as of 2026-05-24, post-37964593)

Snapshot freshness is enforced by
[`scripts/checks/check_paydown_doc_currency.py`](../../scripts/checks/check_paydown_doc_currency.py).
The check runs in `release.yml` before the tag-cut step; it fails the
release if the snapshot date drifts more than 7 days from the most
recent release tag's date (unless an
`<!-- expected-out-of-date-until: YYYY-MM-DD -->` comment with
rationale is added below this paragraph).

| Baseline | Count | Status | Next move |
|---|---:|---|---|
| `cognitive-complexity-files.txt` | 0 | 🟢 resolved | Wave A KFEAT-016 paydown — 34 → 0 via helper extraction + sabotage-prove. Canonical example: `_mark_existing_vec_hit` in `kairix/core/search/rrf.py`. |
| `no-duplicate-string-files.txt` | 0 | 🟢 resolved | Wave A KFEAT-016 paydown — 38 → 0 via UPPER_SNAKE module-level constants. Canonical example: `_CONNECTOR_FRAMEWORK_OWNER` in `kairix/core/features/registry.py`. |
| `empty-body-intent-files.txt` | 0 | 🟢 resolved | Subagent added `# Intentionally empty —` / docstrings to Protocol empty bodies. |
| `no-real-names-in-fixtures-files.txt` | 0 | 🟢 resolved | Test fixtures rewritten to generic names; `reference-library/**` exempted. |
| `shellcheck-disable-with-reason-files.txt` | 0 | 🟢 resolved | `# rationale:` comments added to SC1090 disables. |
| `f26-files.txt` | 0 | 🟢 resolved | `kairix/core/factory.py` exempted in rule as composition root. |
| `f41-files.txt` | 0 | 🟢 resolved | `py.typed` added to all 7 provider packages. |
| `f42-files.txt` | 4 | 🔴 open | Replace `dict[str, Any]` / `list[dict]` Protocol return shapes with `@dataclass(frozen=True)` (or tuple thereof) per F42 spec. Canonical example: any new ExtractionResult shape under `kairix/core/protocols.py`. |
| `unused-params-named-files.txt` | 5 | 🟠 partial | Wave A KFEAT-016 paydown trimmed 33 → 5. Remaining 5 (`kairix/core/search/bm25.py`, `kairix/knowledge/entities/filters.py`, `kairix/platform/setup/wizard.py`, `kairix/quality/eval/{gold_builder,judge}.py`) need `_param`-rename — mechanical, batchable for subagent. |
| `f34-files.txt` | 6 | 🔴 open | Refactor `kairix/core/connectors/**` to talk only through `kairix.core.protocols.*` — drop direct imports of `kairix/connectors/**` / `kairix/extractors/**`. Wave-A connector-framework scaffolding paydown lands as the Protocol surface stabilises. |
| `f35-files.txt` | 7 | 🔴 open | Move cross-plugin work into `kairix/core/connectors/` — no connector should import another connector or any extractor. |
| `f40-files.txt` | 7 | 🔴 open | Declare `version: str = "..."` at module level in each `kairix/extractors/<name>/__init__.py`; thread through to `documents_media.extractor_version`. |
| `f39-files.txt` | 8 | 🔴 open | Every `Chunk(...)` write must pass `source_uri`, `source_modified_at`, AND `sensitivity` explicitly. Audit each call site; default-to-public only valid when connector config declares it. |
| `f43-files.txt` | 8 | 🔴 open | Author `tests/contracts/test_<name>_protocol.py` for each plugin — imports canonical fake AND real impl, asserts same Protocol contract on both. |
| `no-env-monkeypatch-files.txt` | 8 | 🟠 partial | 3 boundary tests (`test_paths`, `test_secrets`, `test_credentials`) → rule exemption; 5 config-loader tests need real refactor (production accepts `env` dict instead of reading `os.environ` directly). |
| `per-file-coverage-floor-union-files.txt` | 8 | 🔴 open | Add unit/integration coverage to lift each file ≥ 90% on the union of unit + integration. Two files depend on a working `SearchPipeline` in test env (`cold_start.py`) — needs an in-process integration fixture. |
| `f57-files.txt` | 9 | 🔴 open | Centralise `UPDATE topology_cc_pairs ... SET status = ?` writes through a module that declares `_ALLOWED_TRANSITIONS: dict[CCPairStatus, frozenset[CCPairStatus]]`. Wave C lifecycle work. |
| `f58-files.txt` | 9 | 🔴 open | Author `tests/contracts/test_*hierarchy*parent_before_child*` per `HierarchyConnector` implementation. Wave E pre-arm; resolves as `HierarchyConnector` impls land. |
| `f61-files.txt` | 9 | 🔴 open | Route bare `_SqliteChunkWriter(db, collection=...)` construction through `CollectionRouter` outside `kairix/core/connectors/`. Today `kairix/worker.py:_run_one_connector_batch` is the canonical grandfathered call site; Wave C rewires it. |
| `no-internal-patches-files.txt` | 9 | 🔴 open | Replace `@patch("kairix.X.Y")` / `monkeypatch.setattr("kairix.X", ...)` with constructor injection of a `Fake*` from `tests/fakes.py`. |
| `per-file-coverage-floor-files.txt` | 12 | 🔴 open | Lift each file ≥ 90% unit coverage. Several depend on lightweight `SearchPipeline` fixtures; coordinate with `per-file-coverage-floor-union-files.txt` paydown. |
| `f52-files.txt` | 13 | 🔴 open | AST-scan flags `flag("<name>")` call sites referencing names not in `kairix/core/features/registry.py:REGISTRY`. Fix: register the flag or remove the dead call site. |
| `f55-files.txt` | 13 | 🔴 open | Declare `version: str` at module level in each `kairix/chunkers/<name>/__init__.py`; thread `chunker_version=` kwarg into every `Chunk(...)` constructor call. Vacuous-green at landing; Wave C threads through Silver, Wave F lands the plugins. |
| `f51-files.txt` | 14 | 🔴 open | Each `FeatureFlag` in `REGISTRY` needs `target_retire_in` ≤ current setuptools-scm version + 6 months, OR a `# retire-extension: <reason>` rationale comment. Stops flags becoming permanent scaffolding. |
| `f36-files.txt` | 15 | 🔴 open | Author `tests/bdd/features/connector_<name>.feature` / `extractor_<name>.feature` for each plugin AND wire it into the `tests/bdd/features/e2e_connector_sync.feature` Examples table (or `@<name>_no_<journey>` opt-out tag). |
| `f54-files.txt` | 18 | 🔴 open | Each registry flag needs BDD scenarios for OFF and ON branches (`tests/bdd/features/feature_flag_<name>.feature` with ≥2 scenarios) plus integration tests exercising both branches; top-level capability flags also need an E2E composed-path test. |
| `f30-operator-outcome-tests-files.txt` | 23 | 🔴 open | Author CLI subprocess / MCP direct-handler outcome tests that assert on `.stdout`/`.stderr`/envelope content (not `returncode == 0` alone, not internal fake call-counts). Governed by F49 — must shrink ≥1 entry per release tag. |
| `f44-files.txt` | 26 | 🔴 open | Engagement-scope code must not import firm-scope storage clients (`psycopg`, `psycopg2`, `asyncpg`, `pg8000`, `aiopg`). Refactor each call site through the reflection-extractor boundary into `kairix-firm/`. |
| `no-internal-test-imports-files.txt` | 29 | 🔴 open | Tests import via public surface (`kairix.<module>.public_name`) instead of attribute access on private (`_`-prefixed) names. |
| `f46-files.txt` | 32 | 🔴 open | Refactor each BDD step file to route through `factory.build_*(paths=FakePaths(...))`. Canonical pattern: `tests/integration/test_vec_index_lifecycle.py`. Governed by F49 — must shrink ≥1 entry per release tag. |
| `f47-integration-factory-files.txt` | 35 | 🔴 open | Convert each integration test to construct via `kairix.core.factory.build_*` with `paths=FakePaths(...)`. Governed by F49 — must shrink ≥1 entry per release tag. |
| `test-only-kwargs-allow-files.txt` | 71 | 🔴 open | Refactor production to take dependency as a default `Fake*` constructor argument (no `_fn=None` test-only kwarg in production); tests inject explicitly. |

**Empty-but-retained baselines** (30): kept as canonical paydown records per the F50 test that walks `.architecture/baseline/`. These remain at zero entries to document that the rule has a baseline mechanism even though no offenders currently exist —
`actionable-feedback-files.txt`,
`bdd-no-implementation-leaks-files.txt`,
`cognitive-complexity-files.txt`,
`empty-body-intent-files.txt`,
`env-reads-in-paths-files.txt`,
`f26-files.txt`,
`f27-files.txt`,
`f28-files.txt`,
`f29-files.txt`,
`f37-files.txt`,
`f38-files.txt`,
`f41-files.txt`,
`f45-files.txt`,
`f56-files.txt`,
`go-dependency-rationale-files.txt`,
`go-logging-discipline-files.txt`,
`go-no-panic-outside-main-files.txt`,
`go-readme-coverage-files.txt`,
`go-version-flag-files.txt`,
`no-commented-out-code-files.txt`,
`no-duplicate-string-files.txt`,
`no-logging-secrets-files.txt`,
`no-real-names-in-fixtures-files.txt`,
`no-test-imports-in-prod-files.txt`,
`no-test-only-kwargs-files.txt`,
`path-naming-files.txt`,
`readme-coverage-files.txt`,
`shellcheck-disable-with-reason-files.txt`,
`suppressions-have-rationale-files.txt`.

## Sequencing recommendation

Dispatch in three waves of parallel subagents. Wave A's mechanical
batches (F16 / F17 / F20 / F32) are done as of KFEAT-016 (2026-05-23);
F19 is down to 5 stragglers. The composition and architectural waves
sit on top of the new Wave-A/B/C connector-framework + topology-v2
baselines that landed concurrently.

**Wave A — mechanical batches (low risk, high volume):**
- F19 `unused-params-named` (5 remaining) — rename to `_param`
- F40 `extractor-version` (7) — add module-level `version: str` to each plugin `__init__.py`
- F55 `chunker-version` (13) — same shape; thread `chunker_version=` kwarg through `Chunk(...)`
- F39 `chunk-metadata` (8) — audit `Chunk(...)` call sites for `source_uri` / `source_modified_at` / `sensitivity`

**Wave B — composition-pattern refactors (after Wave A):**
- F46 `bdd-step-composition` (32) — route through factory; governed by F49 paydown schedule
- F47 `integration-factory` (35) — same pattern, integration tests; governed by F49
- F30 `operator-outcome-tests` (23) — author CLI subprocess / MCP handler outcome tests; governed by F49
- F1 `no-internal-patches` (9) — replace `@patch` with Fake injection
- F5 `no-internal-test-imports` (29) — public-surface imports
- F6 `test-only-kwargs-allow` (71) — default-arg Fake* constructor
- F36 `connector-bdd-parity` (15) — author `connector_<name>` / `extractor_<name>` features + wire into e2e Examples
- F54 `flag-both-branch-tested` (18) — author OFF/ON BDD scenarios + integration tests for each registry flag

**Wave C — architectural changes (sequential, sized):**
- F34 `core-connector-layer-imports` (6) — drop direct `kairix/connectors/**` / `kairix/extractors/**` imports from `kairix/core/connectors/`
- F35 `no-cross-connector` (7) — move cross-plugin work into `kairix/core/connectors/`
- F42 `protocol-return-types` (4) — replace `dict[str, Any]` / `list[dict]` returns with frozen dataclasses
- F43 `plugin-contract-tests` (8) — author per-plugin contract tests
- F44 `engagement-firm-boundary` (26) — refactor firm-scope storage-client call sites through the reflection-extractor
- F2 `no-env-monkeypatch` (8) — refactor production to take `env` dict (3 boundary tests then exempted in rule)
- F7/F9 coverage floors (`per-file-coverage-floor-files.txt` 12, `per-file-coverage-floor-union-files.txt` 8) — author lightweight integration fixtures
- F51 `flag-retirement` (14) — set `target_retire_in` (or rationale comment) on every flag
- F52 `flag-call-sites` (13) — register or remove dead `flag("<name>")` references
- F57 `ccpair-lifecycle-integrity` (9) — centralise `UPDATE topology_cc_pairs ... SET status = ?` through `_ALLOWED_TRANSITIONS`
- F58 `hierarchy-parent-before-child` (9) — author contract test alongside each `HierarchyConnector` impl as Wave E lands
- F61 `collection-router-singleton` (9) — route `_SqliteChunkWriter` construction through `CollectionRouter` outside `kairix/core/connectors/`

## Deprecation endgame

When every baseline file is at zero entries AND no rule's behaviour
depends on reading a baseline:

1. Delete `_arch_lib.gate()`'s baseline-reading branch (the
   `if baseline_file.exists()` block).
2. Delete the `.architecture/baseline/` directory entirely.
3. Update the F50 test to assert "no baseline mechanism remains" instead
   of "F30 baseline file exists".
4. Each rule is now strict-mode by construction: net-new and existing
   violations both block.

The single commit that does (1)–(3) is the structural deprecation. It
becomes safe to make only after every baseline reaches zero.
