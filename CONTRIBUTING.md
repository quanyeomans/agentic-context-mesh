# Contributing

## Setup

```bash
git clone https://github.com/quanyeomans/kairix
cd kairix
pip install -e ".[dev]"
```

## Running tests

```bash
# Unit tests (fast, no external deps required)
pytest tests/ -m unit -x --timeout=30

# Integration (requires usearch)
pytest tests/ -m integration -v

# E2E (requires real kairix index + Azure credentials)
KAIRIX_E2E=1 pytest tests/e2e/ -v -s
```

## Pre-commit checks

```bash
ruff check kairix/ tests/
ruff format --check kairix/ tests/
mypy kairix/ --ignore-missing-imports
pytest tests/ -x --timeout=30 -m unit
bash scripts/pre-commit-confidential-check.sh
```

### About skipped tests

Integration tests that exercise vector search are marked `skip` unless usearch
is installed. usearch is a pip dependency (`usearch>=2.0`) and is loaded automatically.
The skips are expected on systems where the native extension fails to compile and do not indicate broken tests.

To install usearch:

```bash
pip install "usearch>=2.0"
pytest tests/integration/
```

## Architecture

```
kairix/
  core/           # Search engine
    search/       # Hybrid BM25 + vector, RRF fusion, boosts
    embed/        # Embedding pipeline (Azure OpenAI → usearch)
    db/           # SQLite database, FTS5, schema
    temporal/     # Temporal query rewriting
    classify/     # Intent classification + content routing
  knowledge/      # Knowledge management
    entities/     # Entity discovery, seeding, validation
    graph/        # Neo4j client
    store/        # Document store operations
    wikilinks/    # Wikilink injection + audit
    reflib/       # Reference library management
    summaries/    # Document summarisation
    contradict/   # Contradiction detection
  agents/         # Agent-facing capabilities
    mcp/          # MCP server (tool_search, tool_entity, etc.)
    briefing/     # Session briefing generation
    curator/      # Entity graph health agent
    research/     # Multi-hop research agent
  quality/        # Evaluation and benchmarking
    benchmark/    # Benchmark runner, suites, scoring
    eval/         # Suite generation, monitoring, sweep
    contracts/    # Protocol definitions
  platform/       # Deployment and onboarding
    setup/        # Onboarding wizard
    onboard/      # Deployment diagnostics
    llm/          # LLM backend abstraction
```

**Key invariant:** `kairix/db/schema.py` is the single source of truth for the database schema.
`embed.py` calls schema functions — it does not contain raw SQL against the database tables.
If the schema changes, `schema.py` is the single file to update.

## Schema changes

The schema validation on `kairix onboard check` will tell you if columns or tables are missing.
Run `pytest tests/ -k "schema" -v` to verify compatibility after any schema change.

## Adding support for other OpenAI-compatible endpoints

The embedding logic in `kairix/core/embed/embed.py` uses the Azure OpenAI REST API format
(`/openai/deployments/{deployment}/embeddings`). To support standard OpenAI or other
compatible endpoints, the URL construction in `embed_batch()` and `preflight_check()`
would need a flag to switch between Azure and OpenAI endpoint formats.
PRs welcome.

## Branching model

This repo uses a `develop → main` branching model with CalVer pre-release tags.

| Branch | Purpose |
|---|---|
| `main` | Validated stable releases only. Never committed to directly. |
| `develop` | All PRs merge here. VM deploys from alpha tags cut here. |
| `feature/*` | One branch per feature or fix. PR targets `develop`. |

**PR targets `develop`, not `main`.** The CI benchmark gate runs automatically on PRs that touch retrieval code.

## Versioning

CalVer: `YYYY.MM.DD`. Pre-release tags on `develop` use the `aN` suffix: `v2026.4.18a1`, `v2026.4.18a2`.

## Cutting a release

When `develop` is validated on the deployment target:

1. Open a PR `develop → main`
2. In that PR: change `pyproject.toml` version from `2026.X.YaN` → `2026.X.Y`; update `CHANGELOG.md`
3. Merge the PR
4. Tag: `git tag v2026.X.Y && git push origin v2026.X.Y`
5. Deploy to VM: `./infra/scripts/kairix-deploy.sh v2026.X.Y`
