# Contributing

## Setup

```bash
git clone https://github.com/three-cubes/qmd-azure-embed
cd qmd-azure-embed
pip install -e ".[dev]"
```

## Running tests

```bash
# Unit + integration (fast, no external deps required)
pytest tests/unit/ tests/integration/ -v

# E2E (requires real QMD index + Azure credentials)
QMD_E2E=1 pytest tests/e2e/ -v -s
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
qmd_azure_embed/
  schema.py       — QMD DB path resolution, schema validation, pending chunk queries
  embed.py        — Azure API client, chunking, vector encoding, DB writes
  recall_check.py — Post-embed quality gate (vsearch queries against known docs)
  cli.py          — argparse entrypoint, lockfile acquisition, run logging
```

**Key invariant:** `schema.py` is the only place that knows about QMD's table structure.
`embed.py` calls `schema.py` functions — it does not contain raw SQL against QMD's tables.
If QMD's schema changes, `schema.py` is the single file to update.

## Schema changes

If you find that QMD's schema has changed (new QMD version), see [QMD_COMPAT.md](QMD_COMPAT.md)
for the update procedure. The schema validation on startup will tell you exactly what changed.

## Adding support for other OpenAI-compatible endpoints

The embedding logic in `embed.py` uses the Azure OpenAI REST API format
(`/openai/deployments/{deployment}/embeddings`). To support standard OpenAI or other
compatible endpoints, the URL construction in `embed_batch()` and `preflight_check()` 
would need a flag to switch between Azure and OpenAI endpoint formats.
PRs welcome.

## Cutting a release

1. Bump `version` in `pyproject.toml` and `__init__.py`
2. Update [QMD_COMPAT.md](QMD_COMPAT.md) with any new tested QMD versions
3. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`
