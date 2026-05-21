# Consultancy-in-a-box — end-to-end operator workflow

## What this is for

Kairix runs as a per-engagement container. One client, one knowledge store, one fact store, one MCP endpoint. When the engagement ends you tear the container down and the data goes with it. This document walks you through the full lifecycle for a single engagement: spin up → ingest your documents → ingest your meeting transcripts → query → validate → tear down.

The model is "one container per engagement" so two engagements never share a knowledge store, two agents working on different engagements never see each other's facts, and tearing down a container removes every trace of the engagement from disk.

## Walkthrough

This walkthrough uses generic placeholder names. Substitute your own engagement label (kebab-case, short) wherever you see `engagement-alpha`.

### 1. Spin up the engagement container

```bash
# From the kairix checkout, copy the template compose file
cp docker/compose.engagement.yml ./engagement-alpha.yml

# Set the engagement label so volumes / network are namespaced
export KAIRIX_ENGAGEMENT=engagement-alpha

# Start the stack — kairix + neo4j + the MCP endpoint
docker compose -f engagement-alpha.yml --project-name engagement-alpha up -d --wait
```

The `--wait` flag blocks until kairix is genuinely warm — the healthcheck runs `kairix onboard ready` which only succeeds once the first real agent call would also succeed. When it returns you can run real queries; you do not need a sleep loop.

Verify:

```bash
docker compose -f engagement-alpha.yml --project-name engagement-alpha exec kairix \
  kairix onboard check
```

Expected output ends with `all subsystems healthy`.

### 2. Ingest the engagement knowledge store

Mount your engagement document tree into the container, then run an embed cycle. Documents stay on your machine; kairix only writes the search index.

```bash
# Mount the document tree (read-only) and trigger an embed pass
docker compose -f engagement-alpha.yml --project-name engagement-alpha exec kairix \
  kairix embed --root /data/engagement-alpha-docs
```

`kairix embed` is incremental — it only re-embeds files whose content hash changed since the last pass. Re-running after edits is cheap.

### 3. Ingest meeting transcripts

If you record meetings (or have chat exports from a coordination channel), feed them through `kairix ingest-chat`. The transcript should be JSONL — one turn per line — with `role` and `content` keys at minimum. `conversation_id` is optional; if absent, the filename stem is used (so `session-001.jsonl` becomes `conversation_id=session-001`).

```bash
docker compose -f engagement-alpha.yml --project-name engagement-alpha exec kairix \
  kairix ingest-chat /data/transcripts/session-001.jsonl \
  --namespace engagement-alpha
```

Example output:

```
kairix ingest-chat: complete
  turns ingested:         84
  conversations processed: 1
  windows extracted:       17
  facts added:             23
  facts superseded:        2
```

Each window of turns (default 5) is fed through the LLM fact extractor; the extracted facts are persisted to the fact store and stamped with `namespace=engagement-alpha` so cross-engagement queries can't accidentally surface them.

For chunks-only ingest (no LLM calls, no fact extraction — useful for pre-flighting transcript shape before paying the LLM bill), pass `--no-extract`. See [fact-extractor.md](fact-extractor.md) for the cost model.

### 4. Query through the agent surface

Agents talk to kairix over MCP. The `kairix prep <topic>` command is the human-equivalent — useful for spot-checking what an agent will see:

```bash
docker compose -f engagement-alpha.yml --project-name engagement-alpha exec kairix \
  kairix prep "what did we decide about pricing in the last review?"
```

Returns a ranked, right-sized brief — usually 1,200–1,700 tokens of relevant content with the most-relevant chunk first. The `facts_about` MCP tool (see [MCP-ingest-tools.md](MCP-ingest-tools.md)) returns the structured fact set; the search pipeline already federates facts into `prep` so agents don't have to call both.

### 5. Validate retrieval quality

Before you hand the engagement to a live agent, score retrieval against a small seed of ground-truth queries. Build a `ground-truth-queries.json` in your engagement directory (format documented in `reference-library/conversations/README.md`), then:

```bash
docker compose -f engagement-alpha.yml --project-name engagement-alpha exec kairix \
  kairix eval /data/engagement-alpha-eval --metric query-pass-rate
```

Example output:

```
Suite: engagement-alpha (path=/data/engagement-alpha-eval)
  Questions  : 18/20 (90%)
  Mean score : 0.873
  By category:
    multi-hop      6/7  (86%) mean=0.821
    single-hop    10/10 (100%) mean=0.940
    temporal       2/3  (67%) mean=0.703
```

See [eval-suite.md](eval-suite.md) for the full eval workflow, including the regression-gate pattern for ongoing quality monitoring.

### 6. Teardown when the engagement closes

```bash
docker compose -f engagement-alpha.yml --project-name engagement-alpha down -v
```

The `-v` flag removes the named volumes. The engagement's knowledge store, fact store, search index, and entity graph are all gone. The original document tree on your filesystem is untouched (it was mounted read-only).

If you want to archive the fact store before teardown (for compliance or audit), copy it out first:

```bash
docker compose -f engagement-alpha.yml --project-name engagement-alpha exec kairix \
  sqlite3 /data/kairix/facts.sqlite ".backup '/data/exports/engagement-alpha-facts-$(date +%Y%m%d).sqlite'"

docker compose -f engagement-alpha.yml --project-name engagement-alpha cp \
  kairix:/data/exports ./engagement-alpha-archive/
```

## Customisation knobs

| Knob | Effect | Where |
|------|--------|-------|
| `--namespace` (ingest-chat) | Engagement-scope tag on every extracted fact; queries can filter on it | CLI flag |
| `--window-turns` (ingest-chat) | Sliding-window size in turns for fact extraction (default 5; larger windows give the extractor more context but cost more LLM tokens) | CLI flag |
| `--no-extract` (ingest-chat) | Skip the LLM fact-extraction pass; chunks-only ingest | CLI flag |
| `KAIRIX_DATA_DIR` | Where the per-engagement SQLite + Neo4j data lives | env var, defaults to `/data/kairix` |
| Document `paths` in `kairix.config.yaml` | Which collections kairix indexes | config file |

For deeper config (provider plug-in selection, embed concurrency, retrieval boost weights), see [OPERATIONS.md](OPERATIONS.md).

## Troubleshooting

**`docker compose up --wait` never returns.**

The healthcheck is waiting on `kairix onboard ready` to succeed. Most often the embed credential is wrong or the document root is empty. `fix:` exec into the container and run `kairix onboard check` — it points at the failing subsystem. `next:` check the container logs with `docker compose logs kairix`.

**`kairix ingest-chat` reports 0 turns ingested.**

Either the JSONL file is malformed or every line is missing a required key (`role`, `content`). `fix:` check the first few lines validate with `jq -c . session-001.jsonl | head -5` — non-JSON lines are skipped with a WARNING in the kairix log. `next:` if turns lack `conversation_id`, name the file `<conversation-id>.jsonl` and the filename stem is used as the default.

**`kairix prep` returns "no results found" right after a fresh ingest.**

The embed pipeline is asynchronous — `kairix embed` returns when the work is queued, not when every chunk is indexed. `fix:` wait 30 seconds, then re-run. `next:` if the issue persists, see [`runbooks/runbook-embedding-lag.md`](runbooks/runbook-embedding-lag.md).

**Facts from a previous engagement appear in this engagement's queries.**

You forgot the `--namespace` flag on `kairix ingest-chat` (defaults to `shared`). `fix:` re-ingest with `--namespace engagement-alpha`. `next:` to clear stray shared-namespace facts already in the store, see [fact-extractor.md](fact-extractor.md) under "Namespace hygiene".

**Teardown leaves containers behind.**

`docker compose down -v` only acts on the file passed via `-f`. `fix:` always pass both `-f engagement-alpha.yml` and `--project-name engagement-alpha` to every compose command. `next:` `docker ps -a --filter "label=com.docker.compose.project=engagement-alpha"` lists anything still running under that engagement label.

## See also

- [fact-extractor.md](fact-extractor.md) — how the LLM extractor works, when to enable it, cost model
- [eval-suite.md](eval-suite.md) — `kairix eval` workflow + regression-gate pattern
- [MCP-ingest-tools.md](MCP-ingest-tools.md) — calling `ingest_chat` + `facts_about` from agents
- [`docs/architecture/fact-layer.md`](../architecture/fact-layer.md) — design ADR for the fact layer
- [OPERATIONS.md](OPERATIONS.md) — base operations guide (config, secrets, embed pipeline)
- [SHARED-HOSTS.md](SHARED-HOSTS.md) — running multiple engagements on one host
