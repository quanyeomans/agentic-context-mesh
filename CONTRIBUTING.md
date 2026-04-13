# Contributing

## Setup

```bash
git clone https://github.com/quanyeomans/agentic-context-mesh
cd agentic-context-mesh
pip install -e ".[dev]"
```

## Running tests

```bash
# Unit + integration (fast, no external deps required)
pytest tests/unit/ tests/integration/ -v

# E2E (requires real QMD index + Azure credentials)
KAIRIX_E2E=1 pytest tests/e2e/ -v -s
```

### About skipped tests

Integration tests that exercise the `vectors_vec` virtual table are marked `skip` unless sqlite-vec
is installed. sqlite-vec ships as part of QMD and is present in the production runtime — it's not
a separate install for most contributors. The skips are expected and do not indicate broken tests.

To run with sqlite-vec locally, install QMD and point the tests at the QMD node_modules:

```bash
# Rough example — adapt to your QMD install path
LD_PRELOAD=$(find ~/.local -name "vec0.so" 2>/dev/null | head -1) pytest tests/integration/
```

## Architecture

```
kairix/
  cli.py              — top-level dispatcher (kairix <subcommand>)
  embed/              — Azure OpenAI embedding pipeline → sqlite-vec
    schema.py         — QMD DB path resolution, schema validation, pending chunk queries
    embed.py          — Azure API client, chunking, vector encoding, DB writes
    recall_check.py   — Post-embed quality gate
    cli.py            — argparse entrypoint, lockfile, run logging
  search/             — Hybrid BM25 + vector retrieval
    hybrid.py         — Full pipeline: intent → BM25+vec → RRF → entity boost → budget
    intent.py         — Query intent classification (6 intents)
    bm25.py           — QMD FTS wrapper
    vector.py         — sqlite-vec wrapper
    rrf.py            — Reciprocal Rank Fusion + entity/procedural boosts
    budget.py         — Token budget management (L0/L1/L2 tiering)
  entities/           — Entity graph (entities.db, alias resolution, multi-hop)
  graph/              — Neo4j client + graph models (OrganisationNode, PersonNode, etc.)
  vault/              — Vault crawler → Neo4j; vault health check
  temporal/           — Date-aware query rewriting + chunking
  summaries/          — L0/L1 tiered summary generation
  wikilinks/          — Wikilink injection + entity resolver
  briefing/           — Session briefing synthesis
  classify/           — Memory write auto-classification
  contradict/         — Contradiction detection on new knowledge writes
  curator/            — Entity health monitoring (CA-1)
  mcp/                — MCP server (search/entity/prep/timeline tools)
  benchmark/          — YAML-driven benchmark runner (NDCG@10/Hit@5/MRR@10)
```

**Key invariant:** `kairix/embed/schema.py` is the only place that knows about QMD's table structure.
`embed.py` calls `schema.py` functions — it does not contain raw SQL against QMD's tables.
If QMD's schema changes, `schema.py` is the single file to update.

## Schema changes

If you find that QMD's schema has changed (new QMD version), see [QMD_COMPAT.md](QMD_COMPAT.md)
for the update procedure. The schema validation on startup will tell you exactly what changed.

## Adding support for other OpenAI-compatible endpoints

The embedding logic in `kairix/embed/embed.py` uses the Azure OpenAI REST API format
(`/openai/deployments/{deployment}/embeddings`). To support standard OpenAI or other
compatible endpoints, the URL construction in `embed_batch()` and `preflight_check()`
would need a flag to switch between Azure and OpenAI endpoint formats.
PRs welcome.

## Cutting a release

1. Bump `version` in `pyproject.toml`
2. Update [QMD_COMPAT.md](QMD_COMPAT.md) with any new tested QMD versions
3. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`
