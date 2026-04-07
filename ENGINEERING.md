# Engineering Disciplines

Standards, quality gates, and compliance requirements for Agentic Context Mesh contributors.

---

## Contents

1. [Quality Gates](#1-quality-gates)
2. [CI/CD Pipeline](#2-cicd-pipeline)
3. [Testing Standards](#3-testing-standards)
4. [Security Standards](#4-security-standards)
5. [Code Style](#5-code-style)
6. [Dependency Management](#6-dependency-management)
7. [Branch and PR Conventions](#7-branch-and-pr-conventions)
8. [Engineering Compliance Checklist](#8-engineering-compliance-checklist)

---

## 1. Quality Gates

Every merge to `main` must pass all four CI stages. No exceptions without a documented override (see §2.4).

| Gate | Tool | Threshold | Blocks merge? |
|---|---|---|---|
| Type checking | mypy (strict) | Zero errors | ✅ Yes |
| Linting | ruff | Zero errors | ✅ Yes |
| Unit tests | pytest | 100% pass | ✅ Yes |
| Test coverage | pytest-cov | ≥ 80% overall | ✅ Yes |
| SAST | bandit | Zero HIGH findings | ✅ Yes |
| Dependency CVEs | pip-audit | Zero CVEs with fixes | ✅ Yes |
| Contract tests | pytest -m contract | Zero failures | ✅ Yes |
| Build | pip install -e . | Succeeds | ✅ Yes |

**Per-module coverage targets** (documented in PRD.md §6):
- `embed/`: ≥ 90%
- `search/`, `classify/`: ≥ 85%
- `entities/`, `temporal/`: ≥ 80%
- `summaries/`, `briefing/`, `contradict/`: ≥ 75%
- `_azure.py`, `_qmd.py`, `_db.py` (shared utilities): ≥ 95%

CI enforces the aggregate 80% gate. Per-module targets are verified manually during phase review.

---

## 2. CI/CD Pipeline

### 2.1 Workflow overview

Four stages run on every push and PR. Stages 3 and 4 run in parallel after Stage 2.

```
push/PR
  │
  ├── Stage 1: Contracts (30s)     ← fast gate, fails fast
  │     schema validation
  │     interface agreement tests
  │
  ├── Stage 2: Unit + Type (2min)  ← runs on py3.10, 3.11, 3.12
  │     mypy --strict
  │     ruff check + format
  │     pytest (unit tests)
  │     coverage ≥ 80%
  │
  ├── Stage 3: Integration (5min)  ─┐
  │     pytest -m integration       │ parallel
  │     sqlite-vec required          │
  │     backward compat shim check  │
  │                                  │
  └── Stage 4: Security (5min)    ──┘
        bandit (SAST)
        pip-audit (CVE scan)
        detect-secrets (secret scan)
        artifact upload
```

### 2.2 Workflow files

| File | Trigger | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | Every push + PR | Four-stage pipeline (all gates) |
| `.github/workflows/integration.yml` | PR to main | Full integration suite + PR compliance checks |
| `.github/workflows/qmd-compat.yml` | Weekly Monday 02:00 UTC | QMD schema drift detection |
| `.github/workflows/benchmark-gate.yml` | Manual dispatch | Benchmark comparison (required for retrieval PRs) |
| `.github/dependabot.yml` | Weekly Monday 03:00 AEST | Automated dependency updates |

### 2.3 Deployment

There is no deployment pipeline. Mnemosyne runs on `vm-tc-openclaw`. Deployment:

```bash
cd /tmp/qmd-azure-embed
git pull origin main
.venv/bin/pip install -e .    # only needed if pyproject.toml changed
```

**Pre-deploy checklist:**
```
[ ] git log --oneline -5 — confirm what's deploying
[ ] pytest tests/ -q — confirm tests pass on the VM
[ ] mnemosyne embed --limit 10 — smoke test (if embed code changed)
[ ] mnemosyne search "test" --agent builder — smoke test (if search code changed)
[ ] entities.db schema_version matches code (if entities schema changed)
```

**Rollback:** `git checkout <previous-commit>` + `pip install -e .`. All state is in SQLite/vault — safe.

### 2.4 Override process (emergency only)

If a gate must be bypassed:
1. Document justification with specific business reason in the PR
2. Risk assessment and consequences
3. Mitigation plan with timeline to address
4. Dan approval required
5. Auto-expires: must be resolved within the next sprint

---

## 3. Testing Standards

### 3.1 Test pyramid

```
     ┌─────────┐
     │   E2E   │  ~5%  MNEMOSYNE_E2E=1 required. Never in CI.
     ├─────────┤
     │Integr.  │  ~25%  Real sqlite-vec. Skips cleanly if unavailable.
     ├─────────┤
     │Contract │  ~15%  Interface agreements. Zero tolerance. <30s total.
     ├─────────┤
     │  Unit   │  ~55%  Mocked externals. Fast. CI matrix (3.10/3.11/3.12).
     └─────────┘
```

### 3.2 Test markers

Mark every test class or function with the appropriate marker:

```python
@pytest.mark.contract    # interface agreement — schema, API shape, data format
@pytest.mark.unit        # individual component logic
@pytest.mark.integration # multi-component, real sqlite-vec
@pytest.mark.e2e         # live Azure API (requires MNEMOSYNE_E2E=1)
@pytest.mark.slow        # takes >5s
```

Run by stage:
```bash
pytest -m contract               # Stage 1: <30s, must pass
pytest -m "not integration"      # Stage 2: unit only (CI)
pytest -m integration            # Stage 3: requires sqlite-vec
MNEMOSYNE_E2E=1 pytest -m e2e   # Manual only
```

### 3.3 Mocking rules

**Mock only external services:**
- Azure OpenAI API (use `unittest.mock.patch` or `responses` library)
- `qmd` subprocess calls
- File system (use `tempfile.TemporaryDirectory()`)

**Keep real:**
- SQLite operations (use test DB via `MNEMOSYNE_TEST_DB` env var)
- Internal logic and data structures
- sqlite-vec extension (integration tests load the real `.so`)

**Never mock the thing under test.** If the test requires mocking the module being tested, the test is testing the wrong thing.

### 3.4 What must have a test

Every production bug found becomes a test immediately. Current production bugs tested:

| Bug | Test | Module |
|---|---|---|
| sqlite-vec extension must load before `--force` DELETE | `TestExtensionLoadOrder` | `test_sqlite_vec_constraints.py` |
| vec0 tables reject INSERT OR REPLACE | `TestSqliteVecInsertConstraints` | `test_sqlite_vec_constraints.py` |
| MATCH query fails with direct JOIN on vec0 | `TestCTEQuerySyntax` | `test_sqlite_vec_constraints.py` |

### 3.5 Regression prevention

When a bug is found in production:
1. Write a failing test that reproduces it
2. Fix the bug (make the test pass)
3. Tag the test `# regression: <brief description>`
4. Do not ship the fix without the test

### 3.6 Benchmark as evaluation (not CI)

The 50-query benchmark (`mnemosyne benchmark`) is NOT a CI test. It's an evaluation tool:
- Requires live Azure API and QMD DB
- Runs manually or via weekly cron on `vm-tc-openclaw`
- Results committed to `benchmark-results/`
- Required in PR description when retrieval logic changes

Phase gate rule: Phase N+1 does not start until Phase N benchmark confirms gate score.

---

## 4. Security Standards

### 4.1 Secret management

- **All secrets via Key Vault at runtime.** `az keyvault secret show --vault-name kv-tc-exp`
- **Never written to disk, environment file, or log**
- **Never passed as function arguments** — use the shared `_azure.py` client
- `detect-secrets` runs in CI on every PR (baseline in `.secrets.baseline`)
- If a secret is exposed: rotate immediately in Key Vault (next process run picks it up)

### 4.2 SAST (bandit)

Run locally before committing:
```bash
bandit -r mnemosyne/ --severity-level medium
```

- Zero HIGH findings: blocks merge
- MEDIUM findings: documented in PR with risk assessment; tracked as issues
- Exclusions (`# nosec`): require inline justification comment

### 4.3 Dependency security (pip-audit)

```bash
pip-audit --requirement <(pip freeze) --format markdown
```

- Zero CVEs with available fixes: blocks merge
- CVEs without fixes: documented in PR, tracked as issues, remediated within 1 week when fix becomes available
- Dependabot opens weekly PRs for dependency updates (see §6)

### 4.4 4-layer defence

| Layer | Tool | Trigger | Gate |
|---|---|---|---|
| SAST | bandit | Every PR | Zero HIGH |
| Dependency scan | pip-audit | Every PR + weekly Dependabot | Zero CVEs with fixes |
| Dynamic testing | pytest security tests | Every PR | All security tests pass |
| Source control | detect-secrets | Every PR | No secrets in diff |

### 4.5 Agent scoping enforcement

The `--agent` parameter in all Mnemosyne commands enforces collection boundaries. Tests must verify that:
- Agent A cannot write to Agent B's knowledge collections
- Shared collections are readable by all agents but only writable via explicit `--scope shared`
- Coach and Family agent identifiers are rejected by the Mnemosyne CLI entirely

---

## 5. Code Style

### 5.1 Type annotations

**All public functions must have type annotations.** This is enforced by `mypy --strict` in CI.

```python
# Correct
def rrf_score(bm25_rank: int, vec_rank: int, k: int = 60) -> float:
    ...

# Wrong — will fail mypy
def rrf_score(bm25_rank, vec_rank, k=60):
    ...
```

### 5.2 Named constants

All thresholds, configuration values, and magic numbers must be named constants at module level with a comment explaining their derivation.

```python
# Correct
RRF_K = 60             # Standard RRF constant — prevents high ranks dominating. A/B tested in Phase 1.
ENTITY_BOOST = 0.20    # Boost factor per entity mention. Capped at ENTITY_BOOST_CAP.
ENTITY_BOOST_CAP = 2.0 # Maximum entity boost multiplier.

# Wrong
score = 1 / (60 + rank)   # where did 60 come from?
```

### 5.3 Module docstrings

Every module must have a top-level docstring covering:
- What the module does
- Key inputs and outputs
- Failure modes and fallbacks

```python
"""
mnemosyne.search.rrf
~~~~~~~~~~~~~~~~~~~~

Reciprocal Rank Fusion implementation for combining BM25 and vector search results.

Inputs:
  bm25_results: list of BM25Result from mnemosyne.search.bm25
  vec_results:  list of VecResult from mnemosyne.search.vector

Output:
  list of FusedResult, sorted descending by RRF score

Failure modes:
  - Either input list empty: returns the non-empty list ranked by original score
  - Both empty: returns []
  - Entity boosting DB unavailable: returns fused results without boost (logged)
"""
```

### 5.4 No print() in production code

Use `logging` in all non-CLI modules. `print()` is allowed only in `cli.py` files (for user-facing output). Enforced by ruff `T201` rule.

### 5.5 Commit message convention

```
feat(search): implement RRF fusion with entity boosting (#42)
fix(embed): load sqlite-vec before --force DELETE (#38)
test(embed): add TestExtensionLoadOrder for production bug (#39)
docs: add ENGINEERING.md — engineering disciplines
chore(deps): bump requests from 2.31 to 2.32
```

Types: `feat`, `fix`, `test`, `docs`, `refactor`, `chore`, `perf`  
Scope: module name or area (`embed`, `search`, `entities`, `ci`, `deps`)

---

## 6. Dependency Management

### 6.1 Dependabot

Weekly automated PRs (Monday 03:00 AEST):
- **Python dependencies:** Minor/patch dev dependencies grouped into one PR. Production dependencies (requests) get individual PRs.
- **GitHub Actions:** Action version updates.

All Dependabot PRs require CI to pass before merge. No manual merge without CI green.

### 6.2 QMD version compatibility

QMD (`qmd`) is the critical external dependency. It ships sqlite-vec and defines the SQLite schema we write into.

- Tested QMD version pinned in `pyproject.toml` (`[tool.mnemosyne]` section)
- Weekly CI job (`qmd-compat.yml`) checks schema drift
- On drift: GitHub issue opened automatically, work required within 1 week
- QMD upgrades: run `mnemosyne embed --limit 0` to validate schema before full run

### 6.3 sqlite-vec version

sqlite-vec is bundled with QMD. We do not install it independently. The `.so` path is:
```
/data/workspace/.tools/qmd/node_modules/.pnpm/sqlite-vec-linux-x64@<version>/node_modules/sqlite-vec-linux-x64/vec0.so
```

The `find_sqlite_vec()` function in `mnemosyne/embed/schema.py` searches for this path dynamically. If QMD is upgraded and the sqlite-vec version changes, this function will find the new path automatically.

### 6.4 Adding a new dependency

1. Is it really necessary? Can we use stdlib?
2. Check `pip-audit` for known CVEs before adding
3. Pin to a minor version: `requests>=2.31,<3.0`
4. Add to `pyproject.toml` under the correct group (`dependencies` or `dev`)
5. Update `.github/dependabot.yml` if it needs custom grouping

---

## 7. Branch and PR Conventions

### 7.1 Branch naming

```
feat/search-hybrid-rrf         # new feature
fix/embed-extension-load-order # bug fix
refactor/embed-staging-table   # internal restructure
test/search-intent-classifier  # test additions
docs/engineering-disciplines   # documentation
chore/deps-bump-requests       # dependency updates
```

### 7.2 PR requirements

**Before opening a PR:**
- [ ] All CI stages pass locally (`pytest tests/`, `mypy mnemosyne/`, `ruff check mnemosyne/`)
- [ ] No secrets in diff (`detect-secrets scan mnemosyne/ tests/`)
- [ ] If retrieval logic changed: benchmark comparison included in description

**PR description must include:**
- What changed and why (1-3 sentences)
- How to test/verify
- If retrieval logic changed: before/after benchmark scores (at minimum recall and conceptual categories)
- Any open questions or follow-up work

**Merge strategy:** Squash merge only. PR title becomes commit message.

### 7.3 Review requirements

- At least one approval (Dan or Builder with relevant context)
- All CI stages green
- No unresolved comments

---

## 8. Engineering Compliance Checklist

Use before merging any PR:

```
PRE-COMMIT
[ ] Type annotations present on all new public functions
[ ] Named constants for all thresholds/magic numbers
[ ] Module docstring present (if new module)
[ ] No print() in non-CLI modules
[ ] No secrets in code or tests

CI GATES (all must be green)
[ ] Stage 1: Contract tests pass
[ ] Stage 2: mypy zero errors (py3.10/3.11/3.12)
[ ] Stage 2: ruff zero errors
[ ] Stage 2: Unit tests 100% pass
[ ] Stage 2: Coverage ≥ 80%
[ ] Stage 3: Integration tests pass (or skip with explanation)
[ ] Stage 4: bandit zero HIGH
[ ] Stage 4: pip-audit zero CVEs with fixes
[ ] Stage 4: No secrets detected

RETRIEVAL LOGIC CHANGES ONLY
[ ] Benchmark before/after in PR description
[ ] No category regressed below Phase 0 baseline
[ ] If phase gate met: update benchmark results table in PRD.md

SCHEMA CHANGES ONLY
[ ] entities.db schema_version incremented
[ ] Migration script added under entities/migrations/
[ ] QMD schema guard updated (mnemosyne/embed/schema.py) if QMD schema affected

DEPLOYMENT
[ ] Pre-deploy checklist in PRD.md §8.3 completed
[ ] Cron updated if new subcommands added
```

---

## Nightly Entity Extraction (Cron Scheduling)

The entity extraction pipeline runs nightly after the vault git sync. These commands must be added
to the host cron by Shape/Builder at deploy time.

### Schedule

```bash
# /etc/cron.d/mnemosyne  (or crontab -e on the deploy host)

# Vault git sync — 23:00 AEST (13:00 UTC)
0 13 * * * mnemosyne-user /usr/local/bin/vault-git-sync.sh

# Entity extraction — 23:30 AEST (13:30 UTC), after vault sync
30 13 * * * mnemosyne-user cd /data && \
    MNEMOSYNE_TEST_DB="" \
    AZURE_OPENAI_API_KEY="$(az keyvault secret show --vault-name kv-tc-exp --name azure-openai-api-key --query value -o tsv)" \
    AZURE_OPENAI_ENDPOINT="$(az keyvault secret show --vault-name kv-tc-exp --name azure-openai-endpoint --query value -o tsv)" \
    mnemosyne entity extract --changed >> /data/mnemosyne/logs/entity-extract.log 2>&1

# Wikilink injection — 23:45 AEST (13:45 UTC), after entity extraction
45 13 * * * mnemosyne-user mnemosyne wikilinks inject --changed >> /data/mnemosyne/logs/wikilinks.log 2>&1
```

### Manual invocation

```bash
# Incremental (since last run) — preferred for daily operation
mnemosyne entity extract --changed

# Full vault scan (slow, use only for initial population or recovery)
mnemosyne entity extract --all

# Single file (useful for debugging)
mnemosyne entity extract --path /data/obsidian-vault/01-Projects/Mnemosyne/ADR-M07.md

# LLM-assisted extraction (requires Azure OpenAI credentials in environment)
export AZURE_OPENAI_API_KEY=$(az keyvault secret show --vault-name kv-tc-exp --name azure-openai-api-key --query value -o tsv)
export AZURE_OPENAI_ENDPOINT=$(az keyvault secret show --vault-name kv-tc-exp --name azure-openai-endpoint --query value -o tsv)
mnemosyne entity extract --changed --use-llm

# Review merge candidates (read-only report)
mnemosyne entity reconcile --report
```

### Log files

- `/data/mnemosyne/logs/entity-extract.log` — extraction pipeline output
- `/data/mnemosyne/logs/wikilinks.log` — wikilink injection output

Set `MNEMOSYNE_LOG_QUERIES=1` to enable additional query logging.

---

## Search Observability

Set `MNEMOSYNE_LOG_QUERIES=1` to enable raw query logging to `/data/mnemosyne/logs/queries.jsonl`.
Disabled by default (privacy: queries may contain sensitive content).
Analyse with: `python3 scripts/analyze_queries.py`

The log captures: timestamp, raw query text, query hash (cross-reference with `search.jsonl`), intent,
agent, fused result count, vec_failed flag, latency, and top-3 result paths.

Log rotation: automatically rotates to `queries.jsonl.1` when the file exceeds 10 MB (keeps last 2 files).

Environment variables:
- `MNEMOSYNE_LOG_QUERIES=1` — enable logging (default: `0`)
- `MNEMOSYNE_QUERY_LOG=<path>` — override log path (default: `/data/mnemosyne/logs/queries.jsonl`)

---



---

## Entity System — Known Failure Modes and Fixes

### RRF path format mismatch (fixed 2026-03-23, commit `5be5b55`)

**Problem:** BM25 returns document paths as `qmd://collection-name/relative/path.md`. Vector search returns the same document as `relative/path.md`. The RRF `fused` dict keys on path, so they never merge — entity stubs (which live in `vault-entities` collection) never get combined BM25+vector RRF score.

**Fix:** `_canonical_path()` in `mnemosyne/search/rrf.py` strips the `qmd://collection-name/` prefix before using path as dict key. Both BM25 and vector results for the same document now merge correctly.

**Symptom to watch for:** Entity stubs appear in results with `in_bm25=True in_vec=False` (or vice versa) when they should have both. If entity score collapses after a code change, check that `_canonical_path()` is called on both BM25 and vector result paths before keying into `fused`.

### Entity boost silently returning zero (fixed in `8fe178b`)

**Problem:** `entity_mentions` stores absolute paths with original case. QMD stores paths as lowercase+relative+hyphen. `mention_counts.get(r.path, 0)` always returned 0 — entity boost never fired.

**Fix 1 (global scan — regressed recall 0.875→0.250):** Normalised all entity_mentions paths. But this made index files (facts.md 183 mentions, wikilink-entity-index.md 91 mentions) boost every query globally — index files dominated results.

**Fix 2 (query-scoped, `8fe178b`):** Boost only counts mentions for documents already in the candidate result set. Normalise relative to max in result set. Index files only boost when they're genuinely relevant to the query.

**Rule:** Entity boost must always be scoped to candidate results. Never scan all entity_mentions for a global sort.

### Benchmark entity score collapse (gold_path: null)

**Problem:** Entity queries in `suites/builder.yaml` were created with `gold_path: null` and `score_method: llm`. LLM judge scores tangentially-relevant docs 0.2–0.4 even when entity stubs are correctly retrieved, because it has no ground truth to compare against.

**Fix:** Add explicit `gold_path` to entity cases mapping to the canonical entity stub. Use `score_method: exact` for queries with a single unambiguous answer doc. Keep `score_method: llm` only when answers genuinely span multiple docs (temporal queries, "what has X been working on" queries).

**Rule:** Any query with a canonical answer document should have `gold_path` set and `score_method: exact`. Never use `score_method: llm` when exact matching is possible — it introduces LLM judge variance and masks real retrieval failures.

### Entity stub content sparsity

**Problem:** Entity stubs with sparse content (~32 lines) lose in BM25+vector ranking to incidental content-rich vault docs. A doc about "Arize AI observability for agent platforms" outranks `builder.md` for "what is Builder responsible for" because it has more content surface area.

**Fix:** Entity stubs must be ≥500 words of substantive content. Each stub should be the definitive answer to "what do we know about X" — not a registry entry.

**Standard content sections:** About/Overview, Role in the Team, Key Decisions, Active Projects, Communication Patterns, Relationships, Tags.

**Rule:** After enriching a stub, run `mnemosyne embed --changed` to re-index before running benchmark. Unenriched stubs with new content that hasn't been embedded will not surface in vector search.

### vectors_vec silent wipe

**Problem:** `qmd update` can recreate the `vectors_vec` virtual table, dropping all 1536-dim vectors. The `content_vectors` table (metadata) is unaffected, so the embed run log shows vectors as present but vector search returns nothing.

**Detection:** `SELECT COUNT(*) FROM vectors_vec` — should match approximately `SELECT COUNT(*) FROM content_vectors WHERE model='text-embedding-3-large'`.

**Fix:** Force full re-embed with `mnemosyne embed --force`. Costs ~$0.17 for the full vault (6,500 chunks). Takes ~10 minutes.

**Rule:** Before running any benchmark, verify vector count: `SELECT COUNT(*) FROM vectors_vec` > 5000. If not, re-embed before benchmarking.

---

## Benchmark Suite Maintenance

### Suite file: `suites/builder.yaml`

The YAML suite is the canonical benchmark. Old Python scripts (`run-benchmark-hybrid.py`) are deprecated. Always use:

```bash
mnemosyne benchmark run --suite suites/builder.yaml --agent shape --budget 5000
```

### Gold path validity rules

1. Gold paths must be verified to rank in top-3 for their query via direct BM25 search before adding to suite
2. Gold paths must use the QMD-normalised format: lowercase, hyphens for spaces, relative to collection root
3. Verify gold paths still exist after vault reorganisation: `qmd search --json "<query>" | jq '.[0].file'`
4. Never add a gold path without verifying it with a live search first

### When to use exact vs LLM scoring

- `score_method: exact` — use when there is one unambiguous correct document (recall, entity, procedural)
- `score_method: llm` — use when the answer spans multiple docs or changes over time (temporal, multi-hop, conceptual)

Using `llm` when `exact` is possible introduces variance and masks retrieval failures. Default to `exact`.

### Suite integrity check

Before any benchmark run:
```bash
# Verify all gold paths exist in QMD index
python3 scripts/verify_gold_paths.py suites/builder.yaml
# Verify vector count is healthy
python3 -c "
import sqlite3
db = sqlite3.connect('/home/openclaw/.cache/qmd/index.sqlite')
count = db.execute('SELECT COUNT(*) FROM vectors_vec').fetchone()[0]
print(f'vectors_vec: {count} rows')
assert count > 5000, 'Vector index unhealthy — run mnemosyne embed --force'
"
```
