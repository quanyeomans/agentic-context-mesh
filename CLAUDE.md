# CLAUDE.md — Engineering Standards for kairix

## Non-Negotiables

These rules are not suggestions. They block commits, PRs, and releases.

### 1. Test-First Development

Write tests before implementation. For every piece of new code:
- **BDD feature file** first if it changes user-facing behaviour
- **Contract test** first if it adds/changes an integration boundary
- **Unit test** first for business logic

Never ship code with 0% test coverage. The coverage gate is 80% and is enforced in CI.

### 2. CI Must Pass Before Merge

All 4 CI stages must be green:
- Stage 1: Contracts (<30s)
- Stage 2: Unit + Type (ruff lint, ruff format, mypy strict, pytest with coverage)
- Stage 3: Integration
- Stage 4: Security (CodeQL, bandit, pip-audit)

Run locally before pushing: `ruff check kairix/ tests/ && ruff format --check kairix/ tests/ && mypy kairix/ --ignore-missing-imports && pytest tests/ -x --timeout=30 -m unit`

### 3. No Confidential Data in the Public Repo

`scripts/pre-commit-confidential-check.sh` blocks commits containing:
- Real company names (use generic examples: Acme Corp, Example Inc)
- Personal file paths (use `~/` or `$HOME/` instead of absolute paths)
- Cloud resource names (use `<your-resource>` placeholders)
- API keys, tokens, credentials of any kind

### 4. Backwards Compatibility with Deprecation

Renaming a public function, env var, config key, or CLI command:
- Add the new name as primary
- Keep the old name as an alias with `DeprecationWarning`
- Document the deprecation in CHANGELOG
- Remove the alias no sooner than 2 releases later

### 5. Branching and Versioning

Follow the SDLC Release Workflow:
- `main` is always releasable. Only merge commits from `develop`.
- `develop` is the integration branch. All PRs target `develop`.
- CalVer: `YYYY.MM.DD` stable, `YYYY.MM.DDaN` alpha.
- Never push directly to `main`.

---

## Development Environment

Match CI exactly to avoid push-and-pray:

```bash
pip install -e ".[dev]"
pip install ruff==0.15.12  # Pin to CI version
```

Before every commit:
```bash
ruff check kairix/ tests/
ruff format --check kairix/ tests/
mypy kairix/ --ignore-missing-imports
pytest tests/ -x --timeout=30 -m unit
bash scripts/pre-commit-confidential-check.sh
```

---

## Test Strategy

### Test Pyramid

| Layer | Share | Marker | Speed | What it tests |
|-------|------:|--------|-------|---------------|
| Unit | 50% | `@pytest.mark.unit` | <1s each | Individual function logic |
| Contract | 15% | `@pytest.mark.contract` | <30s total | Interface agreements between modules |
| Integration | 25% | `@pytest.mark.integration` | <2min | Multi-module workflows with real data |
| BDD/E2E | 10% | `@pytest.mark.bdd` | <8min | User-facing scenarios end-to-end |

### Reference Library as Test Data

The `reference-library/` directory contains 6,000+ curated open-source documents. Use it for:
- Integration tests (embed, search, entity extraction against known data)
- BDD acceptance tests (search returns expected results for known queries)
- Benchmark baselines (reproducible NDCG scores)

A 50-document integration fixture at `tests/integration/reflib_fixture/` provides fast, deterministic integration testing.

### BDD Conventions

Feature files at `tests/bdd/features/`. Step definitions at `tests/bdd/steps/`. Scenarios describe user outcomes, not implementation:

```gherkin
Scenario: Search finds relevant documents
  Given the reference library is indexed
  When I search for "architecture decision record"
  Then at least 1 result is from engineering/adr-examples/
```

### Contract Conventions

Contract tests verify integration boundaries. Location: `tests/contracts/`. They test:
- Data format agreements (scanner output matches embed input)
- Interface signatures (MCP tool schemas, CLI argument parsing)
- Schema compatibility (SQLite table structure matches query expectations)

---

## Code Quality Standards

### Naming

- Functions and variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- CLI commands: `kebab-case`
- Config keys: `snake_case`
- Env vars: `KAIRIX_UPPER_SNAKE_CASE`

### User-Facing Language

- Grade 8 reading level (12-15 year olds)
- Technical terms explained plainly with formal term in brackets
- Do not use the word "vault" for the user's documents — use "knowledge store" or "document store"
- Do not use the word "actually"

### Error Handling

- Never expose `str(exc)` to MCP callers — sanitise error messages
- Log the full exception server-side at WARNING or ERROR
- Return structured error dicts from MCP tools, never raise

### Security

- No `str(exc)` in user-facing output (may leak paths, credentials)
- No `shell=True` in subprocess calls
- No f-string SQL — use parameterised queries (`?` binding)
- `vault_path` Neo4j property references are acceptable (data model, not our "vault" concept)

---

## Architecture Decisions

ADRs live in the vault at `02-Areas/02-Three-Cubes-Ventures/Kairix-Platform/product/`. Key decisions:

- **ADR-016:** Dual-corpus architecture (reference library + user knowledge store)
- **ADR-017:** Docker Compose primary, pip fallback deployment

---

## File Organisation

```
kairix/              # Source package
  search/            # Hybrid BM25 + vector search
  embed/             # Embedding pipeline
  graph/             # Neo4j entity graph
  store/             # Document store operations (was: vault/)
  reflib/            # Reference library management
  mcp/               # MCP server and tools
  onboard/           # Deployment diagnostics
  ...
tests/               # All tests
  bdd/               # BDD feature files + step definitions
  contracts/         # Interface contract tests
  integration/       # Multi-module integration tests
  e2e/               # End-to-end tests
  reflib/            # Reference library module tests
  ...
reference-library/   # Curated open-source documents (T1-T3 licences)
docs/                # User-facing documentation
scripts/             # Operational and build scripts
```

---

## When in Doubt

- **Architecture decisions:** Discuss with Dan before implementing. Present options, verify assumptions.
- **Breaking changes:** Deprecate first, remove later. Never break existing users silently.
- **Test coverage:** If you can't test it, reconsider the design.
- **External systems:** Read the docs before assuming capabilities. Never build on a guess.
