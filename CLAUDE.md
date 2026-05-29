# CLAUDE.md — Engineering Standards for kairix

Shared knowledge layer for human-agent teams. See [README.md](README.md) for product context.

## How to commit

Use `bash scripts/safe-commit.sh "message"` for every commit. It runs lint, format, mypy, tests, security checks, and Sonar new-code parity. Loop on failures until green. See [CONSTRAINTS.md](CONSTRAINTS.md) for what blocks a commit.

**Local-first feedback loops.** Every blocking signal (lint, type, Sonar, coverage) must be reproducible locally in <60s. CI is the *confirmation* gate, not the *discovery* loop. When you hit a CI-flagged issue, query the full failing set ONCE (`python3 scripts/checks/check_sonar_new_code.py`), batch-fix locally, push once. See [`docs/architecture/local-first-feedback-loops.md`](docs/architecture/local-first-feedback-loops.md) for the Sonar-rule → local-fix recipe map.

## How to test

Test with fakes from `tests/fakes.py`, not monkey-patches. Construct pipelines through `kairix.core.factory.build_*`, not by direct `SearchPipeline(...)` / `EmbedPipeline(...)` construction.

Three principles, all mechanically enforced:

- **Composition (F46 / F47)** — BDD step impls and multi-component integration tests go through the factory with `paths=FakePaths(...)` and any other injection seams. Direct pipeline construction is reserved for `tests/contracts/` (Protocol shape proofs) and `tests/integration/test_<x>_contract.py` (single-layer boundary proofs).
- **Real path (F48)** — `tests/e2e/test_composed_production_path.py` exists, carries `@pytest.mark.e2e`, runs in CI Stage 4.5 under `pytest -m e2e`, and exercises config → factory.build → ingest → query → assertion against composed production code. Every new top-level capability gets a sibling `tests/e2e/test_composed_<capability>_path.py` in the same wave.
- **New capability (F45)** — shipping a new CLI subcommand, MCP tool, provider plugin, connector plugin, or extractor plugin requires a `tests/bdd/features/*.feature` AND an outcome test in the same commit. Pre-commit blocks otherwise.

CLI outcome tests use `subprocess.run([sys.executable, "-m", "kairix.cli", "<sub>", ..., "--document-root", str(tmp_path)])` — no `KAIRIX_*` env vars in the subprocess invocation. MCP tools test by direct handler call with `deps=...` injected. See `tests/contracts/test_protocols.py` for protocol compliance patterns; `tests/integration/test_vec_index_lifecycle.py` for canonical factory shape; `tests/e2e/test_composed_production_path.py` for the E2E exemplar; `docs/architecture/test-discipline-hardening.md` for the full specification.

## Architecture

Protocols define boundaries. Pipelines compose protocols. Factories build production pipelines. Repositories own data access. Strategies replace if/elif branches. See [docs/architecture/ENGINEERING.md](docs/architecture/ENGINEERING.md) for detail.

Key files:
- `kairix/core/protocols.py` — all domain boundary protocols
- `kairix/core/factory.py` — production pipeline construction
- `kairix/core/search/pipeline.py` — SearchPipeline orchestrator
- `tests/fakes.py` — fake implementations for testing

## Cutover patterns

Every change that swaps production behaviour goes through a feature flag. The pattern is mandatory for connector swaps, ranker swaps, schema migrations, ingest-pipeline changes, and any cutover that's reversible-until-validated.

See [`docs/architecture/feature-flag-architecture.md`](docs/architecture/feature-flag-architecture.md) for the canonical spec. Three principles:

- **Default-safe (§2.1)** — every flag defaults to the validated behaviour. Merging flag-gated code is structurally a no-op for operators; the cutover is a separate deliberate action.
- **Both-branch tested (F54)** — every flag has BDD scenarios for OFF and ON, integration tests exercising both branches, and (for top-level capability flags) an E2E composed-path test. F54 enforces this mechanically.
- **Mechanical retirement (F51)** — every flag has a `target_retire_in` version; F51 fires past that deadline unless explicitly extended with rationale. Stops "flag becomes permanent fixture".

Cutover protocol per flag flip: capture pre-flip baseline (state digest + eval scores + probe latency + sample-journey results) → flip the flag → soak (24h min) → capture post-flip same set → diff and gate on hard thresholds (state delta within ±2%, eval within ±2pp, latency within ±20%, sample-journey ≥80% parity) → promote stage or rollback.

Cutover tooling: `scripts/cutover/capture_baseline.py` + `scripts/cutover/diff_baseline.py`. Operator surface: `kairix features status` (CLI) + `tool_features_status` (MCP) — both required by F53.

## How to delegate work

Ralph pattern: fine-grained file-scoped work, parallel agents with embedded backpressure loops, `safe-commit.sh` in each loop. 10-15 loops/hour target. See [engineering hub](https://github.com/three-cubes/engineering-hub/tree/main/ralph).

**Default for batches (≥2 independent file-scoped tasks): parallel worktrees + cherry-pick.** Dispatch each agent with `isolation="worktree"`, all in parallel. Each agent commits to its own branch and reports SHA + path. From the main checkout, `git cherry-pick <sha>` each agent's commit. Resolve `tests/conftest.py` and `tests/fakes.py` conflicts by combining both sides, then push and clean up the worktree. (Repo is trunk-based on `main` — worktrees and the primary checkout share the same base, so the historical develop/main mismatch is gone.)

**Default for single tasks: sequential on the main checkout, no isolation.** One agent at a time, commits and pushes direct to main.

Every agent runs `safe-commit.sh` in its loop and only commits (and pushes, in non-worktree mode) when green.

**Worktree isolation hygiene (#208, upstream anthropics/claude-code#59019).** Subagents dispatched with `isolation="worktree"` MUST stay inside their assigned worktree for all file writes. Do NOT `cd` to the primary checkout or to another worktree. Symptom of failed isolation: untracked files appear in the primary checkout that mirror paths the subagent claims to have written in its own worktree. Orchestrator-side defense: before each `git cherry-pick <subagent-sha>`, run `python3 scripts/checks/check_worktree_isolation.py` (use `--clean` to delete shadow copies in the primary). The subagent's commit is the canonical source; the primary's untracked copy is the stale shadow.

**Primary-agent review gate before every cherry-pick.** Mechanical gates (`safe-commit.sh`, pre-commit, CI) catch *correctness*. The primary agent is the gate for *intent* — that the subagent's diff matches the dispatch brief and the project's invariants. Before `git cherry-pick <subagent-sha>`, read the diff and apply this checklist, then document the pass in the cherry-pick body or post a short rationale on the PR:

- ☐ **Scope** — diff matches the dispatched task; no scope creep (renames, refactors, doc edits the brief didn't authorise)
- ☐ **Sabotage** — every new `test_*` has a sabotage-proof noted in the agent's report (mutate prod → confirm fail → restore); spot-check one
- ☐ **Baselines** — no F-rule baseline grew unless the commit body explicitly explains why
- ☐ **Worktree** — `python3 scripts/checks/check_worktree_isolation.py` reports clean (no shadow copies in primary)
- ☐ **Affordance** — any new pipeline-blocking message follows the "X found. Refactor to YYY to pass." template with Pass + Forbidden examples (F15 is the reference)

Failing any check: send the subagent back with a `SendMessage` correction or reject and re-dispatch with tighter brief. Don't paper over with manual edits at cherry-pick time.

**Human gate on releases.** Per `feedback_release_hitl` memory: don't cut release tags, deploy to shared infra, or run release workflows without explicit per-action authorisation. Routine commits go direct to `main` (trunk-based); release PRs are no longer the standard ritual since develop is gone — release notes now flow through the CHANGELOG entry that `release.yml` reads into the GitHub Release body. If a release-stabilisation PR is ever opened, draft the body locally and wait for green-light before `gh pr create`.

## Languages

**Python is the default.** All retrieval, agents, eval, MCP, and domain logic stays in Python. Hot paths are already native (SQLite FTS5, usearch, sentence-transformers, neo4j C driver, spaCy) — Python is the glue, which is exactly what Python is good at.

**Go is allowed only for operational binaries** that run outside the Python venv — webhook handlers, deploy wrappers, log shippers, health probes. Single-static-binary deploys with no `pip install` on the host. The default answer to "should this be Go?" is no. See [`docs/architecture/go-integration-plan.md`](docs/architecture/go-integration-plan.md) for the four-criterion decision matrix and the G1–G10 Go-side fitness functions.

**Repo layout**: Go binaries live at `services/<name>/cmd/<name>/main.go` with a per-service `go.mod`. CI workflow `Go quality` auto-discovers any `services/*/go.mod` and runs `gofmt -s`, `go vet`, `golangci-lint`, `go test -race -cover`, and cross-compile to linux/amd64+arm64 / darwin/amd64+arm64. The Python `1 · Quality gate` is untouched and independent.

**No Rust, no PyO3, no TypeScript** in scope. Adding a third language requires its own plan-of-record.

## Naming

- Code: `snake_case` functions, `PascalCase` classes, `UPPER_SNAKE_CASE` constants (Python); `gofmt -s` decides for Go.
- User-facing: grade 8 reading level, "knowledge store" not "vault"
- Test agents: generic names (agent-alpha, agent-beta)

## Soak tier (`@pytest.mark.soak`)

Production-scale soak tests live under `tests/soak/` (and Bundle E's `tests/integrity_invariants/*_soak` variants) and carry `pytestmark = pytest.mark.soak`. They seed N >= 10**4 rows through the canonical fakes + `kairix.core.factory.build_*`, then assert concrete observable outcomes (row counts, wall-clock budgets, monotonicity) at production scale. Excluded from Stage 2/3 per-commit CI; the [`soak-suite.yml`](.github/workflows/soak-suite.yml) workflow runs `pytest -m soak` nightly on `main` and on-demand via `gh workflow run soak-suite.yml`. Wall-clock target 20-60 min; this workflow is NOT a branch-protection check and does NOT block PR merge. See [ADR-024 §"Soak tier (new)"](docs/architecture/ADR-024-test-pyramid-redesign.md) for the canonical spec and the three seed soak tests (`bronze_coverage_parity_at_scale`, `vector_index_drift_at_scale`, `drain_progress_at_10k`).

## Architecture fitness functions

Mechanical, blocking checks encode rejected patterns into automation. F-numbers are permanent shipping IDs (never renumbered, never reused); the catalogue at [`scripts/checks/_rule_catalogue.py`](scripts/checks/_rule_catalogue.py) holds full metadata (category, scope, ADR origin, status) and is the canonical query surface. The groupings below match the catalogue's category dimension.

**Layering** — boundaries between architectural slices.
- **F26** `kairix/core/**` may not import `kairix/providers/**` or `kairix/transport/**` (Protocols only). **F27** `kairix/providers/<a>/` may not import another provider. **F34** mirrors F26 for connectors: `kairix/core/connectors/**` may not import `kairix/connectors/**` or `kairix/extractors/**`. **F35** mirrors F27 for connectors. **F44** engagement-scope code may not import firm-scope storage clients (`psycopg`, `asyncpg`, …).
- **F37** change-detection / sync code only under `kairix/connectors/<name>/` or `kairix/core/connectors/`. **F38** Silver processing only in `kairix/core/connectors/silver.py`. **F61** bare `_SqliteChunkWriter(...)` construction only under `kairix/core/connectors/` (everywhere else flows through `CollectionRouter`).

**Test discipline** — what tests must look like.
- **F1** no `@patch`/`monkeypatch` on kairix internals — inject Fake* through a constructor seam. **F2** no `monkeypatch.setenv("KAIRIX_*")`. **F5** no internal-name imports in tests. **F6** no `*_fn=None` test-only kwargs in production. **F8** every `test_*` carries a category marker (`unit`/`bdd`/`contract`/`integration`/`e2e`/`slow`/`soak`/`invariant`). **F11** test skip mechanisms require rationale.
- **F12** every BDD feature has a happy-path scenario. **F13** BDD scenarios reject implementation symbols. **F30** every CLI subcommand + every MCP tool has an outcome test asserting on stdout/stderr/envelope (not on returncode alone). **F45** every new CLI/MCP/provider/connector/extractor adds a matching BDD feature in the same commit. **F46** BDD step impls compose via CLI/MCP/factory — no direct `*Pipeline(...)` construction. **F47** integration tests construct via `kairix.core.factory.build_*` with `paths=FakePaths(...)`. **F48** `tests/e2e/test_composed_*_path.py` exists and runs in CI Stage 4.5.
- **F62** every stateful tick/run_batch component has a multi-tick advance/idempotency test. **F68** every Protocol method has a failure-injection contract test (one of `raises`/`times_out`/`returns_partial`/`returns_empty`/`unauthorized`/`unavailable`). **F69** every integration test with `.fetchall()` / `list_changes()` has a ≥10K-row variant. **F72** every named cross-layer integrity invariant has fixture-scale + soak-scale tests.

**Plugin contract** — what plugins must surface to be shippable.
- **F28** every provider plugin has matching BDD feature + Examples-table row. **F36** mirrors F28 for connectors + extractors. **F40** every `Extractor` plugin declares module-level `version: str` + `make_extractor` factory. **F41** every plugin tree has `py.typed` + no unjustified `# type: ignore`. **F42** Protocol methods return frozen-dc/tuple — never `dict[str, Any]` or bare `Any`. **F43** every plugin has `tests/contracts/test_<name>_protocol.py` exercising real + fake. **F55** every Chunker plugin declares `version` + every `Chunk(...)` call passes `chunker_version=`. **F56** every connector declares `SourceConnector` + at least one of `{Poll, Checkpointed, Event}Connector`. **F64** every plugin importing an HTTP client ships a rate-limit test (429 / Retry-After). **F65** every connector implements `metadata_for` + ships a propagation test for `chunk_date` + `author`.

**Production safety** — what production code must avoid.
- **F15** no logging of secret-named variables in plaintext outside `kairix/{secrets,credentials}.py`. **F39** every `Chunk(...)` constructor passes `source_uri` + `source_modified_at` + `sensitivity` explicitly. **F50** net-new files may not appear in any per-file baseline. **F63** every `.fetchall()` includes `LIMIT` or carries `# F63-bounded:`. **F66** every connector + tick-driven component declares `per_tick_max_items` + `disk_watermark_min_free_bytes`. **F73** token-pattern scanner for private infra identifiers (externalised pattern source).

**Schema integrity** — DB shape + drain symmetry.
- **F57** every `UPDATE topology_cc_pairs SET status=?` lives next to a `_ALLOWED_TRANSITIONS` dispatch dict. **F58** `HierarchyConnector` impls have a parent-before-child contract test. **F67** every `pushed_to_<sink>` column has a matching UPDATE site flipping 0 → 1. **F70** every `CREATE TABLE` has an `INSERT INTO` site OR `# table-is-derived:` rationale. **F71** every preflight `_check_*` counting external state has a count-equals-ground-truth contract test.

**Feature flag** — flag lifecycle.
- **F51** every `FeatureFlag` has `target_retire_in` ≤ current SCM version + 6 months. **F52** every `flag("<name>")` call site references a name in `REGISTRY`. **F53** `kairix features status` CLI + `tool_features_status` MCP both exist. **F54** every flag has OFF + ON BDD scenarios, integration tests, and (for top-level) an E2E composed-path test.

**Agent affordance** — humans + agents reading errors.
- **F3** every per-line suppression (`# noqa`/`# NOSONAR`/`# pragma`/`# type: ignore`/`# nosec`) has rationale. **F10** CI workflow silencers (`continue-on-error: true`) require rationale. **F14** every `sonar.issue.ignore` entry has a preceding rationale. **F16** cognitive complexity ≤ 15 per function (Sonar S3776). **F17** no string literal ≥10 chars duplicated ≥3 times (S1192). **F18** no commented-out code (S125). **F19** unused parameters must be `_`-prefixed (S1172). **F20** empty function bodies require docstring or `# Intentionally empty —` comment (S1186). **F21** every `check_*.{py,sh}` failure-output carries `fix:`/`next:`/`run:` action markers. **F23** every top-level directory has a `README.md` resolver.

**Repo hygiene** — paths, imports, naming.
- **F4** no `os.environ.get("KAIRIX_*")` outside `paths.py`/`secrets.py`. **F22** repo paths follow per-tree naming conventions. **F24** no `from tests.*` imports inside `kairix/**/*.py`. **F29** performance-measurement code only under `kairix/quality/probe/**`. **F31** no hardcoded `/Users/` or `/home/<dev>/` paths. **F32** no real names in test fixtures (use `agent-alpha` etc. + reference library). **F33** shellcheck disable directives require rationale.

**Coverage** — release-gate paydown.
- **F7** per-file coverage ≥ 90% (unit, Stage 2). **F9** per-file coverage ≥ 90% on union of unit + integration (Stage 5). **F49** each release tag reduces `f30`/`f46`/`f47` baselines by ≥1 (or keeps at zero).

**Observability** — ADR-026 cross-cutting primitives (rolling in).
- **F74** (proposed) every `Stage` subclass is only invoked via a `StageRunner` — never direct `.process()` call. **F75** (proposed) every CLI subcommand + MCP tool + connector appears in at least one eval-suite question. **F76** (proposed) no f-string interpolation of content-like vars (`raw`/`body`/`payload`/`markdown`/…) in log/exception/dead-letter strings. **F77** (proposed) `sqlite3.connect` call sites outside the allow-list (worker/factory/scripts/tests) are flagged. **F78/F79/F80** (proposed, deferred — need runtime instrumentation): memory bounds, migration reversibility, cross-scope runtime data-flow.

**Go side** (active when `services/<name>/go.mod` exists; see [`docs/architecture/go-integration-plan.md`](docs/architecture/go-integration-plan.md)).
- **G1** every Go binary exposes `--version`. **G2** errors wrap with `%w`. **G3** no `interface{}`/`any` in exported signatures. **G4** `context.Context` as first arg on exported I/O. **G5** every package has a doc comment. **G6** no `panic` outside `main`/`init`. **G7** Go testing conventions only. **G8** logging via `log/slog`. **G9** every `services/<name>/` has a `README.md`. **G10** dependency-rationale registry per `services/<name>/DEPENDENCIES.md`.

Pre-existing violations are grandfathered in `.architecture/baseline/`; net-new violations block at pre-commit, `safe-commit.sh`, and CI Stage 0 (or Stage 5 for F9). Full detail per rule: [`scripts/checks/_rule_catalogue.py`](scripts/checks/_rule_catalogue.py) (catalogue) + [`docs/architecture/fitness-functions.md`](docs/architecture/fitness-functions.md) (canonical reference). Read these before adding any silencer, skip, suppression, internal import, or BDD scenario — the gate rejects lazy bypasses.

## CI

Stages: arch-fitness (Stage 0, F1-F6+F8+F14) → pre-commit → contracts → unit+bdd+contract+mypy (Stage 2, includes F7 per-file 90% floor) → integration → security (incl. SonarCloud) → Docker. All must pass before merge.

Codecov surfaces:
- **Coverage**: `unit` flag (Stage 2) and `integration` flag (Stage 3) upload via `codecov/codecov-action@v5`. `codecov.yml` carryforwards both flags so the dashboard merges correctly when only one stage runs. Patch target = 85% (matches F7).
- **Test analytics**: JUnit XMLs from contracts / unit / integration upload via `codecov/test-results-action@v1` for flaky-test and slow-test tracking.
- **Bundles**: not applicable (Python-only project).

## Docs — agent-actionable resolver

Find the canonical doc for the task you're doing. Each row reads
"to do X → read Y / run Z". When multiple docs apply, the **bold** one
is the source-of-truth; the others fill in detail.

### 1. Project vision + roadmap

| To do this | Read |
|---|---|
| Understand why kairix exists + who it's for | **[`README.md`](README.md)** — pain → outcome framing for human-agent teams |
| See what's shipped + what's next | **[`docs/project/ROADMAP.md`](docs/project/ROADMAP.md)** — current state, near-term direction, capability matrix |
| Inspect a specific release's behaviour changes | [`CHANGELOG.md`](CHANGELOG.md) — per-version entry; pairs with [`docs/upgrades/`](docs/upgrades/) for upgrade steps |
| Trace a discussion / decision back to context | [`GitHub Discussions → Roadmap`](https://github.com/three-cubes/kairix/discussions) — priorities, RFCs, feature direction |

### 2. Architecture

| To do this | Read |
|---|---|
| Understand the layered architecture (Protocols / Pipelines / Factories / Repositories) | **[`docs/architecture/ENGINEERING.md`](docs/architecture/ENGINEERING.md)** — patterns, factory composition, repository pattern |
| Understand the deployment topology (Docker compose, VM, MCP transport) | [`docs/architecture/ADR-017-deployment-architecture.md`](docs/architecture/ADR-017-deployment-architecture.md) |
| Understand the provider plug-in surface (Azure Foundry, OpenAI, Bedrock, …) | [`docs/architecture/provider-plugin-architecture.md`](docs/architecture/provider-plugin-architecture.md) — three-layer split locked by F26/F27/F28 |
| Understand the connector / source ingestion framework (Obsidian, Dex, M365, planned SharePoint / Notion / Teams / Slack / GitHub / Drive) | [`docs/architecture/connector-ingestion-architecture.md`](docs/architecture/connector-ingestion-architecture.md) — Wave 0-5 framework, locked by F34-F44 |
| Plan connector / collection / scope topology evolution (multi-instance connectors, cross-source collections, per-actor scope profiles, skill-driven retrieval) | **[`docs/architecture/connector-scope-topology/ADR.md`](docs/architecture/connector-scope-topology/ADR.md)** — proposed 5-layer model; `00-overview.md` for nav, `01-05` for source analysis / use cases / BDD / simulation / non-functionals |
| Add guided configuration for a connector (discover available sites/drives/channels/repos → pick from list → emit YAML → progress reporting during ingest) | [`docs/architecture/guided-configuration.md`](docs/architecture/guided-configuration.md) — KFEAT-022; SharePoint pilot deep-dive + the generalised pattern for Slack / GitHub / Notion |
| Understand the fact layer / conversational recall surface | [`docs/architecture/fact-layer.md`](docs/architecture/fact-layer.md) — ADR + Capability #1–#5 from v2026.5.18 |
| Understand the CLI ↔ MCP feature-parity contract | [`docs/architecture/cli-mcp-feature-parity.md`](docs/architecture/cli-mcp-feature-parity.md) |
| Decide whether new operational code should be Go or Python | [`docs/architecture/go-integration-plan.md`](docs/architecture/go-integration-plan.md) — four-criterion matrix + G1–G10 Go fitness functions |

### 3. Engineering practices

| To do this | Read / run |
|---|---|
| Write a test the right way (Protocol fakes, no monkey-patches) | **[`docs/architecture/ENGINEERING.md#testing`](docs/architecture/ENGINEERING.md)** + [`tests/fakes.py`](tests/fakes.py) + [`tests/contracts/test_protocols.py`](tests/contracts/test_protocols.py) |
| Run the same gates CI runs, locally | `bash scripts/safe-commit.sh "<message>"` — lint, format, mypy, pytest+coverage, arch-fitness, secrets, confidential-pattern, sonar new-code parity |
| Reproduce a CI-flagged Sonar / lint / type / coverage issue locally in one shot | **[`docs/architecture/local-first-feedback-loops.md`](docs/architecture/local-first-feedback-loops.md)** — Sonar-rule → local-fix recipe map; `python3 scripts/checks/check_sonar_new_code.py` pulls the full failing set so you batch-fix once instead of push-per-fix |
| Pay down a grandfathered baseline entry (resolve a `.architecture/baseline/<rule>-files.txt` line) | **[`docs/architecture/grandfathering-paydown.md`](docs/architecture/grandfathering-paydown.md)** — three resolution shapes (refactor / rule-exempt with rationale / structural change), per-baseline status + next-move, and the deprecation endgame |
| Onboard as a new contributor | [`CONTRIBUTING.md`](CONTRIBUTING.md) + [`docs/getting-started/quick-start.md`](docs/getting-started/quick-start.md) |
| Understand evaluation methodology + benchmark suites | [`docs/evaluation/EVALUATION.md`](docs/evaluation/EVALUATION.md) |
| Run a benchmark / interpret scores | [`docs/operations/runbooks/how-to-run-benchmark.md`](docs/operations/runbooks/how-to-run-benchmark.md) |

### 4. Guardrails + preferred patterns

| To do this | Read |
|---|---|
| See what blocks a commit (the mechanical contract) | **[`CONSTRAINTS.md`](CONSTRAINTS.md)** — short list of hard blocks |
| Understand the architecture fitness functions F1–F54 + G1–G10 | **[`docs/architecture/fitness-functions.md`](docs/architecture/fitness-functions.md)** — canonical reference; read before adding any silencer, skip, suppression, or internal import |
| Land a new top-level capability with its discipline carrying | **[`docs/architecture/test-discipline-hardening.md`](docs/architecture/test-discipline-hardening.md)** — F45..F49, the three principles (composition / real-path / new-capability), canonical test shapes |
| Cut over from old behaviour to new without breaking operators (connector swap, ranker swap, schema migration, etc.) | **[`docs/architecture/feature-flag-architecture.md`](docs/architecture/feature-flag-architecture.md)** — F51..F54, default-safe / both-branch-tested / mechanical-retirement principles, capture-flip-soak-gate cutover protocol |
| Avoid known code-smell patterns | [`docs/architecture/ENGINEERING.md#code-smells`](docs/architecture/ENGINEERING.md) — inappropriate intimacy, feature envy, test-shaped APIs |
| Understand security posture | [`SECURITY.md`](SECURITY.md) + F15 (no logging of secret-named variables in plaintext) |

### 5. Deployment + release approach & automation

| To do this | Read / run |
|---|---|
| Understand the operational deploy model (Docker compose, healthchecks, secrets-from-KV) | **[`docs/operations/OPERATIONS.md`](docs/operations/OPERATIONS.md)** |
| Deploy the MCP server (HTTP transport, cold-start, readiness gate) | [`docs/operations/MCP-DEPLOYMENT.md`](docs/operations/MCP-DEPLOYMENT.md) |
| Cut an alpha release | `gh workflow run release-alpha.yml -f date_version=YYYY.M.D -f alpha_n=N` — see [`docs/operations/runbooks/how-to-upgrade-kairix.md`](docs/operations/runbooks/how-to-upgrade-kairix.md) |
| Cut a stable release | `gh workflow run release.yml --ref main -f version=vYYYY.M.D -f changelog_label=YYYY.M.D` — workflow tags `main`, pulls `CHANGELOG.md` section into the GitHub Release body |
| Browse all runbooks (entity audit, embedding lag, ranking debug, regression…) | [`docs/operations/runbooks/INDEX.md`](docs/operations/runbooks/INDEX.md) |
| Read per-release upgrade notes (operator-facing) | [`docs/upgrades/`](docs/upgrades/) — one file per release; latest is the highest `v2026.M.D.md` |
| Migrate config overlay (pre-upgrade prereq for shared-mount deploys) | [`docs/operations/runbooks/config-overlay-upgrade.md`](docs/operations/runbooks/config-overlay-upgrade.md) |
| Trace a kairix incident (entity graph corruption, recall regression, embedding stall) | [`docs/runbooks/`](docs/runbooks/) (kairix-side) + [`docs/operations/runbooks/`](docs/operations/runbooks/) (operator-side) |
