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

## State (as of 2026-05-23, post-`95a2e971`)

| Baseline | Count | Status | Next move |
|---|---:|---|---|
| `f26-files.txt` | 0 | ✅ resolved | `kairix/core/factory.py` exempted in rule as composition root |
| `shellcheck-disable-with-reason-files.txt` | 0 | ✅ resolved | `# rationale:` comments added to SC1090 disables |
| `f41-files.txt` | 0 | ✅ resolved | `py.typed` added to all 7 provider packages |
| `empty-body-intent-files.txt` | 9 | 🟡 in-flight | Subagent (general-purpose) — adds docstrings to Protocol empty bodies |
| `no-real-names-in-fixtures-files.txt` | 11 | 🟡 in-flight | Subagent — rewrites test fixtures + exempts vendored `reference-library/**` |
| `no-env-monkeypatch-files.txt` | 8 | 🟠 partial | 3 boundary tests (`test_paths`, `test_secrets`, `test_credentials`) → rule exemption; 5 config-loader tests need real refactor (production accepts `env` dict instead of reading `os.environ` directly) |
| `f46-files.txt` | 6 | 🔴 open | Refactor each BDD step file to route through `factory.build_*(paths=FakePaths(...))`. Canonical pattern: `tests/integration/test_vec_index_lifecycle.py` |
| `f43-files.txt` | 8 | 🔴 open | Author `tests/contracts/test_<name>_protocol.py` for each plugin — imports canonical fake AND real impl, asserts same Protocol contract on both |
| `f47-integration-factory-files.txt` | 11 | 🔴 open | Convert each integration test to construct via `kairix.core.factory.build_*` with `paths=FakePaths(...)` |
| `no-internal-patches-files.txt` | 9 | 🔴 open | Replace `@patch("kairix.X.Y")` with constructor injection of a `Fake*` from `tests/fakes.py` |
| `test-only-kwargs-allow-files.txt` | 11 | 🔴 open | Refactor production to take dependency as a default `Fake*` constructor argument (no `_fn=None` test-only kwarg in production); tests inject explicitly |
| `no-internal-test-imports-files.txt` | 18 | 🔴 open | Tests import via public surface (`kairix.<module>.public_name`) instead of attribute access on private (`_`-prefixed) names |
| `unused-params-named-files.txt` | 33 | 🔴 open | Rename each unused parameter to `_param` (mechanical, batchable for subagent) |
| `cognitive-complexity-files.txt` | 34 | 🔴 open | Extract helpers; sabotage-prove each. Canonical example: `_mark_existing_vec_hit` in `kairix/core/search/rrf.py` (replaced a 3-deep nested for/if/if). |
| `no-duplicate-string-files.txt` | 38 | 🔴 open | Extract `≥10 char` literals duplicated `≥3 times` to module-level UPPER_SNAKE constants. Canonical example: `_CONNECTOR_FRAMEWORK_OWNER` in `kairix/core/features/registry.py` |
| `per-file-coverage-floor-union-files.txt` | 4 | 🔴 open | Add unit/integration coverage to lift each file ≥ 90% on the union of unit + integration. Two files depend on a working `SearchPipeline` in test env (`cold_start.py`) — needs an in-process integration fixture. |
| `per-file-coverage-floor-files.txt` | 1 | 🔴 open | `cold_start.py` warm_retrieval_stack — needs lightweight SearchPipeline fixture; existing test harness can't compose one in unit context. |

**Empty-but-retained baselines** (32): kept as canonical paydown records per the F50 test that walks `.architecture/baseline/`. These remain at zero entries to document that the rule has a baseline mechanism even though no offenders currently exist.

## Sequencing recommendation

Dispatch in three waves of parallel subagents:

**Wave A — mechanical batches (low risk, high volume):**
- F19 `unused-params-named` (33) — rename to `_param`
- F17 `no-duplicate-string` (38) — extract constants
- F20 `empty-body-intent` (9) — in-flight
- F32 `no-real-names-in-fixtures` (11) — in-flight

**Wave B — composition-pattern refactors (after Wave A):**
- F46 `bdd-step-composition` (6) — route through factory
- F47 `integration-factory` (11) — same pattern, integration tests
- F1 `no-internal-patches` (9) — replace @patch with Fake injection
- F5 `no-internal-test-imports` (18) — public-surface imports
- F6 `test-only-kwargs-allow` (11) — default-arg Fake* constructor

**Wave C — architectural changes (sequential, sized):**
- F16 `cognitive-complexity` (34) — extract helpers per function, sabotage-prove each
- F43 `plugin-contract-tests` (8) — author per-plugin contract tests
- F2 `no-env-monkeypatch` (8) — refactor production to take `env` dict (3 boundary tests then exempted in rule)
- F7/F9 coverage floors (5) — author lightweight integration fixtures

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
