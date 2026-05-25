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

## Architecture fitness functions

Mechanical, blocking checks encode rejected patterns into automation:

- **F1** no internal-substitution patching of kairix code — flags six shapes: `@patch("kairix.X")`, `with patch("kairix.X")`, `kairix.X.Y = expr`, `alias.Y = expr` (where alias resolves to a kairix module), `monkeypatch.setattr("kairix.X.Y", ...)`, `monkeypatch.setattr(<kairix module ref>, ...)`. Stdlib and external SDKs (`os.*`, `httpx.*`, `openai.*`, `boto3.*`) remain allowed. To pass: inject a Fake* from `tests/fakes.py` through a constructor seam — **F2** no `monkeypatch.setenv("KAIRIX_*")` — **F3** every per-line suppression (`# noqa` / `# NOSONAR` / `# pragma: no cover` / `# type: ignore` / `# nosec`) has rationale — **F4** no `os.environ.get("KAIRIX_*")` outside `paths.py`/`secrets.py`.
- **F5** no internal-name imports in tests — **F6** no `*_fn=None` test-only kwargs in production.
- **F7** per-file coverage ≥ 90% (unit) — **F9** per-file coverage ≥ 90% on the unit ∪ integration union (Stage 5).
- **F8** every `test_*` carries a category marker (`unit`/`bdd`/`contract`/`integration`/`e2e`/`slow`).
- **F10** CI workflow silencers (`continue-on-error: true`, `fail_ci_if_error: false`) require rationale — **F11** test skip mechanisms (`pytest.mark.skip`/`skipif`/`xfail`/`importorskip`) require rationale.
- **F12** every BDD feature has a happy-path scenario — **F13** BDD scenarios reject implementation symbols (`Mock`, `kairix.<pkg>.<symbol>`).
- **F14** every `sonar.issue.ignore.multicriteria.*.ruleKey` in `sonar-project.properties` has a preceding rationale comment.
- **F15** no logging of secret-named variables in plaintext — `logger.*`, `print`, `sys.std{out,err}.write`, `raise X(...)` calls must not pass any `*_api_key`/`*_token`/`*_secret`/`*_password`/`*_credential`/`bearer`/`jwt`/`*_private_key` argument (or f-string interpolation thereof) outside the `kairix/{secrets,credentials}.py` boundary modules.
- **F16** cognitive complexity ≤ 15 per function (Sonar S3776) — extract helpers / early-return / dispatch-dict to flatten — **F17** no string literal of ≥10 chars duplicated ≥3 times in a module (S1192) — **F18** no commented-out code (S125) — **F19** unused function parameters must be `_`-prefixed (S1172) — **F20** empty function bodies require a docstring or `# Intentionally empty —` comment (S1186).
- **F21** every `scripts/checks/check_*.{py,sh}` failure-output string carries at least one of the lowercase action markers `fix:`, `next:`, or `run:` — so the agent reading a gate failure gets the correction action, not just the diagnosis (#258 convergence with sibling-repo fitness functions).
- **F22** repo paths follow per-tree naming conventions — `kairix/**/*.py` snake_case, `tests/**/test_*.py`, `tests/bdd/features/*.feature` snake_case, `scripts/checks/check_*.{py,sh}`, `docs/**/runbooks/*.md` kebab-case, `.architecture/baseline/<rule>-files.txt` (#258).
- **F23** every top-level directory has a `README.md` resolver — landing on `docs/`, `tests/`, `kairix/`, etc. via a path mention must hit a one-screen orientation, not a bare directory listing (#258).
- **F24** no `from tests.*` / `import tests` imports inside `kairix/**/*.py` — `tests/` isn't shipped in the published wheel, so any production import of `tests.<x>` works locally but `ModuleNotFoundError`s the moment an end user `pip install`s kairix (#266; codifies the v2026.5.15.1 → v2026.5.15.2 incident).
- **F26** `kairix/core/**` may not import `kairix/providers/**` or `kairix/transport/**` — domain code talks to those layers through Protocols only (`kairix.core.protocols.*`). Locks the three-layer split from `docs/architecture/provider-plugin-architecture.md`.
- **F27** `kairix/providers/<a>/**` may not import another provider — plugins must stay independently shippable. Cross-provider concerns go through `kairix/transport/`.
- **F28** every plugin under `kairix/providers/<name>/` has a matching `tests/bdd/features/provider_<name>.feature` AND appears as an Examples-table row in every `tests/bdd/features/e2e_provider_*.feature` (or carries the `@<name>_no_<journey>` opt-out tag). Stops new providers shipping without behaviour tests.
- **F29** performance-measurement code (`bench*.py`, `microbench*.py`, `*_latency*.py`, `*_perf*.py`) may only land under `kairix/quality/probe/**` — the single perf surface for PVT and end-user `kairix probe-config`. Stops transport/ and providers/ growing parallel benchmark harnesses.
- **F30** every subcommand in `kairix/cli.py:COMMANDS` AND every `@server.tool()` in `kairix/agents/mcp/server.py` has at least one outcome test that (a) invokes via `subprocess.run([sys.executable, "-m", "kairix.cli", "<sub>", ...])` or calls the MCP tool handler directly, and (b) asserts on `.stdout`/`.stderr`/returned-envelope content (not on `returncode == 0` alone, not on internal fake call-counts). Baseline paid down to **zero** in Wave 0 (2026-05-22, kairix-pro-platform#59). Motivation: Plan B-parity shipped 5233 green tests but the LoCoMo benchmark fell to 5% because no test exercised the composed production path against a real ingested fact.
- **F34** `kairix/core/connectors/**` may not import `kairix/connectors/**` or `kairix/extractors/**` — domain code talks to those layers through Protocols only (`kairix.core.protocols.*`). Mirrors F26 for the connector framework. Locks the three-layer split from `docs/architecture/connector-ingestion-architecture.md`. Pre-arm — vacuous-green at landing until Wave 1 creates the trees.
- **F35** `kairix/connectors/<a>/**` may not import another connector or any extractor — plugins stay independently shippable; cross-plugin work goes through `kairix/core/connectors/`. Mirrors F27.
- **F36** every plugin under `kairix/connectors/<name>/` and `kairix/extractors/<name>/` has a matching `tests/bdd/features/connector_<name>.feature` / `extractor_<name>.feature` AND appears as an Examples-table row in `tests/bdd/features/e2e_connector_sync.feature` (or carries an `@<name>_no_<journey>` opt-out tag). Mirrors F28.
- **F37** change-detection / sync code (anything importing `watchdog`, `msgraph`/`msgraph_core`, `notion_client`, `slack_sdk.{rtm,socket_mode}`, `dulwich`) may only land under `kairix/connectors/<name>/` or `kairix/core/connectors/`. Mirrors F29 — singular sync surface.
- **F38** Silver processing (chunking + entity-signal extraction) may only live in `kairix/core/connectors/silver.py`. No per-connector chunkers. Stops the connector-Protocol-mandates-chunking failure mode that breaks F35.
- **F39** every chunk write (`Chunk(...)` constructor call) must pass `source_uri`, `source_modified_at`, AND `sensitivity` explicitly. Default-to-public is only valid when the connector config declares it. Boundary enforcement at the write surface; mirrors F15.
- **F40** every `Extractor` plugin under `kairix/extractors/<name>/__init__.py` declares a module-level `version: str = "..."` written through to `documents_media.extractor_version`. Enables tractable re-extracts when an extractor version bumps.
- **F41** every plugin under `kairix/{connectors,extractors,providers}/<name>/` carries a `py.typed` marker AND uses `# type: ignore` only with an F3-style rationale. Closes Python's runtime-encapsulation gap at the plugin boundary per `kairix-pro-platform` ADR-019. Whole-tree mypy strict (via `safe-commit.sh`) covers the mypy assertion; F41 covers the static markers.
- **F42** public Protocol methods on connector-surface Protocols (`SourceConnector`, `Extractor`, `BronzeStore`, `SilverProcessor`, `EntityGraphSink`) return a `@dataclass(frozen=True)`, `tuple[<frozen-dc>, ...]`, or one of the allowed simple shapes. Never `dict[str, Any]`, `list[dict]`, or bare `Any`. Forces frozen-dataclass discipline at the typed boundary.
- **F43** every plugin under `kairix/{connectors,extractors,providers}/<name>/` has `tests/contracts/test_<name>_protocol.py` that imports the canonical fake AND the real implementation and runs the same contract assertions against both. Mechanically proves Protocol compliance.
- **F44** engagement-scope code (every directory under `kairix/`) may not import firm-scope storage clients (`psycopg`, `psycopg2`, `asyncpg`, `pg8000`, `aiopg`). Locks the two-scope boundary from `kairix-pro-platform` ADR-017 mechanically. Engagement-scope code stays on SQLite + Neo4j + filesystem; firm-scope queries belong in a separate `kairix-firm/` codebase routed via the reflection-extractor.
- **F45** every new CLI subcommand, MCP tool, provider plugin, connector plugin, or extractor plugin must add a matching `tests/bdd/features/*.feature` in the same commit (convention: `{cli_<name>,mcp_<tool>,provider_<name>,connector_<name>,extractor_<name>}.feature`, or `# F45-feature: <path>` override comment). Pre-commit blocks otherwise. Forward-only rule.
- **F46** BDD step implementations under `tests/bdd/steps/*.py` must invoke (call-graph depth ≤ 2) a CLI entry point (`kairix.cli.main` / per-subcommand `main`), an MCP tool function, OR a factory constructor (`kairix.core.factory.build_*`). Direct construction of `*Pipeline(...)` classes is disallowed except in `tests/contracts/`. Locks the composition principle for BDD.
- **F47** tests under `tests/integration/` that exercise a multi-component pipeline must construct it via `kairix.core.factory.build_*` with `paths=FakePaths(...)`. Direct construction is allowed only in `tests/contracts/` and `tests/integration/test_<x>_contract.py`.
- **F48** `tests/e2e/test_composed_production_path.py` must exist, must carry `@pytest.mark.e2e`, must run in CI Stage 4.5 under `pytest -m e2e`, and must exercise config → factory.build → ingest → query → assertion against the composed production code. Every new top-level capability gets a sibling `tests/e2e/test_composed_<capability>_path.py`.
- **F49** each release tag (matching `v[0-9]*.[0-9]*.[0-9]*`) must reduce each of `f30-operator-outcome-tests-files.txt`, `f46-files.txt`, `f47-integration-factory-files.txt` by ≥1 entry compared to the previous tag — or keep all three at zero. Runs in `release.yml` before the tag is cut.
- **F50** net-new files (added in the staged diff at commit-time, or net-new vs the previous release tag at CI-time) may not appear in any per-file F-rule baseline. Closes the per-file-shrink-only loophole that lets a brand-new file land with arbitrary violations because the baseline doesn't yet know it exists. Pre-existing files in baselines are unaffected — F49 governs their paydown schedule. F50 only blocks fresh additions. Cross-pollinated from tc-agent-zone's `net_new_file_finding_cap.py` (2026-05-22 cross-repo audit).
- **F51** every `FeatureFlag` in `kairix/core/features/registry.py:REGISTRY` has a `target_retire_in` version ≤ current `setuptools-scm` version + 6 months. Past that, the gate fires unless the registry entry carries a `# retire-extension: <reason>` rationale comment. Stops flags becoming permanent scaffolding. See `docs/architecture/feature-flag-architecture.md` §6.
- **F52** every `flag("<name>")` call site in `kairix/**/*.py` references a `name` that exists in the registry. AST scan; catches typos and dead references after retirement.
- **F53** `kairix features status` CLI subcommand and `tool_features_status` MCP tool both exist with F30-compliant outcome tests. Operations affordance — flags are useless if operators can't see what's enabled.
- **F54** every flag in the registry has BDD scenarios for OFF and ON branches (`tests/bdd/features/feature_flag_<name>.feature` with ≥2 scenarios), integration tests exercising both branches (`tests/integration/test_feature_flag_<name>.py`), and — for flags whose `related_spec` references a top-level capability spec — an E2E composed-path test (`tests/e2e/test_composed_<name>_path.py`). Mechanically prevents the rollback-becomes-fiction failure mode.
- **F55** every `Chunker` plugin under `kairix/chunkers/<name>/__init__.py` declares a module-level `version: str` AND every `Chunk(...)` constructor call passes `chunker_version=` as a kwarg. Mirrors F40 for the chunker registry layer from `docs/architecture/connector-scope-topology/ADR.md`. Vacuous at landing — `kairix/chunkers/` doesn't exist yet; Wave C threads `chunker_version` through Silver, Wave F lands the plugins.
- **F57** every SQL `UPDATE topology_cc_pairs ... SET status = ?` lives in a module that also declares a top-level `_ALLOWED_TRANSITIONS: dict[CCPairStatus, frozenset[CCPairStatus]]` dispatch dict. Ad-hoc updates bypass the ADR v2 §3 state machine (`SCHEDULED → INITIAL_INDEXING → ACTIVE ↔ PAUSED / DELETING / INVALID`). Vacuous at landing — Wave A schema-only; Wave C lifecycle code trips F57 if it doesn't centralise transitions.
- **F58** when a class named `HierarchyConnector` exists in production code, at least one test under `tests/contracts/` has a function name matching `test_*hierarchy*parent_before_child*` AND references `HierarchyConnector`. Every `HierarchyNode` emission must have `raw_parent_id` either None (root) or referencing a previously-emitted node within the same `iter_containers()` call. Vacuous at landing — Wave E adds `HierarchyConnector` implementations.
- **F61** bare `_SqliteChunkWriter(db, collection=...)` construction lives only under `kairix/core/connectors/` (the framework owns the writer; everywhere else flows through `CollectionRouter`). Extends F38 with the per-collection routing layer from ADR v2 §"Table B". Today `kairix/worker.py:_run_one_connector_batch` is grandfathered; Wave C rewires it through `CollectionRouter`.

**Go side (active when `services/<name>/go.mod` exists; see [`docs/architecture/go-integration-plan.md`](docs/architecture/go-integration-plan.md) for full text):**

- **G1** every Go binary exposes `--version`. **G2** errors wrap with `%w`. **G3** no `interface{}`/`any` in exported signatures. **G4** `context.Context` as first arg on exported I/O. **G5** every package has a doc comment. **G6** no `panic` outside `main`/`init`. **G7** Go testing conventions only. **G8** logging via `log/slog`. **G9** every `services/<name>/` has a `README.md`. **G10** dependency-rationale registry per `services/<name>/DEPENDENCIES.md`.

Pre-existing violations are grandfathered in `.architecture/baseline/`; net-new violations block at pre-commit, in `safe-commit.sh`, and in CI's Stage 0 (or Stage 5 for F9). **Canonical reference:** [docs/architecture/fitness-functions.md](docs/architecture/fitness-functions.md). Read this before adding any silencer, skip, suppression, internal import, or BDD scenario — the gate will reject lazy bypasses.

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
