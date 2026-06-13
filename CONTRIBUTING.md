# Contributing

Read [CLAUDE.md](CLAUDE.md) for engineering standards and [CONSTRAINTS.md](CONSTRAINTS.md) for hard boundaries before starting.

## Setup

```bash
git clone https://github.com/three-cubes/kairix
cd kairix
make setup-dev
```

`make setup-dev` runs `scripts/dev/setup.sh` — it checks prerequisites (Python 3.12+, pip, git), installs kairix with the canonical CI extras set, and wires the pre-commit hooks. Idempotent; safe to re-run after pulling new deps.

To dry-run (report what would change without installing): `make setup-check`.

For granular control:

```bash
pip install -e ".[dev,agents,markitdown,pdf_fallback,ocr,pptx,docx,xlsx]"
make setup           # pre-commit hooks only
```

### Fitness-check config (F73 private-infra patterns)

The F73 token-pattern scanner loads its pattern set from org config in the
`three-cubes` org — single-sourced for CI and local dev. CI reads the org
secret; locally you fetch the org variable (org-member-readable) with the
`gh` CLI:

```bash
# Export into your current shell (preferred):
eval "$(bash scripts/fetch-fitness-config.sh)"

# Or cache it to the gitignored .private-infra-patterns fallback file:
make fitness-config
```

Without the patterns the scanner is a no-op locally, so the commit gate
passes — CI is still the backstop. The hand-maintained
`.private-infra-patterns` file is a last-resort fallback/cache only; the
org variable is the canonical local source.

## Making changes

Kairix is trunk-based on `main`. Routine work commits direct to `main` when `safe-commit.sh` is green.

1. Pull latest `main`
2. Make your changes
3. Commit via the gated script: `bash scripts/safe-commit.sh "your message"`
4. The script runs lint, format, mypy, tests, security checks, and Sonar new-code parity. If any fail, fix and re-run.
5. For docs/CHANGELOG-only edits, use `--fast` to skip the heavy test suite: `bash scripts/safe-commit.sh --fast "docs: …"`.
6. Push to `main` once green.

Open a PR (`feat/*` or `fix/*` branch) when you want grouped review, a release-stabilisation cycle, or cross-team sign-off. PRs gate on the same CI checks as direct push, plus mandatory branch-protection checks. Merge with `gh pr merge --merge` — never squash; per-commit history is the audit trail.

## Running tests

```bash
# All tests that must pass before commit (same as safe-commit.sh)
pytest tests/ -m "unit or bdd or contract" -x --timeout=30

# Integration (requires real SQLite index)
pytest tests/ -m integration -v

# E2E composed production path (CI Stage 4.5)
pytest tests/e2e/ -m e2e -v

# Soak tier (nightly on main; not part of the PR gate)
pytest tests/ -m soak -v
```

## Testing approach

Tests use protocol fakes, not monkey-patches. See `tests/fakes.py` for fake implementations and `tests/contracts/test_protocols.py` for protocol compliance patterns.

```python
from tests.fakes import FakeClassifier, FakeDocumentRepository
from kairix.core.factory import build_search_pipeline

pipeline = build_search_pipeline(
    paths=FakePaths(...),
    classifier=FakeClassifier(),
    document_repository=FakeDocumentRepository(documents=[...]),
)
result = pipeline.search("test query")
```

Construct pipelines through `kairix.core.factory.build_*`, not by direct `SearchPipeline(...)` / `EmbedPipeline(...)` construction. F46/F47/F48 enforce this. See [CONSTRAINTS.md](CONSTRAINTS.md) and [`docs/architecture/test-discipline-hardening.md`](docs/architecture/test-discipline-hardening.md) for the full specification.

## Architecture

Protocols define every boundary. Pipelines compose protocols. Factories build production pipelines. See [CLAUDE.md](CLAUDE.md) for the full architecture overview.

Key files for contributors:
- `kairix/core/protocols.py` — all domain boundary interfaces
- `kairix/core/factory.py` — how production pipelines are constructed
- `kairix/core/search/pipeline.py` — the search pipeline orchestrator
- `tests/fakes.py` — fake implementations for testing

```
kairix/
  core/
    protocols.py         # Domain boundary protocols
    factory.py           # Production pipeline construction
    search/
      pipeline.py        # SearchPipeline orchestrator
      backends.py        # BM25, Vector search adapters
      fusion.py          # RRF, BM25Primary fusion strategies
      boosts.py          # Entity, Procedural, Temporal boost strategies
    db/
      repository.py      # SQLiteDocumentRepository
    embed/
      pipeline.py        # EmbedPipeline orchestrator
  knowledge/
    graph/
      repository.py      # Neo4jGraphRepository
  quality/
    eval/
      scorers.py         # NDCG, ExactMatch, LLMJudge scoring strategies
    benchmark/
      pipeline.py        # BenchmarkPipeline orchestrator
  agents/
    briefing/
      pipeline.py        # BriefingPipeline orchestrator
tests/
  fakes.py               # All fake implementations
  contracts/             # Protocol compliance tests
  integration/           # Real DB, real paths
  e2e/                   # Composed production-path E2E (CI Stage 4.5)
```

## Branching model

Trunk-based on `main`. The historical `develop` branch was retired in v2026.6.8.

| Branch | Purpose |
|---|---|
| `main` | **Default branch.** All work lands here — direct push or PR. Release tags point at `main` SHAs. |
| `feat/*`, `fix/*` | Optional feature branches for grouped commits, release stabilisation, or external review — PR targets `main`. |

The `raw.githubusercontent.com/.../main/...` URLs in [README.md](README.md) and [docker-compose.yml](docker-compose.yml) point at `main`, which serves both as default and as the last-released compose source.

## Versioning

CalVer: `YYYY.M.D`. Pre-release: `YYYY.M.DaN`.

## Cutting a release

Releases are HITL — they ship to shared infra. Don't run release workflows without explicit per-action authorisation.

1. Validate on the deployment target.
2. Confirm `CHANGELOG.md` `[Unreleased]` section is fully populated (no empty sub-sections) and the version label matches CalVer (`vYYYY.M.D[aN]`).
3. Trigger the **`5 · Release`** workflow (Actions tab → workflow_dispatch) with `version=vYYYY.M.D[aN]`. It tags `main` HEAD, extracts the `[Unreleased]` CHANGELOG section as release notes, and creates the GitHub Release. The release-created event then fires Docker + PyPI publish workflows automatically.

See [scripts/release-checklist.md](scripts/release-checklist.md) for the full end-to-end checklist including post-deploy validation.
