# Contributing

Read [CLAUDE.md](CLAUDE.md) for engineering standards and [CONSTRAINTS.md](CONSTRAINTS.md) for hard boundaries before starting.

## Setup

```bash
git clone https://github.com/quanyeomans/kairix
cd kairix
pip install -e ".[dev,neo4j,agents,rerank]"
```

## Making changes

1. Create a branch from `develop`
2. Make your changes
3. Commit via the gated script: `bash scripts/safe-commit.sh "your message"`
4. The script runs lint, format, mypy, tests, and security checks. If any fail, fix and re-run.
5. Open a PR targeting `develop`

## Running tests

```bash
# All tests that must pass before commit (same as safe-commit.sh)
pytest tests/ -m "unit or bdd or contract" -x --timeout=30

# Integration (requires real SQLite index)
pytest tests/ -m integration -v

# E2E (requires running kairix instance + credentials)
KAIRIX_E2E=1 pytest tests/e2e/ -v -s
```

## Testing approach

Tests use protocol fakes, not monkey-patches. See `tests/fakes.py` for fake implementations and `tests/contracts/test_protocols.py` for protocol compliance patterns.

```python
from tests.fakes import FakeClassifier, FakeDocumentRepository
from kairix.core.search.pipeline import SearchPipeline
from kairix.core.search.backends import BM25SearchBackend

pipeline = SearchPipeline(
    classifier=FakeClassifier(),
    bm25=BM25SearchBackend(FakeDocumentRepository(documents=[...])),
    ...
)
result = pipeline.search("test query")
```

See [CONSTRAINTS.md](CONSTRAINTS.md) for what's not allowed in tests.

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
```

## Branching model

| Branch | Purpose |
|---|---|
| `main` | Validated stable releases. |
| `develop` | All PRs merge here. |
| `feature/*` | One branch per feature or fix. PR targets `develop`. |

## Versioning

CalVer: `YYYY.MM.DD`. Pre-release: `YYYY.MM.DDaN`.

## Cutting a release

1. Validate on deployment target
2. PR `develop → main` — update version in `pyproject.toml`, update `CHANGELOG.md`
3. Merge, tag: `git tag v2026.X.Y && git push origin v2026.X.Y`
