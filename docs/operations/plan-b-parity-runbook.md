# Plan B-parity — consolidated operator runbook

End-to-end workflow for running kairix as a per-engagement container with the Plan B-parity fact layer enabled. Read this once; follow the steps in order; teardown when the engagement closes. Cross-links point at the deep docs for each phase.

This page is a *workflow* runbook — not a reference. For "how does X actually work?" follow the link in each section heading to the matching deep doc.

## Pre-flight

Before you spin up the container, confirm three things.

### Container image

```bash
docker image pull ghcr.io/three-cubes/kairix:latest
docker image inspect ghcr.io/three-cubes/kairix:latest --format '{{.Created}}'
```

The image bundles the eval CLI and the seeded reference corpora. If `docker image pull` fails, your auth is wrong — `fix:` `docker login ghcr.io`. `next:` if pull still fails, see [`OPERATIONS.md`](OPERATIONS.md) under "Image registry".

### LLM credentials

The fact extractor calls an LLM per window. Confirm your provider plug-in is wired and the credential is loadable before ingest:

```bash
docker compose -f engagement-alpha.yml --project-name engagement-alpha exec kairix \
  kairix onboard check
```

Expected last line: `all subsystems healthy`. If `chat` is listed as degraded, the LLM credential is the problem — `fix:` re-check `KAIRIX_LLM_CREDENTIAL` or your provider config in `kairix.config.yaml`. `next:` see [`OPERATIONS.md`](OPERATIONS.md) for the per-provider credential format.

### Namespace plan

Pick a kebab-case engagement label (e.g. `engagement-alpha`). Use it as the project name, the volume prefix, and the `--namespace` value on every ingest. Two engagements with the same label share a fact store; two engagements with different labels never see each other's facts. There is no "rename namespace" tool — pick once, stick with it.

## Hydrate the knowledge store

`kairix embed` builds the chunk + vector index over your document tree. Run it once when you spin the container up, then re-run after any document edits — it's incremental on content hash, so re-runs are cheap.

```bash
export KAIRIX_ENGAGEMENT=engagement-alpha

docker compose -f engagement-alpha.yml --project-name engagement-alpha up -d --wait

docker compose -f engagement-alpha.yml --project-name engagement-alpha exec kairix \
  kairix embed --root /data/engagement-alpha-docs
```

The `--wait` flag blocks until kairix is genuinely warm (the healthcheck runs `kairix onboard ready`). When it returns you can query immediately — no sleep loops.

When to use `kairix embed`:

- First hydration of a new engagement.
- After you add, edit, or remove documents in the mounted tree.
- After a provider-plug-in or embed-model change (re-embed forces a full pass, even on unchanged files).

When *not* to use `kairix embed`:

- For chat transcripts — those go through `kairix ingest-chat` (next section). `kairix embed` works on document files; `kairix ingest-chat` works on JSONL turn sequences.

Full reference: [`OPERATIONS.md`](OPERATIONS.md) under "Embed pipeline".

## Ingest meeting transcripts

`kairix ingest-chat` reads a JSONL transcript (one turn per line), chunks it, and runs the LLM fact extractor across sliding windows of turns.

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

Realistic workflow:

1. Pre-flight with `--no-extract` to confirm the JSONL parses and the turn count matches your expectation.
2. Re-run with extraction enabled and the engagement namespace.
3. Check `facts added` is non-zero — if it's `0` despite `windows extracted > 0`, the LLM is returning malformed output. See [`fact-extractor.md`](fact-extractor.md) under "Troubleshooting".

The `--namespace` flag stamps every extracted fact with the engagement label. Forgetting it stamps facts as `shared`, which leaks them into every other engagement's queries — this is the most common ingest mistake.

Full reference: [`fact-extractor.md`](fact-extractor.md) for the extractor pipeline, prompt customisation, and tuning knobs.

## Query through the agent surface

Agents talk to kairix over MCP. `kairix prep <topic>` is the human-equivalent for spot checks:

```bash
docker compose -f engagement-alpha.yml --project-name engagement-alpha exec kairix \
  kairix prep "what did we decide about pricing in the last review?"
```

What `kairix prep` runs under the hood (federated retrieval — see [`fact-layer.md`](../architecture/fact-layer.md) section "The federation pattern"):

| Retrieval source | What it contributes | When it wins |
|------------------|---------------------|--------------|
| Chunk retriever (BM25 + vector) | Relevant paragraphs of free text | "Give me the section that discusses pricing" |
| Entity retriever (graph lookup) | Canonical entities + their relationships | "Who is agent-alpha and who do they report to?" |
| Fact retriever (SQLite FTS5 over fact store) | Structured `(entity, attribute, value)` claims | "What is agent-alpha's current engagement?" — small, citable answer |

The pipeline blends all three into one ranked, token-budgeted response (typically 1,200–1,700 tokens). Agents do not need to call the three retrievers separately. The fact retriever auto-wires when SQLite has a populated facts table — no extra config required.

For structured fact queries directly (agents asking "what do we know about entity X?"), the MCP `facts_about` tool returns the fact set without the chunk overhead — see [`MCP-ingest-tools.md`](MCP-ingest-tools.md).

## Validate per engagement

Before you hand the engagement to a live agent, score retrieval against a small ground-truth corpus.

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

### Regression-gate pattern

Pin a baseline once, then fail any future run that regresses more than 2 percentage points:

```bash
# Pin
kairix eval my-suite --json > expected/my-suite.json

# Gate
kairix eval my-suite --regression-against expected/
```

Exit code `1` = real regression; `0` = within tolerance. This is the pattern the conversation-eval CI workflow uses to gate every PR that touches retrieval or extraction code.

Full reference: [`eval-suite.md`](eval-suite.md) for suite format, metric semantics (`query-pass-rate` vs `extractor-f1`), and the LoCoMo nightly cross-backend benchmark.

## Cost model

The fact extractor is the only LLM cost in the Plan B-parity pipeline. Query-time retrieval is unchanged — the fact retriever uses SQLite FTS5 + the existing embedder, no per-query LLM calls.

### Ingest cost

One LLM extraction call runs per window (default window size = 5 turns) at extractor temperature `0.0`. So:

- A 100-turn conversation → ~20 windows → ~20 LLM calls.
- At GPT-5.4-mini pricing, this is roughly **$0.02–$0.05 per 100-turn conversation**.
- An engagement with 100 such conversations → **~$2–$5/month for fact extraction**.

If your transcripts are longer or you raise `--window-turns`, scale linearly. `--window-turns=10` halves the call count for the same transcript at the cost of giving the model a bigger window per call.

Pre-flight discipline keeps this honest: always run a new transcript shape with `--no-extract` first to confirm the chunk + window counts before paying the LLM bill.

### Query cost

Zero LLM calls per query. The federated retriever runs three database queries (chunk, entity, fact) and blends the results in-process. Latency is dominated by the embed pass on the query string, not by retrieval.

### Eval cost

`kairix eval` with `--metric query-pass-rate` runs the retriever — no LLM calls beyond what retrieval already does (zero by default). `--metric extractor-f1` re-runs the LLM extractor across every session in the suite, so it costs the same as a fresh ingest. Run `--metric query-pass-rate` per-PR; run `--metric both` weekly or before a release.

## Teardown

When the engagement closes:

```bash
docker compose -f engagement-alpha.yml --project-name engagement-alpha down -v
```

`-v` removes the named volumes. The engagement's chunk store, fact store, search index, and entity graph are all gone. Your original document tree (mounted read-only) is untouched.

### Pre-teardown archive (optional)

If compliance requires you to keep the fact store after teardown:

```bash
docker compose -f engagement-alpha.yml --project-name engagement-alpha exec kairix \
  sqlite3 /data/kairix/facts.sqlite ".backup '/data/exports/engagement-alpha-facts-$(date +%Y%m%d).sqlite'"

docker compose -f engagement-alpha.yml --project-name engagement-alpha cp \
  kairix:/data/exports ./engagement-alpha-archive/
```

### Trace cleanup

After `down -v`, confirm nothing is left running under the engagement label:

```bash
docker ps -a --filter "label=com.docker.compose.project=engagement-alpha"
```

Expected output: empty. Anything still there is a container that was started outside the compose file and needs `docker rm` by hand.

## Troubleshooting

The action markers below (`fix:` / `next:`) all correspond to real failure modes documented in the deep operator docs — they're not invented. See the linked doc for the underlying diagnostic.

### Spin-up

**`docker compose up --wait` never returns.** The `kairix onboard ready` healthcheck is failing. `fix:` exec into the container and run `kairix onboard check` — it names the failing subsystem (chat, vector_search, secrets_loaded). `next:` if the failure is in `chat`, see "LLM credentials" in pre-flight; if in `vector_search`, see [`runbooks/runbook-vector-search-failure.md`](runbooks/runbook-vector-search-failure.md). (Source: [`consultancy-in-a-box.md`](consultancy-in-a-box.md).)

### Ingest

**`kairix ingest-chat` reports `turns ingested: 0`.** The JSONL is malformed — every line is missing a required key (`role`, `content`). `fix:` `jq -c . session-001.jsonl | head -5` to confirm each line parses. `next:` if `conversation_id` is missing from every turn, name the file `<conversation-id>.jsonl` and the filename stem is used by default. (Source: [`consultancy-in-a-box.md`](consultancy-in-a-box.md).)

**`kairix ingest-chat` reports `windows extracted: N` (with N > 0) but `facts added: 0`.** The LLM is returning malformed output and the parser is rejecting every row. `fix:` re-run with `-v DEBUG` to see the rejection reason per row in the kairix log. `next:` if the LLM is returning trailing prose around the JSON array, add `response_format=json` to the provider config (provider-dependent). Full diagnostic at [`fact-extractor.md`](fact-extractor.md) under "LLM output is malformed."

**Facts from a previous engagement appear in this engagement's queries.** `--namespace` was missing on a prior ingest, so facts landed in `shared`. `fix:` re-ingest with `--namespace engagement-alpha`. `next:` to clear stray `shared`-namespace facts, see [`fact-extractor.md`](fact-extractor.md) under "Namespace hygiene".

### Query

**`kairix prep` returns "no results found" right after a fresh ingest.** Embed is asynchronous — `kairix embed` returns when work is queued, not when every chunk is indexed. `fix:` wait 30 seconds and re-run. `next:` if the issue persists, see [`runbooks/runbook-embedding-lag.md`](runbooks/runbook-embedding-lag.md). (Source: [`consultancy-in-a-box.md`](consultancy-in-a-box.md).)

### Eval

**`kairix eval: baseline file not found: <dir>/<suite>.json`.** You passed `--regression-against` but never pinned the baseline. `fix:` re-run without `--regression-against`, redirect `--json` to `<dir>/<suite>.json`, then re-run with the flag. `next:` see [`eval-suite.md`](eval-suite.md) under "Regression-gate pattern" for the full pin-once-gate-forever pattern.

**Mean score regressed but the change was an intentional improvement.** The 2pp tolerance is conservative. `fix:` re-pin the baseline in the same PR and call out the per-category shift in the commit body. `next:` reviewers can diff the `expected/<suite>.json` to see which categories moved — see [`eval-suite.md`](eval-suite.md) under "Regression-gate pattern".

### MCP

**`ingest_chat` returns `degraded_reason: "llm cold"`.** The LLM backend is still warming. `fix:` retry in ~5 seconds — the warm-gate envelope handles cold start across all retrieval/synthesis tools. `next:` if degradation persists beyond 30 seconds, see [`runbooks/runbook-vector-search-failure.md`](runbooks/runbook-vector-search-failure.md). (Source: [`MCP-ingest-tools.md`](MCP-ingest-tools.md).)

**Agent says `facts_about` returns empty for a known entity.** Either not yet ingested into the fact store (only the chunk store), or living under a different namespace than the agent is querying. `fix:` cross-check with `kairix entity audit` to confirm the canonical name. `next:` if the entity is there but missing facts, re-ingest the transcript with extraction enabled. (Source: [`MCP-ingest-tools.md`](MCP-ingest-tools.md).)

### Teardown

**Teardown leaves containers behind.** `docker compose down -v` only acts on the file passed via `-f`. `fix:` always pass both `-f engagement-alpha.yml` and `--project-name engagement-alpha`. `next:` `docker ps -a --filter "label=com.docker.compose.project=engagement-alpha"` lists anything still running under that label. (Source: [`consultancy-in-a-box.md`](consultancy-in-a-box.md).)

## Deep docs

Each phase of this runbook has a depth doc:

- [`consultancy-in-a-box.md`](consultancy-in-a-box.md) — original per-engagement workflow (same shape as this runbook, more depth on each step).
- [`fact-extractor.md`](fact-extractor.md) — extractor pipeline, prompt customisation, tuning knobs, cost-tuning details.
- [`eval-suite.md`](eval-suite.md) — `kairix eval` flags, suite format, regression-gate pattern, LoCoMo nightly.
- [`MCP-ingest-tools.md`](MCP-ingest-tools.md) — `ingest_chat` and `facts_about` MCP tools, namespace fence, agent calling patterns.
- [`docs/architecture/fact-layer.md`](../architecture/fact-layer.md) — design ADR: the four surfaces, the five capabilities, the federation pattern.

For the base operations material (config, secrets, embed pipeline, provider plug-ins), see [`OPERATIONS.md`](OPERATIONS.md). For running multiple engagements on one host, see [`SHARED-HOSTS.md`](SHARED-HOSTS.md).
