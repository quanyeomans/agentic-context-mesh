# Non-functionals — storage, freshness, latency, conversion cost

Quantitative envelopes per connector and operator-deployment shape.
Drives concurrency caps, freshness SLOs, storage planning, and
operator-config defaults the ADR commits to.

All numbers are operating-point targets, not contracts — `Source` column
flags whether the number is published (`pub`), operator-observed
(`obs`), or estimated from first principles (`est`).

## Storage growth model

### Per-source per-item footprint (estimated)

| Source kind | Avg item raw size | Avg chunks/item | Per-chunk bytes (text + vector) | Per-item storage | Source |
|---|---:|---:|---:|---:|---|
| Obsidian (.md) | 3 KB | 12 | ~1.2 KB text + ~6 KB vector (1536-dim f32) ≈ 7.2 KB | ~86 KB | obs (dogfood VM) |
| SharePoint docx | 30 KB | 8 | 7.2 KB | ~58 KB | est |
| SharePoint xlsx | 200 KB | 25 (per-sheet × sheet count) | 7.2 KB | ~180 KB | est |
| SharePoint pptx | 5 MB | 30 (per-slide × notes) | 7.2 KB | ~216 KB | est |
| SharePoint pdf | 1 MB | 18 (per-page × OCR or not) | 7.2 KB | ~130 KB | est |
| Notion page (avg) | 5 KB | 6 (blocks → markdown chunks) | 7.2 KB | ~43 KB | est |
| Notion database row | 0.5 KB | 1 | 7.2 KB | ~7 KB | est |
| Slack message | 1 KB | 1 (small chunk per msg) | 7.2 KB | ~7 KB | est |
| Slack attachment (file) | source-size-dependent | per-MIME | 7.2 KB/chunk | source-size + 7-200 KB | est |
| GitHub source file | 5 KB | 8 | 7.2 KB | ~58 KB | est |
| GitHub issue/PR (with comments) | 4 KB | 4 | 7.2 KB | ~29 KB | est |
| GitHub markdown (README, wiki) | 6 KB | 8 | 7.2 KB | ~58 KB | est |
| Google Docs (markdown export) | 8 KB | 10 | 7.2 KB | ~72 KB | est |
| Google Sheets (CSV per sheet) | 50 KB | 5 (per logical table block) | 7.2 KB | ~36 KB | est |
| Google Slides | 1.5 MB | 12 (per slide) | 7.2 KB | ~86 KB | est |
| Dex contact | 0.3 KB | 0 (EntitySignal only) | n/a | ~0.2 KB row + Neo4j node | est |
| Dex org | 0.3 KB | 0 | n/a | ~0.2 KB + Neo4j node | est |
| M365 email header bundle | 0.5 KB | 1 | ~7 KB (with vector) | ~7.5 KB | est |
| M365 calendar event | 1 KB | 1 | 7.2 KB | ~8.2 KB | est |

### Whole-deployment storage projections

For a deployment with the following content profile (operator-realistic):

| Source | Item count | Per-item storage | Subtotal |
|---|---:|---:|---:|
| Obsidian (engagement vault) | 6 k files | 86 KB | 515 MB |
| SharePoint corp tenant | 50 k mixed docs (50% docx, 20% xlsx, 15% pptx, 15% pdf) | weighted avg 110 KB | 5.5 GB |
| Notion workspace | 5 k pages + 20 k database rows | weighted avg 14 KB | 350 MB |
| Slack workspace | 200 k messages + 5 k files | 7 KB + 50 KB | 1.65 GB |
| GitHub org | 50 repos, ~10k files + 5k issues/PRs | weighted 50 KB | 750 MB |
| Google Drive (one team) | 10 k mixed files | weighted 60 KB | 600 MB |
| Dex CRM | 5 k contacts + 1 k orgs + 20 k relationships | 0.2 KB row | 5 MB + Neo4j |
| M365 email headers (1 yr) | 500 k bundles | 7.5 KB | 3.75 GB |
| M365 calendar (1 yr) | 50 k events | 8.2 KB | 410 MB |
| **Total** | | | **~13.5 GB SQLite + vector store** |

**Implication**: a typical engagement-scope deployment lands at
10–20 GB SQLite + vectors, before Bronze raw retention. Bronze adds
roughly the raw item-size sum (~7 GB at this profile) if retained
full; the operator config defaults retain Bronze for 30 days and prune.

**Recommendation**: SQLite + usearch are fine to ~50 GB engagement
scope; beyond that (or for firm-scope across many engagements per
ADR-017) Postgres + pgvector / OpenSearch becomes the right tier.
Document this as the deployment-tier boundary in the ADR.

---

## Indexing latency budgets

### Per-item conversion cost (CPU-bound, single-threaded)

| Conversion | Cost range | Source |
|---|---:|---|
| Markdown passthrough | ~5 ms | est |
| Markdown via markitdown | ~15 ms | est |
| Docx via python-docx | 50–200 ms | obs (Wave 4) |
| Xlsx via openpyxl | 100 ms – 2 s (workbook-size dependent) | obs |
| Pptx via python-pptx | 200–800 ms | obs |
| Pdf via pdfplumber | 100 ms – 5 s (page count × text density) | obs |
| Pdf via OCR (Tesseract) | 2 – 30 s/page | obs |
| Notion block-tree → markdown | ~10 ms + (~3 req/s API) | est |
| Slack message → text | ~2 ms | est |
| GitHub source file → text | ~5 ms (or clone overhead amortised) | est |
| Google Docs markdown export | ~50 ms + 1 API call | est |

### Embedding cost

| Embedding | Latency per chunk | Source |
|---|---:|---|
| sentence-transformers all-MiniLM (CPU) | ~10 ms | obs |
| OpenAI text-embedding-3-small (cloud, batched) | ~50 ms / batch of 100 → 0.5 ms/chunk | obs |
| Azure OpenAI embeddings (operator-deployed) | ~150 ms / batch of 100 → 1.5 ms/chunk | obs |

Indexing throughput target: **≥10 chunks/sec sustained per worker**
(after backfill warm-up). At the storage profile above, full backfill
of a typical deployment lands in single-digit hours; per-day
incremental ingest in single-digit minutes.

### Initial-backfill envelope (per source)

| Source | 1k items | 10k items | 100k items | Rate-limited? |
|---|---:|---:|---:|:---:|
| Obsidian | < 1 min | ~3 min | ~30 min | No (filesystem) |
| SharePoint (delta, throttled) | ~5 min | ~45 min | ~6 h | Yes (10k/10min) |
| Notion (3 req/s) | ~10 min | ~1.5 h | ~14 h | Yes (3 req/s) |
| Slack (Tier 3 50/min) | ~5 min | ~45 min | ~7 h | Yes (50 req/min) |
| GitHub (PAT 5k/h) | ~5 min | ~30 min | ~5 h | Yes (5k/h) |
| Drive (1k/100s/user) | ~3 min | ~30 min | ~5 h | Yes |
| Dex (self-rate-limited 1/s) | ~17 min | ~3 h | ~28 h | Self |
| M365 email (delta) | ~3 min | ~30 min | ~5 h | Yes |

**Implication**: initial-backfill is the highest-pressure window for
rate limits. The ConnectorPipeline's per-container concurrency cap
(Simulation 12) is operator-tunable per connector with sane defaults
sized to these envelopes. Defaults below.

---

## Freshness SLOs

Operator-realistic freshness targets per source:

| Source | Push lag (when push available) | Poll lag (push-unavailable) | Operator-config default |
|---|---:|---:|---:|
| Obsidian | < 1 s | 60 s reconcile-every-10 | Push always-on |
| SharePoint | seconds–minutes | 5 min active / 60 min cold | Push + 5-min poll |
| Notion | seconds–minutes (beta) | 5 min | Push when GA + 5-min poll |
| Slack | seconds | 5 min active | Push + 5-min poll |
| GitHub | seconds | 1 min active | Push + 1-min poll |
| Google Drive | seconds (channel <7 d) | 1–5 min per Shared Drive | Push + 5-min poll |
| Dex CRM | (no push) | 1 h (Dex API has no realtime) | Poll-only 1-h cadence |
| M365 email/cal | seconds | 5 min | Push + 5-min poll |

These map directly into the connector-instance config's
`freshness_strategy` block (see Simulation 11).

### Freshness envelope reporting

The result envelope MUST carry per-source freshness:

```
result.freshness:
  - source: sharepoint-corp
    last_synced_at: 2026-05-23T14:32:00Z
    age_seconds: 240
    state: "fresh"  # fresh / stale / access-revoked / not-yet-synced
  - source: dex-crm-personal
    last_synced_at: 2026-05-23T13:30:00Z
    age_seconds: 3840
    state: "fresh"
  - source: notion-acme
    last_synced_at: 2026-05-23T11:00:00Z
    age_seconds: 12840
    state: "stale"  # > 2× operator's configured cadence
```

Agents acting on retrieval results can decide whether to escalate
("the SharePoint data is 4 minutes old, that's fine" vs "the Notion
data is 3.5 hours old, that exceeds the freshness budget for SoW
preparation — request operator action").

---

## Rate-limit budgets + concurrency caps

Default per-connector concurrency caps (operator-tunable):

| Source | Concurrent containers | Rationale |
|---|---:|---|
| Obsidian | 1 | Filesystem; no benefit from concurrency for one vault |
| SharePoint | 4 | 10k/10min budget; 4 concurrent drives at 5-min poll = ~1200 req across drives = comfortable |
| Notion | 1 | 3 req/s ceiling shared across integration |
| Slack | 2 | 50 req/min Tier 3 ceiling |
| GitHub | 4 | 5k/h budget; 4 concurrent repos at 1-min poll = manageable |
| Google Drive | 4 | 1k/100s/user; 4 concurrent users at 5-min poll = comfortable |
| Dex CRM | 1 | 1 req/s self-imposed |
| M365 email/cal | 4 | Throttling budget shared across mailboxes; 4 concurrent OK |

These default to the values above; operator can lower for cost-
constrained deploys or raise for backfill bursts (kairix surfaces a
`kairix backfill` mode that temporarily raises caps).

### Burst handling

Every connector MUST honour `Retry-After` headers (where the source
provides them) and fall back to a token-bucket implementation per
container shared across worker threads. The framework's
`run_batch` per-container path is responsible; per-connector
implementations only need to surface the rate-limit signal via
`ContainerTransient(retry_after)`.

---

## Document conversion cost — backpressure

For office docs (docx/xlsx/pptx/pdf), conversion CPU cost can dwarf
embed cost. Pptx + pdf in particular can pin a worker thread for
seconds.

**Mitigation**: extractor invocation runs in a separate thread pool
from the connector list-changes loop:

```
Connector → [list_changes queue] → ExtractorPool (CPU-bound, e.g. cpu_count threads) → [chunked-items queue] → EmbedPool → ChunkRouter
```

This decouples I/O-bound list/fetch from CPU-bound extract from
network-bound embed. Operator can size each pool independently.

**Conversion budget per worker pod**:
- Conservative: 1 ExtractorPool thread per CPU core, embedding via
  batched cloud calls (no GPU needed).
- Aggressive: 2× CPU on ExtractorPool, embedding via local
  sentence-transformers on GPU (deploy-shape-specific).

Default to conservative; operator opts-in to aggressive.

---

## Cost envelope (embedding + storage)

Rough per-deployment cost at the storage profile above:

| Item | Cost | Source |
|---|---:|---|
| ~13 GB SQLite + vector storage | ~$0 (local disk) | n/a |
| Embedding generation (Azure deployed) | ~$15 per million tokens; 13 GB / 4 bytes/token = ~3 G tokens; ~$45 one-time + delta | est |
| Embedding refresh (re-extract on extractor version bump) | 10% per year ≈ ~$5/year | est |
| Document conversion (CPU only, no cloud) | ~$0 marginal (use existing VM) | n/a |
| Neo4j (community, on-VM) | ~$0 license + VM CPU | n/a |

Implication: a typical engagement is single-digit dollars in embedding
cost to backfill, dominated by office-doc and email volumes.

---

## Failure-mode timing budgets

| Failure | Detection latency | Recovery latency | Operator-visible? |
|---|---:|---:|:---:|
| Subscription expiry | 0 (renewer pre-empts) | n/a | No (silent renew) |
| Subscription revoked | next poll detects | next sync re-establishes (or surfaces grant required) | Yes (access-revoked in envelope) |
| Rate limit hit | immediate (Retry-After) | per Retry-After | No (silent retry) |
| Connector raise (per-item) | immediate | per-item dead_letter | Yes (deadletter count) |
| Source-side outage | next poll detects | resumes when source returns | Yes (stale envelope state) |
| SQLite write failure | immediate | rollback + retry | Yes (worker logs) |
| Vector backend backpressure | per-batch latency | adaptive batch sizing | No (transparent) |

---

## Topology overhead vs current code

| Concern | Current cost | Under proposed topology | Delta |
|---|---|---|---|
| Connector enumeration | 1 connector_cursors row per connector | N rows per connector (one per container) | +N rows × ~100 B = O(MB) for large deployments |
| Collection routing | bypass (direct chunk write) | CollectionRouter lookup per item | ~5 µs per item (in-process fnmatch) |
| Scope-profile resolution | none | resolve per search call | ~50 µs per query (dict lookup) |
| Skill resolution | none | resolve per skill call | ~100 µs per skill invocation |
| Freshness envelope | none | per-source dict + serialise | ~1 ms per result envelope |

Total: search-path overhead is sub-millisecond per query — well
within the existing P50 retrieval budget of ~1.7 s (observed on
dogfood VM). Ingest-path overhead is per-item negligible.

---

## What this drives in the ADR

The non-functional envelopes commit the ADR to:

1. **Connector concurrency caps** as operator-tunable config with
   defaults sized to source rate limits (table above).
2. **ExtractorPool / EmbedPool separation** as the pipeline shape.
3. **Freshness envelope in every result** (per-source last_synced + state).
4. **Bronze retention default 30 days** with prune cadence (storage
   bound).
5. **Embedding budget transparency** — kairix surfaces estimated cost
   per backfill via `kairix benchmark cost-estimate` (new sub-command).
6. **Tier boundary**: SQLite + usearch ≤ 50 GB engagement; Postgres +
   pgvector above (per ADR-017).
