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

## State (as of 2026-07-04, post-419f8619)

Snapshot freshness is enforced by
[`scripts/checks/check_paydown_doc_currency.py`](../../scripts/checks/check_paydown_doc_currency.py).
The check runs in `release.yml` before the tag-cut step; it fails the
release if the snapshot date drifts more than 7 days from the most
recent release tag's date (unless an
`<!-- expected-out-of-date-until: YYYY-MM-DD -->` comment with
rationale is added below this paragraph).

Refresh policy: regenerate counts via `wc -l .architecture/baseline/*.txt`,
bump the snapshot date + HEAD SHA on the header, and update `Status`
for any baseline that moved (open ↔ partial ↔ resolved) or zeroed out
(promote to **Empty-but-retained**). The `Next move` cell only needs
rewriting when the recommended approach changes — same-count rows
keep the prior cell unchanged.

| Baseline | Count | Status | Next move |
|---|---:|---|---|
| `cognitive-complexity-files.txt` | 10 | 🔴 open | **Regressed since the 2026-06-09 snapshot** (was 0 → now 10) — re-apply the prior paydown. Wave A KFEAT-016 paydown — 34 → 0 via helper extraction + sabotage-prove. Canonical example: `_mark_existing_vec_hit` in `kairix/core/search/rrf.py`. |
| `no-duplicate-string-files.txt` | 10 | 🔴 open | **Regressed since the 2026-06-09 snapshot** (was 0 → now 10) — re-apply the prior paydown. Wave A KFEAT-016 paydown — 38 → 0 via UPPER_SNAKE module-level constants. Canonical example: `_CONNECTOR_FRAMEWORK_OWNER` in `kairix/core/features/registry.py`. |
| `empty-body-intent-files.txt` | 10 | 🔴 open | **Regressed since the 2026-06-09 snapshot** (was 0 → now 10) — re-apply the prior paydown. Subagent added `# Intentionally empty —` / docstrings to Protocol empty bodies. |
| `no-real-names-in-fixtures-files.txt` | 0 | 🟢 resolved | Test fixtures rewritten to generic names; `reference-library/**` exempted. |
| `shellcheck-disable-with-reason-files.txt` | 0 | 🟢 resolved | `# rationale:` comments added to SC1090 disables. |
| `f26-files.txt` | 0 | 🟢 resolved | `kairix/core/factory.py` exempted in rule as composition root. |
| `f41-files.txt` | 0 | 🟢 resolved | `py.typed` added to all 7 provider packages. |
| `f43-files.txt` | 94 | 🔴 open | **Regressed since the 2026-06-09 snapshot** (was 0 → now 94) — re-apply the prior paydown. Protocol contract tests authored across all plugin trees during v2026.5–6 sweep. |
| `no-internal-patches-files.txt` | 2 | 🟠 partial | 9 → 2 across v2026.6 series via Fake injection refactors (per `feedback_no_monkeypatch`). Two stragglers remain — replace `@patch("kairix.X.Y")` / `monkeypatch.setattr("kairix.X", ...)` with constructor injection of a `Fake*` from `tests/fakes.py`. |
| `f42-files.txt` | 4 | 🔴 open | Replace `dict[str, Any]` / `list[dict]` Protocol return shapes with `@dataclass(frozen=True)` (or tuple thereof) per F42 spec. Canonical example: any new ExtractionResult shape under `kairix/core/protocols.py`. |
| `no-env-monkeypatch-files.txt` | 5 | 🟠 partial | 8 → 5 across v2026.6 series. Remaining 5 config-loader tests need real refactor (production accepts `env` dict instead of reading `os.environ` directly). |
| `f34-files.txt` | 6 | 🔴 open | Refactor `kairix/core/connectors/**` to talk only through `kairix.core.protocols.*` — drop direct imports of `kairix/connectors/**` / `kairix/extractors/**`. Wave-A connector-framework scaffolding paydown lands as the Protocol surface stabilises. |
| `no-internal-test-imports-files.txt` | 6 | 🟠 partial | 29 → 6 across v2026.6 series — major public-surface refactor in BDD + integration tiers. Remaining 6 stragglers import via attribute access on private (`_`-prefixed) names; rewrite each via `kairix.<module>.public_name`. |
| `f35-files.txt` | 7 | 🔴 open | Move cross-plugin work into `kairix/core/connectors/` — no connector should import another connector or any extractor. |
| `f40-files.txt` | 7 | 🔴 open | Declare `version: str = "..."` at module level in each `kairix/extractors/<name>/__init__.py`; thread through to `documents_media.extractor_version`. |
| `f39-files.txt` | 8 | 🔴 open | Every `Chunk(...)` write must pass `source_uri`, `source_modified_at`, AND `sensitivity` explicitly. Audit each call site; default-to-public only valid when connector config declares it. |
| `no-private-infra-refs-files.txt` | 8 | 🔴 open | F73 — externalise hardcoded references to private infra identifiers (org slugs, internal hostnames) into operator config or test fixtures. Net-new baseline introduced post-v2026.6.0; reflects historical fixtures + telemetry strings. |
| `actionable-feedback-files.txt` | 9 | 🟠 partial | F21 affordance pattern (`fix:` / `next:` / `run:` on every check-script failure). 9 grandfathered call sites in `scripts/checks/` need their error blocks rewritten; net-new check scripts already pass. |
| `f57-files.txt` | 9 | 🔴 open | Centralise `UPDATE topology_cc_pairs ... SET status = ?` writes through a module that declares `_ALLOWED_TRANSITIONS: dict[CCPairStatus, frozenset[CCPairStatus]]`. Wave C lifecycle work. |
| `f58-files.txt` | 9 | 🔴 open | Author `tests/contracts/test_*hierarchy*parent_before_child*` per `HierarchyConnector` implementation. Wave E pre-arm; resolves as `HierarchyConnector` impls land. |
| `f61-files.txt` | 9 | 🔴 open | Route bare `_SqliteChunkWriter(db, collection=...)` construction through `CollectionRouter` outside `kairix/core/connectors/`. Today `kairix/worker.py:_run_one_connector_batch` is the canonical grandfathered call site; Wave C rewires it. |
| `f77-sqlite-single-writer-files.txt` | 11 | 🔴 open | Route `sqlite3.connect` outside the allow-list (worker / factory / scripts / tests) through a centralised writer. Net-new baseline (ADR-026 §observability). |
| `f76-pii-content-interpolation-files.txt` | 12 | 🔴 open | Replace f-string interpolation of content-like vars (`raw`/`body`/`payload`/`markdown`/…) in log/exception/dead-letter strings with structured key=value emit. Net-new baseline. |
| `f52-files.txt` | 13 | 🔴 open | AST-scan flags `flag("<name>")` call sites referencing names not in `kairix/core/features/registry.py:REGISTRY`. Fix: register the flag or remove the dead call site. |
| `f55-files.txt` | 13 | 🔴 open | Declare `version: str` at module level in each `kairix/chunkers/<name>/__init__.py`; thread `chunker_version=` kwarg into every `Chunk(...)` constructor call. Vacuous-green at landing; Wave C threads through Silver, Wave F lands the plugins. |
| `unused-params-named-files.txt` | 10 | 🟠 partial | Was 5 at 2026-05-24 snapshot; peaked at 14, now 10 as new connector + provider plugin code landed without `_`-prefix discipline. Re-run Wave A mechanical batch — rename to `_param` per F19. |
| `f51-files.txt` | 14 | 🔴 open | Each `FeatureFlag` in `REGISTRY` needs `target_retire_in` ≤ current setuptools-scm version + 6 months, OR a `# retire-extension: <reason>` rationale comment. Stops flags becoming permanent scaffolding. |
| `f36-files.txt` | 15 | 🔴 open | Author `tests/bdd/features/connector_<name>.feature` / `extractor_<name>.feature` for each plugin AND wire it into the `tests/bdd/features/e2e_connector_sync.feature` Examples table (or `@<name>_no_<journey>` opt-out tag). |
| `f64-external-api-rate-limit-files.txt` | 16 | 🔴 open | Ship a rate-limit test (429 / Retry-After handling) for every plugin importing an HTTP client. Net-new baseline; one test per plugin. |
| `f71-preflight-truthfulness-files.txt` | 16 | 🔴 open | Author count-equals-ground-truth contract test for every preflight `_check_*` counting external state. Net-new baseline. |
| `per-file-coverage-floor-union-files.txt` | 6 | 🔴 open | Was 8 at 2026-05-24 snapshot; peaked at 16, now down to 6 as integration coverage landed for net-new connector + topology code. Lift each file ≥ 90% on the union of unit + integration. Several depend on a working `SearchPipeline` in test env — needs an in-process integration fixture. |
| `f54-files.txt` | 18 | 🔴 open | Each registry flag needs BDD scenarios for OFF and ON branches (`tests/bdd/features/feature_flag_<name>.feature` with ≥2 scenarios) plus integration tests exercising both branches; top-level capability flags also need an E2E composed-path test. |
| `f70-schema-writer-symmetry-files.txt` | 22 | 🔴 open | Every `CREATE TABLE` needs a matching `INSERT INTO` site OR `# table-is-derived:` rationale. Net-new baseline. |
| `f30-operator-outcome-tests-files.txt` | 23 | 🔴 open | Author CLI subprocess / MCP direct-handler outcome tests that assert on `.stdout`/`.stderr`/envelope content (not `returncode == 0` alone, not internal fake call-counts). Governed by F49 — must shrink ≥1 entry per release tag. |
| `f63-unbounded-fetchall-files.txt` | 24 | 🔴 open | Every `.fetchall()` must include `LIMIT <n>` OR carry a `# F63-bounded:` rationale comment. Net-new baseline. |
| `f44-files.txt` | 26 | 🔴 open | Engagement-scope code must not import firm-scope storage clients (`psycopg`, `psycopg2`, `asyncpg`, `pg8000`, `aiopg`). Refactor each call site through the reflection-extractor boundary into `kairix-firm/`. |
| `f72-integrity-invariants-files.txt` | 26 | 🔴 open | Author fixture-scale + soak-scale tests for every named cross-layer integrity invariant. Net-new baseline. |
| `f46-files.txt` | 29 | 🔴 open | Was 32 at 2026-05-24 snapshot — 3 paid down this cycle (F49 honoured). Refactor each BDD step file to route through `factory.build_*(paths=FakePaths(...))`. Canonical pattern: `tests/integration/test_vec_index_lifecycle.py`. Governed by F49 — must shrink ≥1 entry per release tag. |
| `f47-integration-factory-files.txt` | 32 | 🔴 open | Was 35 at 2026-05-24 snapshot — 3 paid down this cycle (F49 honoured). Convert each integration test to construct via `kairix.core.factory.build_*` with `paths=FakePaths(...)`. Governed by F49 — must shrink ≥1 entry per release tag. |
| `f69-scale-bound-tests-files.txt` | 32 | 🔴 open | Every integration test with `.fetchall()` / `list_changes()` needs a ≥10K-row variant. Net-new baseline; coordinate with F63 + F72 (overlapping touch surface). |
| `per-file-coverage-floor-files.txt` | 23 | 🔴 open | Was 12 at 2026-05-24 snapshot; peaked at 47, now down to 23 as unit coverage landed for net-new code. Lift each file ≥ 90% unit coverage. Several depend on lightweight `SearchPipeline` fixtures; coordinate with `per-file-coverage-floor-union-files.txt` paydown. |
| `test-only-kwargs-allow-files.txt` | 81 | 🔴 open | Was 71 at 2026-05-24 snapshot; grew by 10. Refactor production to take dependency as a default `Fake*` constructor argument (no `_fn=None` test-only kwarg in production); tests inject explicitly. |

**Empty-but-retained baselines** (25): kept as canonical paydown records per the F50 test that walks `.architecture/baseline/`. These remain at zero entries to document that the rule has a baseline mechanism even though no offenders currently exist —
`bdd-no-implementation-leaks-files.txt`,
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
`f62-stateful-multi-tick-files.txt`,
`f65-connector-metadata-files.txt`,
`go-dependency-rationale-files.txt`,
`go-logging-discipline-files.txt`,
`go-no-panic-outside-main-files.txt`,
`go-readme-coverage-files.txt`,
`go-version-flag-files.txt`,
`no-logging-secrets-files.txt`,
`no-real-names-in-fixtures-files.txt`,
`no-test-only-kwargs-files.txt`,
`path-naming-files.txt`,
`readme-coverage-files.txt`,
`shellcheck-disable-with-reason-files.txt`,
`suppressions-have-rationale-files.txt`.

## Sequencing recommendation

Dispatch in three waves of parallel subagents. Wave A's mechanical
batches (F16 / F17 / F20 / F32) are done as of KFEAT-016 (2026-05-23);
F19 is down to 5 stragglers. The composition and architectural waves
sit on top of the new Wave-A/B/C connector-framework + topology
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

1. Drop the shared `tc_fitness.gate()` baseline-reading branch (the
   `if baseline_file.exists()` block) — coordinated upstream in the
   `three-cubes-fitness` package, since the gate primitive is shared, not
   local (the old `_arch_lib.py` was retired in EPIC #499).
2. Delete the `.architecture/baseline/` directory entirely.
3. Update the F50 test to assert "no baseline mechanism remains" instead
   of "F30 baseline file exists".
4. Each rule is now strict-mode by construction: net-new and existing
   violations both block.

The single commit that does (1)–(3) is the structural deprecation. It
becomes safe to make only after every baseline reaches zero.
