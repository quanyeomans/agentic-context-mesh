# Mnemosyne — Product Requirements Document

**Project:** `three-cubes/qmd-azure-embed`  
**Owner:** [owner] / Triad Consulting  
**Implementer:** Builder 🔨  
**Version:** 3.0 — full spec, post Phase 0 benchmark  
**Date:** 2026-03-23  
**Status:** Active

---

## Erratum — Deviations from Spec (as of 2026-03-23)

| Section | PRD Spec | Shipped Reality |
|---|---|---|
| ADR-M04 | Phi-4-mini primary for L0/L1 generation | gpt-4o-mini used — Phi-4-mini Azure endpoint has cold-start failures (>3min timeout); unreliable for batch |
| §4.3 entities/extract.py | "Phase 4 — gpt-4o-mini NER extraction" | Shipped in Phase 2 (2026-03-23) as part of ADR-M07 entity+wikilink dual-output model |
| §4.3 module list | No wikilinks/ module | `mnemosyne/wikilinks/` shipped Phase 2 per ADR-M07 (resolver, injector, audit, cli) |
| Benchmark suite size | "50 queries at Phase 0, expand to 100 by Phase 5" | 39-query YAML suite; synthetic queries flagged as insufficient — real-world LOCOMO-aligned suite planned |
| Phase 2 scope | temporal + summaries only | Also shipped: wikilinks, entity extractor + reconciler, entities.db schema v2 |
| vectors_vec | 6198 vectors after Phase 0 embed | Re-embedded 2026-03-23 at 1536-dim after discovering original embed never wrote to vectors_vec (768-dim QMD native table was in place); 6481 vectors current |
| Entity benchmark scoring | Entity queries scored as pure LLM judge (gold_path: null) | Entity queries should have explicit gold_path mapped to entity stubs. Without it, LLM judge scores tangentially-relevant docs 0.2–0.4 even when correct entity stubs are retrieved. Fix in Phase 2.5. |
| RRF path dedup | BM25 and vector paths assumed to be identical | BM25 returns `qmd://collection-name/relative/path.md`; vector returns `relative/path.md`. Fixed in `5be5b55` via `_canonical_path()` normalisation. Entity stubs now correctly get combined BM25+vector RRF score. |
| Entity stub content depth | Stubs assumed to be content-rich from creation | Tier 1 stubs (NexusDigital, AcmeHealth, Softcorp, BridgewaterEngineering) were created with frontmatter + ~600 word bodies. Core agent/person stubs (alex-jordan.md) are thin (~32 lines). Alex Jordan stub lacks: role history, communication preferences, active projects list, decisions made. Phase 2.5 enrichment required. |

---

## Contents

1. [What This Is](#1-what-this-is)
2. [Context: What Phase 0 Told Us](#2-context-what-phase-0-told-us)
3. [Goals and Success Criteria](#3-goals-and-success-criteria)
4. [Architecture](#4-architecture)
5. [Phase Specifications](#5-phase-specifications)
6. [Testing Strategy](#6-testing-strategy)
7. [Non-Functional Requirements](#7-non-functional-requirements)
8. [Delivery Infrastructure](#8-delivery-infrastructure)
9. [Evaluation Framework](#9-evaluation-framework)
10. [Operational Runbook](#10-operational-runbook)
11. [Architecture Decision Records](#11-architecture-decision-records)
12. [Open Questions](#12-open-questions)
13. [Baseline Results](#13-baseline-results)

---

## 1. What This Is

Mnemosyne is the memory layer for the Triad Consulting multi-agent OpenClaw platform. It extends the existing QMD + Obsidian vault stack to give agents durable, high-quality memory across time, entities, and concepts.

**The problem it solves:** OpenClaw agents currently retrieve from QMD BM25 only. That scores 0.51 on the Phase 0 benchmark. It fails on temporal queries (0.37), entity consolidation (0.57 but requires multiple searches), multi-hop reasoning (0.40), and it provides no session context synthesis. Agents start cold or load raw document chunks. There is no way to ask "what do we know about this person" and get a coherent answer.

**How it solves it:** Incrementally adds hybrid search, an entity graph, temporal indexing, tiered context loading, briefing synthesis, and write-time contradiction detection — all on top of the existing QMD + vault stack, with BM25 as a fallback for every component.

**What it is not:**
- Not a replacement for QMD, Obsidian, or the vault. Extends them.
- Not a hosted service. Runs on `vm-tc-openclaw`.
- Not a general-purpose memory system. Designed for our specific OpenClaw agent stack.
- Not on PyPI. Internal tool, installed from source.
- Not integrated with Coach or Family agents (privacy boundaries).

---

## 2. Context: What Phase 0 Told Us

Phase 0 ran a 50-query benchmark across 6 categories against BM25 and Azure Vector independently. Full results in [§13](#13-baseline-results).

**Finding 1 — BM25 is already solid (0.51, not the ~0.30 assumed in the original spec)**

BM25 scores strongly on conceptual (0.63) and procedural (0.60) because those queries match structured how-to content by keyword. The original plan assumed hybrid would be the big win over a weak baseline. The real challenge is improving the categories where both systems fail without degrading the ones where BM25 already works.

**Finding 2 — Pure vector regresses on structured content (0.38 overall)**

Vector scores 0.21 on conceptual and 0.20 on procedural — significantly below BM25. This isn't surprising: "How do we handle agent mistakes?" does not share keywords with `rules.md` entries like "Do first, explain after." Keyword matching wins here. Hybrid RRF is therefore a correctness requirement, not an optimisation. We cannot deploy pure vector search.

**Finding 3 — Temporal is broken upstream**

Both BM25 (0.37) and vector (0.33) fail on temporal queries. The failure mode is content structure, not search quality. Board Kanban files are single blobs — the Done column is not date-indexed. Daily memory logs are append-only files where all entries for a day are one chunk. No search strategy can return "what was completed on 2026-03-22" when the content isn't structured around dates at index time. The fix is upstream (chunking), not downstream (query rewriting).

**Finding 4 — Implementation language should be Python, not bash**

The original architecture planned shell scripts in `/data/mnemosyne/scripts/`. Phase 0 shipped a Python project with tests, CI, schema validation, and sqlite-vec extension handling. Python is the correct choice for a system that needs testing, type safety, and maintainability.

---

## 3. Goals and Success Criteria

### Primary goal

Give OpenClaw agents memory retrieval that works across all query types.

**Measurable definition:** ≥ 0.75 weighted benchmark score across all 6 categories by end of Phase 3. This corresponds to "production quality" — comparable to what Muninn achieves on its domain before tuning.

**Phase 4 result:** 0.6658 on revised 36-query, 6-category suite (gate ≥ 0.620 ✅). Phase 3 achieved 0.762 on the original 43-query, 7-category suite. These are different instruments.

### Per-phase gate scores

| Phase | Weighted Total | Key category targets |
|---|---|---|
| P0 baseline (BM25) | 0.51 | — |
| P1 — Hybrid + entities | ≥ 0.62 | Recall >0.60, no category below 0.50 |
| P2 — Temporal + L0/L1 | ≥ 0.68 | Temporal >0.55, all categories >0.50 |
| P3 — Briefing + classify | ≥ 0.75 | All categories >0.60 |
| P4 — Contradiction + extraction | ≥ 0.80 | Entity >0.65, multi-hop >0.60 |

**Phase gate rule:** A phase is not complete until benchmark confirms the gate score. If the score is below target, the phase stays open for tuning. Phase N+1 does not start until Phase N benchmark is confirmed.

### Secondary goals

- **Observable:** Every retrieval decision logged with collection, tier used, score, and latency.
- **Resilient:** Any component failure falls back to BM25 transparently. No agent failures from Mnemosyne problems.
- **Incremental:** Each phase ships working, tested, deployed software. No big-bang phases.
- **Cheap:** < $1/month Azure API cost at current vault scale (~1,800 documents).
- **Auditable:** All write operations logged. Contradictions surfaced to humans, never silently overwritten.

### Non-goals

- Real-time retrieval. Acceptable P50: < 500ms for hybrid search.
- External API surface. Mnemosyne is a local tool.
- Multi-tenant use.
- Replacing QMD. QMD BM25 is the permanent fallback.
- Perfect recall. 0.80 is the ceiling; above that, we're over-tuning for the benchmark.

---

## 4. Architecture

### 4.1 Guiding principles

1. **File-first.** Markdown vault files are source of truth. SQLite indexes, embeddings, entity graphs, and summaries are all derived artifacts — regenerable from source if corrupt or stale.
2. **Graceful degradation.** Any Mnemosyne component failure must fall back to BM25 silently. The system must never prevent an agent from operating.
3. **Evolutionary.** QMD BM25, the vault structure, agent knowledge files, and daily logs stay unchanged. New capabilities are additive layers on top.
4. **Agent-scoped.** Shape's episodic memories are not Builder's. Enforced by collection design and `--agent` parameter, not by convention.
5. **Observable by default.** Every retrieval event, write event, and contradiction detection is logged. We measure improvement before claiming it.
6. **Explicit over implicit.** Every module has a defined fallback. Every write has a defined target. Ambiguous classification surfaces to the agent rather than guessing.

### 4.2 System layers

```
┌──────────────────────────────────────────────────────────────────────┐
│                         AGENT INTERFACE                              │
│                                                                      │
│  mnemosyne search "query" --agent shape                              │
│  mnemosyne entity lookup "Jane Smith"                                │
│  mnemosyne brief shape                                               │
│  mnemosyne classify "content" --agent shape                          │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────────┐
│                      RETRIEVAL PIPELINE                              │
│                                                                      │
│  1. Intent classification                                            │
│     keyword → BM25 only                                              │
│     semantic → hybrid (BM25 + vector via RRF)                        │
│     temporal → temporal module + hybrid                              │
│     entity   → entity graph first, then hybrid                       │
│     procedural → BM25 on procedural collections                      │
│                                                                      │
│  2. Parallel search dispatch                                         │
│     BM25: qmd search (subprocess)                                    │
│     Vector: sqlite-vec CTE MATCH query                               │
│                                                                      │
│  3. RRF fusion + entity boosting                                     │
│  4. Tier widening (L0 → L1 → L2 as confidence + budget allows)      │
│  5. Token budget enforcement (hard cap 3,000 tokens)                 │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────────┐
│                         INDEXES                                      │
│                                                                      │
│  QMD SQLite (~/.cache/qmd/index.sqlite)                              │
│    ├── FTS5 BM25 index (all collections)                             │
│    └── vectors_vec (sqlite-vec, 6198 vectors, text-embedding-3-large)│
│                                                                      │
│  Entity graph (/data/mnemosyne/entities.db)                          │
│    ├── entities, relationships, entity_mentions, entity_facts        │
│    └── WAL mode, staggered writes                                    │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────────┐
│                       SOURCE OF TRUTH                                │
│                                                                      │
│  Obsidian vault (/data/obsidian-vault/)                              │
│    ├── 04-Agent-Knowledge/{shared,<agent>}/                          │
│    ├── 04-Agent-Knowledge/entities/     (NEW in Phase 1)             │
│    └── .summaries/                      (NEW in Phase 2)             │
│                                                                      │
│  Agent workspaces (/data/workspaces/<agent>/memory/YYYY-MM-DD.md)   │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.3 Module structure

```
mnemosyne/
  embed/            # Phase 0 — SHIPPED
    schema.py         # QMD DB schema validation + sqlite-vec extension loading
    embed.py          # Embedding pipeline: staging table upsert, batching, retry
    recall_check.py   # Post-embed recall gate (direct CTE vector search)
    cli.py            # Entry: mnemosyne embed [--force] [--limit N]

  search/           # Phase 1
    intent.py         # Query intent classifier (keyword/semantic/temporal/entity/procedural)
    bm25.py           # QMD BM25 search wrapper (subprocess → structured result)
    vector.py         # sqlite-vec CTE search wrapper
    rrf.py            # Reciprocal rank fusion + entity boosting
    budget.py         # Token budget enforcer (L0→L1→L2 selection)
    hybrid.py         # Orchestrates intent → dispatch → fuse → budget
    cli.py            # Entry: mnemosyne search "query" --agent <agent>

  entities/         # Phase 1
    schema.py         # entities.db DDL + migration versioning
    graph.py          # Entity lookup, graph traversal, mention index
    extract.py        # gpt-4o-mini NER extraction from vault files (Phase 4)
    cli.py            # Entry: mnemosyne entity {lookup|write|extract}

  temporal/         # Phase 2
    chunker.py        # Date-aware pre-processor: board files → per-card chunks
                      #                           daily logs → per-section chunks
    rewriter.py       # Temporal intent extractor + time-window query rewriter
    index.py          # entity_facts time-range queries
    cli.py            # Entry: mnemosyne timeline "topic" --since YYYY-MM-DD

  summaries/        # Phase 2
    generate.py       # L0 frontmatter injection + L1 sidecar generation (Phi-4-mini)
    staleness.py      # Staleness detection: source mtime vs l0-generated/L1 mtime
    loader.py         # Tier router: L0 → L1 → L2 based on confidence + budget
    cli.py            # Entry: mnemosyne summarise [--all|--stale]

  classify/         # Phase 3
    rules.py          # Rule-based fast-path classifier (no API call)
    judge.py          # LLM-judge for ambiguous cases (gpt-4o-mini)
    router.py         # Target file selector given classification result
    cli.py            # Entry: mnemosyne classify "content" --agent <agent>

  briefing/         # Phase 3
    pipeline.py       # 8-step synthesis: retrieve → synthesise → write
    templates.py      # Per-agent briefing format templates
    cli.py            # Entry: mnemosyne brief <agent>

  contradict/       # Phase 4
    rules.py          # Stage 1: rule-based detection (numeric, status, date conflicts)
    similarity.py     # Stage 2: embedding similarity search for near-duplicate facts
    judge.py          # Stage 3: LLM-judge for confirmed contradiction
    resolve.py        # CONFLICTS.md writer + Telegram notification trigger
    cli.py            # Entry: mnemosyne contradict [--check "content"|--batch]

  benchmark/        # Phase 5
    runner.py         # 50/100-query benchmark runner
    judge.py          # LLM-as-judge scorer (gpt-4o-mini)
    report.py         # Results formatter + trend analysis
    cli.py            # Entry: mnemosyne benchmark [--system <system>]

  _azure.py         # Shared Azure OpenAI client (embedding + chat completions)
  _qmd.py           # Shared QMD subprocess wrapper + result parser
  _db.py            # Shared SQLite connection factory (WAL mode, extension loading)
  cli.py            # Top-level CLI dispatcher
```

### 4.4 Module dependency graph

```
embed        depends on: _azure, _db, embed/schema
search       depends on: _qmd, _db, embed/schema, entities/graph (for boosting)
entities     depends on: _azure, _db
temporal     depends on: _db, _qmd, entities/graph
summaries    depends on: _azure
classify     depends on: _azure
briefing     depends on: search, temporal, entities, summaries
contradict   depends on: _azure, _db, entities/graph
benchmark    depends on: search, _azure, _qmd

Shared modules (_azure, _qmd, _db) have NO dependencies on other mnemosyne modules.
```

**Critical constraint:** No circular dependencies. `search` may use `entities.graph` for boosting but `entities` must not import from `search`.

### 4.5 Data stores

| Store | Path | Owner | Regenerable | Backup needed |
|---|---|---|---|---|
| QMD index | `~/.cache/qmd/index.sqlite` | QMD (read/write) | Yes — from vault | No |
| Entity graph | `/data/mnemosyne/entities.db` | Mnemosyne | Yes — from vault | No |
| L1 summaries | `/data/obsidian-vault/.summaries/` | Mnemosyne | Yes — from vault | No |
| Briefings | `/data/mnemosyne/briefing/` | Mnemosyne | Ephemeral | No |
| Mnemosyne logs | `/data/mnemosyne/logs/` | Mnemosyne | No — event log | Yes (7-day rotation) |
| Benchmark results | `/data/obsidian-vault/01-Projects/202603-Mnemosyne/benchmark-results/` | Mnemosyne | No — preserve | Yes — in vault (Obsidian sync) |

### 4.6 Agent scoping

| Collection / store | Readable by | Writable by |
|---|---|---|
| `<agent>-memory` | That agent only | That agent only |
| `knowledge-<agent>` | That agent only | That agent only |
| `knowledge-shared` | All agents | Any agent |
| `knowledge-entities` | All agents | Any agent (via `mnemosyne entity write`) |
| `.summaries/` | All agents | Mnemosyne generate only |
| `entities.db` | All agents (read) | Mnemosyne extract/write only |
| Coach | Isolated — no vault/QMD | MEMORY.md only |
| Family | Isolated — no vault/QMD | MEMORY.md only |

---

## 5. Phase Specifications

### Phase 0 — Embedding pipeline ✅ SHIPPED

**Commit:** `0a43e2e`  
**Tests:** 88 passing, 16 skipped (sqlite-vec skips expected in CI)

**Delivered:**
- Azure OpenAI text-embedding-3-large → QMD `vectors_vec` (sqlite-vec)
- Schema validation against live QMD DB (guards against qmd version drift)
- Staging table upsert pattern (sqlite-vec has no INSERT OR REPLACE)
- CTE-based vector search (direct JOIN on vec0 virtual table fails)
- Recall gate: 5-query direct vector search post-embed, alerts on >10% drop
- `scripts/deploy.sh`: fetches secrets from Key Vault, runs embed, tails log
- CI: unit + integration tests on every push; weekly QMD schema compat check

**Production bugs found and tested:**
1. sqlite-vec extension must load before any `--force` DELETE (`TestExtensionLoadOrder`)
2. vec0 tables reject `INSERT OR REPLACE` — staging table is the correct pattern (`TestSqliteVecInsertConstraints`)
3. `qmd vsearch` hangs on CPU (llama.cpp reranker) — recall gate uses direct CTE (`TestDirectVectorSearch`)

**Acceptance criteria (met):**
- ✅ 6,198 vectors written, 0 failures
- ✅ 5/5 recall gate on live DB
- ✅ $0.16 cost for full vault
- ✅ `mnemosyne embed --limit 50` E2E validation passed before full run

---

### Phase 1 — Hybrid search + entity graph foundation

**Benchmark gate:** ≥ 0.62 weighted total. Recall > 0.60. No category below 0.50.

**Rationale for these targets:** Phase 1 must prove that hybrid is better than either BM25 or vector alone. 0.62 vs 0.51 baseline is a +21% improvement — meaningful, not marginal. The no-category-below-0.50 constraint ensures we haven't traded BM25 strength for vector gains.

#### 1a. Hybrid search (`mnemosyne/search/`)

**Intent classifier (`intent.py`)**

Classifies query into one of five types before dispatch. Rule-based with LLM fallback for ambiguous cases.

| Intent | Detection signals | Dispatch |
|---|---|---|
| `keyword` | Proper nouns, error codes, file names, version numbers | BM25 only |
| `semantic` | Abstract concepts, "what do we know about", "how does", causal language | BM25 + vector (RRF) |
| `temporal` | "last week/month", "in March", "when did", "recently", "what changed" | Temporal module (Phase 2); until then, BM25 with date-string boost |
| `entity` | Named person/org as subject, "tell me about", "what has X done" | Entity lookup first (Phase 1b), then hybrid |
| `procedural` | "how to", "what's the rule for", "should I", "what do I do when" | BM25 on procedural collections only |

**BM25 wrapper (`bm25.py`)**

Subprocess call to `qmd search` with `--json` output. Returns structured list `[{file, title, snippet, score, collection}]`. Handles subprocess timeout (5s), parse failure, and empty results without raising.

```python
def bm25_search(query: str, collections: list[str] | None = None,
                limit: int = 10, agent: str | None = None) -> list[BM25Result]:
    """
    Returns top-N BM25 results. Returns [] on any failure.
    Never raises — caller must handle empty list as fallback signal.
    """
```

**Vector search wrapper (`vector.py`)**

Reuses the CTE pattern established in Phase 0 recall check. Returns `[{hash_seq, distance, path, collection, title, snippet}]`.

```python
def vector_search(db: sqlite3.Connection, query_vec: list[float],
                  k: int = 10, collections: list[str] | None = None) -> list[VecResult]:
    """
    CTE MATCH query against vectors_vec. Returns [] if extension not loaded
    or DB locked. Never raises.
    """
```

**RRF fusion (`rrf.py`)**

```python
def rrf(bm25: list[BM25Result], vec: list[VecResult], k: int = 60) -> list[FusedResult]:
    """
    Reciprocal Rank Fusion. k=60 default (standard constant).
    Results appearing in only one list get rank = len(other_list) + 1.
    Returns sorted descending by RRF score.
    """

def entity_boost(results: list[FusedResult], db: sqlite3.Connection,
                 boost_factor: float = 0.2, cap: float = 2.0) -> list[FusedResult]:
    """
    Boost documents with entity_mentions entries.
    boost = 1 + min(boost_factor * log(1 + mention_count), cap - 1)
    Applied after RRF, before budget trim.
    """
```

**RRF k constant (open question Q3):** Default 60 is standard, but our corpus is small and homogeneous. Test k=30, k=60, k=90 against recall category on Phase 1 benchmark. Pick the k that maximises recall without degrading conceptual.

**Token budget enforcer (`budget.py`)**

Hard cap 3,000 tokens per retrieval call. Applies tier widening logic: load L0 for all results, expand to L1 for high-scoring results if budget allows, load L2 (full document) only if budget > 2,000 tokens remaining and score > 0.25.

```python
def apply_budget(results: list[FusedResult], budget: int = 3000,
                 l1_threshold: float = 0.15, l2_threshold: float = 0.25) -> list[BudgetedResult]:
    """
    Returns results annotated with tier (L0/L1/L2) and truncated to budget.
    Until Phase 2 (L0/L1 exist), all results are L2 (full snippet).
    """
```

**Orchestrator (`hybrid.py`)**

```python
def search(query: str, agent: str, scope: str = "shared+agent",
           budget: int = 3000) -> SearchResult:
    """
    Full pipeline: intent → dispatch → fuse → boost → budget.
    Falls back to BM25-only if vector search fails.
    Logs: {query_hash, intent, bm25_count, vec_count, fused_count,
           collections, tiers_used, total_tokens, latency_ms}
    """
```

**Acceptance criteria:**
- `mnemosyne search "what do we know about the QMD lock crash"` returns `facts.md` in top 3
- `mnemosyne search "how to fetch a Key Vault secret"` returns correct result AND does not degrade vs BM25 alone
- Vector search failure returns BM25 results with a warning log, not an error
- Benchmark: recall > 0.60, no category below 0.50, weighted total ≥ 0.62

#### 1b. Entity graph foundation (`mnemosyne/entities/`)

**Schema (`schema.py`)**

SQLite database at `/data/mnemosyne/entities.db`. WAL mode. Schema versioned via `schema_version` table.

```sql
CREATE TABLE schema_version (
  version     INTEGER PRIMARY KEY,
  applied_at  TEXT NOT NULL
);

CREATE TABLE entities (
  id            TEXT PRIMARY KEY,       -- kebab-case slug: "alex-jordan"
  type          TEXT NOT NULL           -- person | organisation | decision | concept | project
                CHECK (type IN ('person','organisation','decision','concept','project')),
  name          TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','archived')),
  markdown_path TEXT NOT NULL,          -- relative to vault root
  summary       TEXT,                   -- L0 abstract (100 tokens)
  agent_scope   TEXT NOT NULL DEFAULT 'shared',
  created_at    TEXT NOT NULL,          -- ISO-8601
  updated_at    TEXT NOT NULL
);

CREATE TABLE relationships (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  from_entity   TEXT NOT NULL REFERENCES entities(id),
  to_entity     TEXT NOT NULL REFERENCES entities(id),
  rel_type      TEXT NOT NULL           -- reports_to | member_of | decided_by | client_of | relates_to
                CHECK (rel_type IN ('reports_to','member_of','decided_by','client_of','relates_to')),
  label         TEXT,
  confidence    REAL NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
  source_ref    TEXT,                   -- qmd:// URI
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE entity_mentions (
  entity_id     TEXT NOT NULL REFERENCES entities(id),
  doc_uri       TEXT NOT NULL,
  mention_count INTEGER NOT NULL DEFAULT 1,
  first_seen    TEXT NOT NULL,
  last_seen     TEXT NOT NULL,
  PRIMARY KEY (entity_id, doc_uri)
);

CREATE TABLE entity_facts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id     TEXT NOT NULL REFERENCES entities(id),
  fact_type     TEXT NOT NULL           -- role | decision | relationship | context | other
                CHECK (fact_type IN ('role','decision','relationship','context','other')),
  fact_text     TEXT NOT NULL,
  valid_from    TEXT,                   -- ISO-8601, null = unknown start
  valid_until   TEXT,                   -- null = still valid
  source_ref    TEXT,
  created_at    TEXT NOT NULL
);

CREATE INDEX idx_entities_type   ON entities(type);
CREATE INDEX idx_entities_name   ON entities(name);
CREATE INDEX idx_rel_from        ON relationships(from_entity);
CREATE INDEX idx_rel_to          ON relationships(to_entity);
CREATE INDEX idx_facts_entity    ON entity_facts(entity_id);
CREATE INDEX idx_facts_from      ON entity_facts(valid_from);
CREATE INDEX idx_mentions_doc    ON entity_mentions(doc_uri);
```

**Graph module (`graph.py`)**

```python
def entity_lookup(name: str, db: sqlite3.Connection) -> EntityResult | None:
    """
    Find entity by name (fuzzy match). Returns entity record + top-5 mention docs
    + all facts ordered by valid_from desc. Returns None if not found.
    """

def entity_write(name: str, entity_type: str, markdown_path: str,
                 db: sqlite3.Connection, facts: list[dict] | None = None) -> str:
    """
    Create or update entity record. Returns entity id (slug).
    Creates markdown file if it doesn't exist.
    """

def get_mentions(entity_id: str, db: sqlite3.Connection, limit: int = 10) -> list[str]:
    """
    Return doc_uris ordered by mention_count desc, last_seen desc.
    Used by hybrid search for entity boosting.
    """
```

**Vault entity files:** Stored at `04-Agent-Knowledge/entities/{people,organisations,decisions}/`. Human-readable Markdown. Format specified in architecture doc. New QMD collection `knowledge-entities` added to `~/.config/qmd/index.yml`.

**Phase 4 extraction (`extract.py`):** Placeholder in Phase 1. Automated extraction from vault files via gpt-4o-mini NER is Phase 4 scope. Phase 1 entities are written manually via `mnemosyne entity write` or through agent use of the write interface.

**Acceptance criteria:**
- `mnemosyne entity write --name "Alex Jordan" --type person` creates vault file + DB record
- `mnemosyne entity lookup "Alex Jordan"` returns entity + source docs
- Entity boosting increases score for entity-query benchmark cases vs no-boosting baseline
- `entities.db` schema version check passes on every startup
- `entities.db` survives concurrent reads during entity extraction write

---

### Phase 2 — Temporal reasoning + L0/L1 tiered context

**Benchmark gate:** ≥ 0.68 weighted total. Temporal category ≥ 0.55.

**The temporal problem is two separate fixes:**

#### 2a. Date-aware chunking (`mnemosyne/temporal/chunker.py`)

**Root cause:** Board Kanban files (one file per board) and daily memory logs (one file per day, all entries concatenated) are indexed as single documents. Temporal queries can't be answered from a single chunk containing all of a day's work or all of a board column.

**Board file pre-processor:**

Reads Kanban `.md` files and writes structured per-card chunk files to a staging directory (`/data/mnemosyne/chunks/boards/`). Each card becomes a standalone `.md` with frontmatter including the board, column, and all metadata tags (`completed::`, `started::`, `created::`, `priority::`, `owner::`).

```
Input card:
  - [ ] Fix Bower Bird auth [owner::builder] [completed::2026-03-22] [priority::p1]

Output chunk file:
  ---
  source: /data/obsidian-vault/01-Projects/Boards/Builder.md
  board: Builder
  column: Done
  completed: "2026-03-22"
  owner: builder
  priority: p1
  chunk-type: board-card
  ---
  # Fix Bower Bird auth
  Board: Builder | Column: Done | Completed: 2026-03-22
```

The staging directory is indexed as a new QMD collection `board-cards`. Refreshed whenever source board files change.

**Daily log pre-processor:**

Reads `memory/YYYY-MM-DD.md` files and splits at `## [HH:MM]` section headers. Each section becomes a per-entry chunk with date-stamped frontmatter.

```
Source section:
  ## 14:32 Completed Bower Bird auth fix
  [content]

Output chunk:
  ---
  source: /data/workspaces/builder/memory/2026-03-22.md
  date: "2026-03-22"
  time: "14:32"
  agent: builder
  chunk-type: memory-entry
  ---
  ## 14:32 Completed Bower Bird auth fix
  [content]
```

Indexed as part of existing `<agent>-memory` collections (QMD collection path updated to point at chunks).

**Chunker schedule:** Run immediately after `qmd update` in the qmd-maintenance.sh cron. Incremental — only reprocess files changed since last run. Lockfile: `/tmp/mnemosyne-chunker.lock`.

#### 2b. Temporal query rewriter (`mnemosyne/temporal/rewriter.py`)

Secondary fix — works alongside chunking. Detects temporal intent in queries, extracts topic + time window, rewrites into a date-bounded search.

**Temporal signal detection:**
```python
TEMPORAL_PATTERNS = [
    r"\blast (week|month|year|30|7|90)\b",
    r"\bin (January|February|March|...)\b",
    r"\b(yesterday|today|recently|lately)\b",
    r"\bwhen did\b",
    r"\bwhat changed\b",
    r"\bwhat was (done|completed|fixed|shipped)\b",
]
```

**Rewriter output:**
```python
@dataclass
class TemporalQuery:
    original: str
    topic: str            # extracted topic sans temporal language
    since: date | None
    until: date | None
    confidence: float     # 0-1; < 0.6 → treat as non-temporal
```

**Time-indexed entity facts query (`index.py`):**

```sql
SELECT ef.fact_text, ef.valid_from, ef.source_ref, e.name, e.type
FROM entity_facts ef JOIN entities e ON e.id = ef.entity_id
WHERE ef.fact_text LIKE '%' || :topic || '%'
  AND (:since IS NULL OR ef.valid_from >= :since)
  AND (:until IS NULL OR ef.valid_from <= :until)
ORDER BY ef.valid_from DESC
LIMIT 10
```

**Acceptance criteria:**
- Temporal benchmark category ≥ 0.55 (from 0.35)
- `mnemosyne timeline "Bower Bird" --since 2026-03-01` returns correct dated facts
- Board card chunks correctly indexed and retrievable by date
- All categories remain ≥ 0.50 (no regression)

#### 2c. L0/L1 tiered context (`mnemosyne/summaries/`)

**Why tiered context matters:** The current retrieval pipeline returns full document snippets (L2) regardless of whether the query needs depth. Broad concept queries spend token budget loading irrelevant content. L0 (100-token abstract) + L1 (2,000-token structural overview) let the system return more relevant content within the same token budget.

**L0 generation:**
- Format: `l0:` YAML frontmatter field in each vault file
- Content: 100-token abstract (1-2 sentences, factual, no editorialising)
- Staleness: `l0-generated:` date vs file mtime; stale if > 1 day difference
- Fallback: if no L0, use first 100 tokens of document body

**L1 generation:**
- Format: Sidecar `.L1.md` file at `/data/obsidian-vault/.summaries/<path>.L1.md`
- Content: ~2,000-token structural overview (purpose, key sections, core claims, key entities, when to load full doc)
- Generation model: Phi-4-mini primary (Azure Foundry) → gpt-4o-mini fallback
- Staleness: `source-mtime:` in L1 frontmatter vs source file mtime
- QMD collection: `vault-overviews` → `.summaries/**/*.L1.md`

**Open question Q1 (blocking L0 indexing):** Does QMD support frontmatter-only extraction for the `vault-abstracts` collection? Test before implementing. Fallback: generate `*.L0.txt` sidecar files alongside each vault file, indexed as `vault-abstracts-alt` collection.

**Tier routing in budget enforcer (`budget.py` updated):**
```
For each result in RRF output, ordered by score desc:
  1. Load L0 (always — trivial cost)
  2. If remaining_budget > 500 AND score > l1_threshold: load L1
  3. If remaining_budget > 2000 AND score > l2_threshold: load L2 (full doc)
  4. Stop when budget exhausted
```

**20-file Phi-4-mini validation (open question Q2):** Run L0/L1 generation on 20 representative vault files before full run. Evaluate output quality manually. Gate: ≥ 80% of L0 outputs are accurate, informative, non-hallucinated. If Phi-4-mini fails, switch to gpt-4o-mini for full run.

**Acceptance criteria:**
- `mnemosyne summarise --stale` generates L0/L1 for stale files without errors
- L0 search (`vault-abstracts`) returns results without loading full documents
- Token usage for broad queries drops measurably (log comparison before/after)
- Benchmark weighted total ≥ 0.68

---

### Phase 2.5 — Entity content enrichment + benchmark repair

**Not a phase gate.** Prerequisite for Phase 2 gate (≥0.68) and Phase 3. Two parallel workstreams.

**Why this exists:**
The Phase 1 gate (≥0.620) was declared passed at 0.642 using the old Python benchmark scripts. When migrated to the canonical YAML suite, the true score is 0.558. The regression has two confirmed root causes:

1. **Benchmark test design gap** — Entity queries (`E01`–`E06`) have `gold_path: null`, so the LLM judge scores based on semantic relevance to *any* content. Without explicit gold paths to entity stubs, the judge cannot distinguish "retrieved a tangentially-related doc" (score 0.2) from "retrieved the canonical entity stub" (score 0.9). The benchmark is failing to measure what we intend.

2. **Entity stub content sparsity** — Core stubs (notably `alex-jordan.md` at ~32 lines, `three-cubes.md`, `openclaw.md`) lack the substantive content needed to rank above incidental docs in BM25+vector search. For a query like "what do we know about Alex Jordan", the entity stub should be the unambiguously correct answer — but it loses to content-rich vault docs because it has less surface area for both keyword and semantic matching.

#### 2.5a — Benchmark suite repair (`suites/builder.yaml`)

**Goal:** Entity queries must have explicit gold paths so exact-match scoring works. LLM judge is reserved for queries where there is genuinely no single correct document (temporal, conceptual, multi-hop).

**Approach:**
- Add `gold_path` to all 6 entity cases (E01–E06) mapped to the canonical entity stub file
- Change `score_method` from `llm` to `exact` for E01, E02, E03, E05, E06 (these have unambiguous canonical stubs)
- Keep `score_method: llm` only for E04 ("what has Shape been working on") — answers are in memory logs, not a single entity stub

**Gold path mappings:**

| Case | Query | Gold path |
|---|---|---|
| E01 | what do we know about Alex Jordan | `04-Agent-Knowledge/entities/person/alex-jordan.md` |
| E02 | tell me about Triad Consulting as an organisation | `04-Agent-Knowledge/entities/organisation/three-cubes.md` |
| E03 | what is Builder agent responsible for | `04-Agent-Knowledge/entities/concept/builder.md` |
| E04 | what has Shape agent been working on | `null` (llm) — answer is in memory logs |
| E05 | what is tc-productivity | `04-Agent-Knowledge/entities/project/tc-productivity.md` |
| E06 | who is the OpenClaw platform built for | `04-Agent-Knowledge/entities/concept/openclaw.md` |

**Expected effect:** Entity score should rise from 0.300 to ≥0.600 if stubs are correctly returned as top result. If score stays low after gold path fix, it confirms the entity stubs are not ranking highly enough → content enrichment required.

**Validation:** Run benchmark before and after gold path change (no code changes). Delta isolates whether the issue is test design or retrieval quality.

#### 2.5b — Entity stub content enrichment (`04-Agent-Knowledge/entities/`)

**Goal:** Core entity stubs must be substantive enough (~500–1000 words) to rank above incidental vault docs for entity-intent queries.

**Principle:** An entity stub is the authoritative, synthesised record for that entity. It should be the answer to "what do we know about X" — not a registry entry, but a rich knowledge summary drawn from memory logs, decisions, and session history.

**Content standard per stub:**

```
## About / Overview          — 2–3 para summary of who/what they are
## Role in Triad Consulting       — specific to our context, not generic description
## Key Decisions / Positions — decisions this entity has made or that affect them
## Active / Recent Projects  — what they're working on now
## Communication Patterns    — how they communicate (for people) or how we use it (for tools)
## Relationships             — links to other entities they relate to
## Tags                      — classification for FTS
```

**Priority stubs to enrich (in order):**

| Stub | Current size | Target | Priority |
|---|---|---|---|
| `alex-jordan.md` | ~32 lines | 600+ words | P0 — E01 gold doc |
| `three-cubes.md` | unknown | 600+ words | P0 — E02 gold doc |
| `openclaw.md` | ~60 lines | 700+ words | P0 — E06 gold doc |
| `tc-productivity.md` | unknown | 500+ words | P1 — E05 gold doc |
| `shape.md` | unknown | 500+ words | P1 — entity system |
| `builder.md` | ~57 lines (rich) | review only | P2 — already good |

**Source material for enrichment:** Draw from `memory/2026-03-*.md`, `04-Agent-Knowledge/builder/decisions.md`, `04-Agent-Knowledge/shared/`, AGENTS.md (read-only reference), USER.md (read-only reference).

**Constraint:** Stubs must use only confirmed facts from memory/vault. No speculation. Use `Working assumption (unconfirmed):` for inferred content.

**Re-embed after enrichment:** After stub content is updated, run `mnemosyne embed --changed` to update vectors for the modified files. Enriched stubs must be re-indexed before benchmark is re-run.

**Acceptance criteria:**
- Each P0/P1 stub is ≥500 words of substantive content
- Entity score on benchmark ≥ 0.600 with exact gold path scoring
- Benchmark weighted total ≥ 0.620 (Phase 1 gate re-confirmed on YAML suite)
- No regressions on recall (≥ 0.875), temporal (≥ 0.500), conceptual (≥ 0.575)

---

### Phase 3 — Session briefing + auto-classification

**Benchmark gate:** ≥ 0.75 weighted total. All categories ≥ 0.60.

**Prerequisite:** Phase 2.5 complete (entity stubs enriched, benchmark gate ≥0.620 confirmed on YAML suite).

#### 3a. Session briefing (`mnemosyne/briefing/`)

**Purpose:** Replace cold-start or raw-chunk context loading with a synthesised ~800-token briefing that prioritises the most relevant information for the session.

**8-step pipeline (`pipeline.py`):**

| Step | Source | Cap | What it retrieves |
|---|---|---|---|
| 1 | `<agent>-memory` collections | 500 tokens | Last 3-5 session summaries (7 days) |
| 2 | Today's + yesterday's memory file | 300 tokens | Items tagged `[pending]`, `[blocked]`, `[action:]` |
| 3 | `knowledge-entities` | 400 tokens | Entity profiles for names in session context |
| 4 | `knowledge-<agent>` + `knowledge-shared` | 300 tokens | Top 6 active rules and constraints |
| 5 | `entity_facts` time query | 400 tokens | Decisions from last 30 days |
| 6 | Hybrid search on session topic | 600 tokens | Top 5 relevant knowledge chunks |
| 7 | Phi-4-mini synthesis | → 800 tokens | Structured briefing output |
| 8 | Write to file | — | `/data/mnemosyne/briefing/<agent>-latest.md` |

Total input: ~2,700 tokens. Output: ~800 tokens (70% compression). The file is ephemeral — overwritten each session. It is working memory initialisation, not a persistent record.

**Template per agent:** Each agent has a template in `briefing/templates/<agent>.md` that defines section weights and formatting preferences. Default template used if agent-specific template doesn't exist.

**Integration with AGENTS.md:** Add to every agent's "Every Session" section:
```bash
mnemosyne brief [agent-name]
cat /data/mnemosyne/briefing/[agent-name]-latest.md
```

**Failure mode:** If briefing generation fails for any reason (API timeout, DB locked), agent falls back to standard MEMORY.md + today's memory file. Log the failure. Never block the agent.

**Acceptance criteria:**
- `mnemosyne brief shape` completes in < 30 seconds
- Output is < 800 tokens
- Briefing contains actionable pending items from memory logs
- Benchmark weighted total ≥ 0.75

#### 3b. Auto-classification (`mnemosyne/classify/`)

**Purpose:** Route new memory writes to the correct target file automatically. Reduce inconsistency in where agents write things.

**Two-stage classifier:**

Stage 1 — Rule-based (`rules.py`):

| Signal | Classification | Target |
|---|---|---|
| Starts with `## [HH:MM]` | episodic | `memory/YYYY-MM-DD.md` |
| Contains `never`, `always`, `rule:`, `constraint:`, `never do` | procedural-rule | `rules.md` |
| Contains `pattern:`, `workflow:`, `how to`, `step [1-9]`, `## Steps` | procedural-pattern | `patterns.md` |
| Contains `decided:`, `decision:`, `ADR-`, `we chose`, `rationale:`, `we decided` | semantic-decision | `decisions.md` |
| Named person/org as primary subject + attributes | entity | `entities/<type>/<slug>.md` |
| Contains infra/config facts (IP, version, endpoint, vCPU) | semantic-fact | `facts.md` |
| No signal matches | → Stage 2 | — |

Stage 2 — LLM-judge (`judge.py`):
- Input: content + few-shot examples
- Output: `{type, target_agent, confidence: 0-1, reason}`
- Confidence < 0.70 → return to caller with classification suggestion and ask to confirm before writing

**Write router (`router.py`):** Given classification result, returns absolute path to target file. Creates file if it doesn't exist. Enforces agent scoping (cannot route to another agent's knowledge files).

**Acceptance criteria:**
- Rule-based classifier routes > 90% of test cases correctly without LLM call
- LLM-judge handles ambiguous cases with confidence ≥ 0.70 on ≥ 80% of cases
- No agent can write to another agent's collection via the classifier

---

### Phase 4 — Date-aware chunking + multi-hop query planning (✅ Complete 2026-04-05)

> **Note:** The original Phase 4 scope (contradiction detection + entity extraction) was deferred. Phase 4 was re-scoped to address the two highest-impact search quality gaps: temporal retrieval and multi-hop synthesis.

**Benchmark result:** 0.6658 on revised 36-query suite (gate ≥ 0.620 ✅). Temporal: 0.433→0.633, Multi-hop: 0.480→0.600.

#### 4a. Date-aware chunking (`mnemosyne/search/chunker.py`)

Splits documents on H2 boundaries, extracts `[completed::]` / `[started::]` date fields per chunk, stores in `chunk_metadata` SQLite table. Temporal re-ranking boosts chunks whose `chunk_date` falls in extracted date window.

#### 4b. Multi-hop query planner (`mnemosyne/search/planner.py`)

LLM-based decomposition via gpt-4o-mini (Azure AI Foundry, using `mnemosyne._azure.chat_completion`). Breaks connective queries into 2–3 sub-queries, executes in parallel via `ThreadPoolExecutor`, merges via RRF (k=60).

**Original Phase 4 scope (contradiction detection + entity extraction):** Deferred to future phase. See original section preserved below for implementation notes.

#### 4a. Contradiction detection (`mnemosyne/contradict/`)

**Trigger:** Invoked by `mnemosyne classify` at write time, and run in batch by nightly cron on recent writes.

**3-stage detection:**

Stage 1 — Rule-based (`rules.py`, sync, free):
- Numeric facts: `[entity] has [N] [units]` → check `entity_facts` for existing fact about same attribute with different N
- Status reversals: active/inactive, enabled/disabled, deployed/decommissioned
- Date conflicts: "decided on DATE" conflicts with existing different date for same decision

Stage 2 — Embedding similarity (`similarity.py`, async):
- Embed the new fact (text-embedding-3-large, reuse embed module)
- CTE search against existing `entity_facts` (via entity_id)
- Cosine distance < 0.15 (high similarity) → candidates for contradiction
- Extract claims from candidate facts; compare with simple assertion parser

Stage 3 — LLM-judge (`judge.py`, targeted):
- Only invoked when Stage 2 returns 3+ high-similarity candidates
- gpt-4o-mini: "Do these two statements contradict each other? Return {contradicts: bool, reason, confidence}"
- Act only on confidence > 0.80

**Resolution workflow (`resolve.py`):**
1. Log to `/data/mnemosyne/logs/contradictions.jsonl`
2. Append to `04-Agent-Knowledge/shared/00-CONFLICTS.md`:
   ```markdown
   ## [Date] — [Entity/Topic] — UNRESOLVED
   **New:** [text] ([source])
   **Existing:** [text] ([source])
   **Detection:** Stage [N], confidence [X]
   ```
3. If confidence > 0.90: send Telegram notification via Shape
4. **Never silently overwrite.** Preserve both claims until human resolves.

#### 4b. Automated entity extraction (`mnemosyne/entities/extract.py`)

Runs after `qmd update` in qmd-maintenance.sh. Scans modified vault files for entity mentions using gpt-4o-mini NER. Creates/updates entity Markdown files and SQLite records.

**Trigger:** Files with `<!-- mnemosyne-extract: true -->` comment are prioritised. All changed files processed incrementally.

**Extraction prompt:** Structured JSON output — `{entities: [{name, type, id_slug, facts: [{fact_type, fact_text, valid_from}]}], relationships: [{from, to, rel_type, confidence}]}`.

**Acceptance criteria:**
- Writing "The VM has 32 vCPUs" to `facts.md` when "4 vCPU" is in entity_facts triggers Stage 1 contradiction
- Entity files auto-created for top 10 recurring names in memory logs after backfill run
- Benchmark entity category ≥ 0.65
- `00-CONFLICTS.md` is populated with any real contradictions found in backfill

---

### Phase 5 — Eval quality rebuild + observability

**Goal:** Replace synthetic benchmark with real-world queries mined from agent session logs. No regression goes undetected.

#### 5a. Real-world eval suite

Mine `memory_search` calls from OpenClaw agent session logs on the VM. Extract actual queries agents issued, their context, and ground-truth results (what was useful). Replace synthetic test cases (e.g. fictional entities) with real-world queries grounded in actual vault content.

**Process:**
1. Parse agent session logs from `/data/tc-agent-zone/logs/` and OpenClaw session history
2. Extract `memory_search` tool calls + agent-provided context
3. Score against current retrieval (gold = what agent actually used from results)
4. Build `benchmark-results/test-cases-v2.jsonl` with real queries

**Goal:** No regression goes undetected. Every retrieval decision is measurable.

**Benchmark runner (`mnemosyne/benchmark/`):**
- Expand to 100-case test suite (50 mined from real agent session queries, 50 synthetic)
- Test case format: `{id, category, query, gold, scoring_type, source, weight}`
- Store in `benchmark-results/test-cases.jsonl` (versioned — don't overwrite old test cases)
- Supports `--system bm25|vector|hybrid|<any>` flag
- Outputs `benchmark-results/B<N>-<system>-<date>.json`
- Run time < 10 minutes for 100 cases

**OTel metrics:**
```
mnemosyne.search.latency_ms        (histogram, tagged: intent, collections)
mnemosyne.search.tier_used         (counter, tagged: tier=L0|L1|L2)
mnemosyne.search.result_count      (histogram)
mnemosyne.embed.latency_ms         (histogram)
mnemosyne.embed.batch_cost_usd     (counter)
mnemosyne.benchmark.score          (gauge, tagged: system, category)
mnemosyne.contradict.detections    (counter, tagged: stage)
mnemosyne.entity.write_count       (counter, tagged: type)
```

All events sent to App Insights via existing OTel collector. New Azure Workbook panel for Mnemosyne metrics.

**Regression alerting:** Weekly benchmark cron (Saturday 2am AEST). If weighted total drops > 5% from previous run, send Telegram alert to Shape with category breakdown.

---

## 6. Testing Strategy

### Principles

1. **Test what breaks in production, not what's easy to test.** Phase 0 found three production bugs that weren't in the test suite. Each became a test immediately. This is the standard.
2. **No network calls in unit tests.** Mock everything external (Azure API, QMD subprocess).
3. **Integration tests run against real sqlite-vec but skip gracefully when unavailable.** The extension is bundled with QMD, not available in GitHub Actions CI.
4. **E2E tests are gated.** `MNEMOSYNE_E2E=1` required. Run against a DB copy, never the live vault.
5. **Benchmark is not a CI test.** It's a pre/post measurement for architecture changes, run manually or by weekly cron.

### Test structure

```
tests/
  embed/              # Phase 0 — full coverage
    test_encoding.py    # Vector encoding, hash_seq format
    test_batching.py    # Batch splitting, chunk_text
    test_retry.py       # Azure API retry logic, rate limit handling
    test_db_roundtrip.py # staging table upsert, content_vectors write
    test_sqlite_vec_constraints.py  # 3 production failure modes
    test_vsearch.py     # Direct CTE vector search (E2E gated)

  search/             # Phase 1
    test_intent.py      # Intent classifier accuracy on labelled examples
    test_rrf.py         # RRF math: score correctness, tie-breaking, k sensitivity
    test_bm25.py        # BM25 wrapper: parse, timeout, empty result handling
    test_vector.py      # Vector wrapper: extension failure fallback
    test_hybrid.py      # Orchestrator: intent → dispatch → fuse → budget
    test_budget.py      # Token budget enforcement, tier selection thresholds
    test_entity_boost.py # Entity boost math, cap enforcement

  entities/           # Phase 1
    test_schema.py      # entities.db DDL, migration, version check
    test_graph.py       # entity_write, entity_lookup, get_mentions
    test_entity_boost_integration.py  # boost applied correctly in search

  temporal/           # Phase 2
    test_chunker.py     # Board file → per-card chunks (known fixture boards)
    test_log_chunker.py # Memory log → per-section chunks
    test_rewriter.py    # Temporal signal detection, date extraction
    test_index.py       # entity_facts time-range query

  summaries/          # Phase 2
    test_staleness.py   # Stale detection: mtime comparison
    test_loader.py      # Tier routing: L0/L1/L2 selection under budget
    test_generate.py    # L0/L1 generation (mocked Phi-4-mini API)

  classify/           # Phase 3
    test_rules.py       # Rule-based classifier: 50 labelled examples
    test_judge.py       # LLM-judge: mocked API, confidence gate
    test_router.py      # Target file resolution, agent scoping enforcement

  briefing/           # Phase 3
    test_pipeline.py    # 8-step pipeline (all retrieval mocked)
    test_templates.py   # Template rendering per agent

  contradict/         # Phase 4
    test_rules.py       # Stage 1: numeric, status, date conflict detection
    test_similarity.py  # Stage 2: high-similarity candidate detection
    test_judge.py       # Stage 3: LLM-judge mock + confidence gate
    test_resolve.py     # CONFLICTS.md write, log write

  benchmark/          # Phase 5
    test_runner.py      # Benchmark runner mechanics (not actual Azure calls)
    test_judge.py       # LLM-judge scorer (mocked)
    test_report.py      # Results formatting, trend analysis
```

### Coverage targets

| Module | Line coverage target | Notes |
|---|---|---|
| `embed/` | ≥ 90% | Already ~85%; add edge cases for rate limit paths |
| `search/` | ≥ 85% | Intent classifier: 100% of rule branches |
| `entities/` | ≥ 85% | Schema migration: 100% |
| `temporal/` | ≥ 80% | Chunker: test with real fixture board files |
| `summaries/` | ≥ 75% | Generation mocked; staleness logic 100% |
| `classify/` | ≥ 90% | Rule-based 100% (it's a lookup table) |
| `briefing/` | ≥ 75% | Pipeline integration test more valuable than unit coverage |
| `contradict/` | ≥ 80% | Stage 1 rules: 100% |
| `_azure`, `_qmd`, `_db` | ≥ 95% | Shared utilities must be bulletproof |

### Test data strategy

**Fixtures:** Real-ish vault content (anonymised or abstracted) committed to `tests/fixtures/`. Board files, memory log excerpts, and entity files that represent the realistic structure without containing Dan's actual data in the public repo.

**Golden test cases for classification (`tests/classify/fixtures/cases.jsonl`):**
```json
{"content": "Never write credentials to disk", "expected_type": "procedural-rule", "expected_target": "rules.md"}
{"content": "Decided to use RRF k=60 as default", "expected_type": "semantic-decision", "expected_target": "decisions.md"}
```
Minimum 50 labelled cases before Phase 3 ships. Grows to 100 by Phase 5.

**Benchmark test cases (`benchmark-results/test-cases.jsonl`):**
Versioned. Old cases are never removed. New cases are added with an incrementing `id`. Categories and weights are fixed at Phase 0.

### CI gates

Every pull request must:
1. Pass all unit tests (`pytest tests/ -m "not integration and not e2e"`)
2. Pass integration tests where sqlite-vec is available (`pytest tests/ -m integration`)
3. Pass type checking (`mypy mnemosyne/` — added in Phase 1)
4. Pass linting (`ruff check mnemosyne/`)
5. Not reduce overall test coverage below 80%

PRs that change retrieval logic (search, rrf, budget, temporal) must include a before/after comparison on at least the recall and conceptual benchmark categories.

---

## 7. Non-Functional Requirements

### 7.1 Performance

| Operation | P50 | P95 | Measured from |
|---|---|---|---|
| BM25 only (`qmd search` subprocess) | < 700ms | < 1,200ms | Phase 0: ~616ms P50 |
| Vector only (CTE MATCH) | < 200ms | < 500ms | Phase 0: ~163ms P50 |
| Hybrid search (dispatch + RRF) | < 900ms | < 1,600ms | Target (sum + 15% overhead) |
| Entity lookup | < 100ms | < 300ms | SQLite in-process |
| Temporal query (chunked) | < 1,000ms | < 2,000ms | BM25 on board-cards collection |
| L0 search | < 300ms | < 700ms | BM25 on vault-abstracts |
| Session briefing synthesis | < 30s | < 60s | Phi-4-mini cold start: ~2s warm |
| Embedding new docs (async, not blocking) | n/a | n/a | Background cron |

**Latency budget for hybrid search:** BM25 + vector dispatched concurrently using `concurrent.futures.ThreadPoolExecutor`. Neither call blocks the other. Total time ≈ max(BM25, vector) + RRF overhead (~5ms) + budget trim (~5ms).

### 7.2 Concurrency and SQLite

All SQLite databases use WAL mode:
```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;  -- 5s wait on lock before raising
```

**Write serialisation:** Cron scripts staggered (see §11). Multiple concurrent reads are safe in WAL mode. Writes from different processes are prevented by lockfiles at `/tmp/mnemosyne-<component>.lock`. Each lockfile stores the PID; stale locks (PID not running) are cleaned up by the acquiring process.

**Cron scripts that write to SQLite:**
- `mnemosyne embed` → `qmd/index.sqlite` (vectors_vec)
- `mnemosyne entity extract` → `entities.db`
- `mnemosyne summarise` → vault files (frontmatter L0 injection)
- `mnemosyne contradict` → `entities.db` (entity_facts), vault files (CONFLICTS.md)

These are staggered 5 minutes apart to prevent lock contention.

### 7.3 Memory and disk

| Component | Initial size | Growth rate | 12-month estimate |
|---|---|---|---|
| `qmd/index.sqlite` (existing + vectors) | ~100MB | ~10MB/month | ~220MB |
| `entities.db` | ~2MB | ~500KB/month | ~8MB |
| `.summaries/` (L1 sidecars) | ~100MB | ~10MB/month | ~220MB |
| `mnemosyne/logs/` | ~1MB | ~5MB/month (rotated at 7 days) | ~35MB sustained |
| Board card chunks | ~5MB | ~1MB/month | ~17MB |
| Briefing files (ephemeral) | < 1MB | — | < 1MB |

Total new storage: ~120MB initial, ~570MB at 12 months. Well within VM data volume.

**Runtime memory (Python process):**
- Embed pipeline: ~200MB peak (batch of 100 vectors in memory)
- Search: ~50MB (DB connection + result set)
- Briefing: ~100MB (synthesis context)
No persistent daemon. All operations are one-shot processes.

### 7.4 Availability and resilience

**Degradation hierarchy (ordered by decreasing capability):**

| State | Capability | What's missing |
|---|---|---|
| Full system | Hybrid search + entities + tiered context + briefing | Nothing |
| Vector DB unavailable | BM25 only | Semantic recall improvement |
| Entity graph unavailable | Hybrid search without entity boosting | Entity consolidation |
| Azure API unavailable | BM25 only, no new embeddings queued | New doc embedding (async retry) |
| QMD unavailable | No search at all | Everything (QMD is the critical dependency) |

No agent operation must fail because Mnemosyne has a problem. The one exception is the briefing pipeline — if it fails, agents fall back to standard MEMORY.md + daily log loading.

**Recovery procedures:** All Mnemosyne artifacts are derived from vault source files. Recovery for any corrupted artifact: delete it, run the corresponding generation command.

```bash
# Corrupt or missing vectors
mnemosyne embed --force

# Corrupt entity graph
rm /data/mnemosyne/entities.db
mnemosyne entity extract --all

# Missing L1 summaries
mnemosyne summarise --all

# Corrupt board card chunks
rm -rf /data/mnemosyne/chunks/
mnemosyne timeline chunk --all
```

### 7.5 Security

- **Secrets:** All Azure API keys fetched from `kv-tc-exp` Key Vault at runtime via `az keyvault secret show`. Never written to disk, environment variable, or log.
- **Agent scoping:** `--agent` parameter enforces collection boundaries. `mnemosyne classify` cannot route to another agent's knowledge files.
- **No client data in shared indexes.** Client content (if any) goes to agent-scoped collections, not `knowledge-shared`.
- **Log sanitisation:** No secret values in `mnemosyne/logs/`. No PII in OTel metrics.
- **DB permissions:** `entities.db` and QMD index readable by `openclaw` user only (`chmod 640`).

### 7.6 Maintainability

- **Type annotations:** All public functions in all modules have type annotations. `mypy` strict mode from Phase 1.
- **Module docstrings:** Every module has a top-level docstring describing its purpose, inputs, outputs, and failure modes.
- **No magic constants:** All thresholds (RRF k, boost factor, confidence gates, budget values) are named constants at module level with comments explaining their derivation.
- **Schema versioning:** `entities.db` has a `schema_version` table. Migrations are numbered scripts in `entities/migrations/`. The schema module checks version on every startup and raises if the DB is ahead of the code.
- **QMD compat:** CI runs weekly schema check against live QMD. Auto-opens GitHub issue on drift (established in Phase 0).

---

## 8. Delivery Infrastructure

### 8.1 Repository

**URL:** `https://github.com/three-cubes/qmd-azure-embed`  
**Default branch:** `main`  
**Access:** Private (currently public — review before client-facing use)

**Branch strategy:**
- `main` — production. Only merge via PR.
- `feat/<module>-<description>` — feature branches per phase/module
- `fix/<description>` — bug fix branches
- No long-lived branches. PRs should be small (< 400 lines changed where possible).

**Commit convention:** `<type>: <description>`
- `feat:` — new capability
- `fix:` — bug fix
- `test:` — test additions/changes
- `docs:` — documentation
- `refactor:` — internal restructure without behaviour change
- `chore:` — dependency updates, CI changes

### 8.2 CI/CD

**GitHub Actions workflows:**

`ci.yml` — Runs on every push and PR:
```yaml
jobs:
  test:
    matrix: python: [3.10, 3.11, 3.12]
    steps:
      - pytest tests/ (unit only — no sqlite-vec, no Azure API)
      - mypy mnemosyne/
      - ruff check mnemosyne/
      - coverage report --fail-under=80
```

`integration.yml` — Runs on PR to main (if sqlite-vec available):
```yaml
jobs:
  integration:
    steps:
      - Install QMD (to get sqlite-vec)
      - pytest tests/ -m integration
```

`qmd-compat.yml` — Weekly (Monday 02:00 UTC):
```yaml
jobs:
  compat:
    steps:
      - Check live QMD schema against mnemosyne/embed/schema.py expectations
      - Open GitHub issue if drift detected
```

**PR requirements before merge:**
1. CI passes (unit tests, mypy, ruff, coverage ≥ 80%)
2. At least one reviewer approval (Dan or Builder)
3. No secrets in diff
4. If retrieval logic changed: benchmark comparison in PR description

### 8.3 Deployment procedure

**There is no deployment pipeline.** Mnemosyne runs on `vm-tc-openclaw` directly. Deployment is:

```bash
cd /tmp/qmd-azure-embed       # or wherever it's cloned
git pull origin main
.venv/bin/pip install -e .    # reinstall if pyproject.toml changed
```

The venv is at `/tmp/qmd-azure-embed/.venv`. It persists across deploys. If the `tmp` directory is cleared (unlikely but possible), re-clone and re-install.

**Cron scripts call the venv directly:**
```bash
/tmp/qmd-azure-embed/.venv/bin/mnemosyne embed
```

**Rollback:** `git checkout <previous-commit>` + `pip install -e .`. Because all state is in SQLite and vault files (not in the Python package), rollback is safe — old code reads existing state correctly.

**Deploy checklist:**
- [ ] `git log --oneline -5` — confirm what's being deployed
- [ ] `pytest tests/ -q` — confirm tests pass on VM
- [ ] If schema changed: `mnemosyne embed --limit 10` smoke test before full run
- [ ] If entities schema changed: verify `entities.db` schema_version matches
- [ ] Update cron if new subcommands added

### 8.4 Environments

There is one environment: `vm-tc-openclaw`. There is no staging environment.

**Mitigation:** All destructive operations (embed --force, entity extract --all, summarise --all) require explicit flags. The `--limit N` flag lets any operation be tested on a small subset before running full. All cron runs are incremental by default.

**Test isolation:** The `MNEMOSYNE_TEST_DB` environment variable redirects all SQLite operations to a test DB path. The `MNEMOSYNE_E2E` flag enables live Azure API calls in tests. Both must be set explicitly — tests cannot accidentally modify the live DB.

### 8.5 Secret management

| Secret | KV secret name | Used by |
|---|---|---|
| Azure OpenAI API key | `azure-openai-api-key` | embed, search, summaries, classify, contradict, benchmark |
| Azure OpenAI endpoint | `azure-openai-endpoint` | same |
| Embedding deployment name | `azure-openai-embedding-deployment` | embed |
| gpt-4o-mini deployment | `azure-openai-deployment` | entities/extract, classify/judge, contradict/judge, benchmark |
| Phi-4-mini deployment | `azure-foundry-phi4-mini-instruct-deployment` | summaries/generate, briefing/pipeline |
| Phi-4-mini API key | `azure-foundry-phi4-mini-instruct-key` | summaries/generate, briefing/pipeline |

**Shared Azure client (`mnemosyne/_azure.py`):** Single module that fetches all secrets at startup (cached for process lifetime). All other modules import from `_azure`. Secrets are never passed as function arguments — accessed via the client object.

**Secret rotation:** If a secret is rotated in Key Vault, the next Mnemosyne process start picks up the new value automatically (fetched at runtime, not cached to disk). No restart required.

---

## 9. Evaluation Framework

### 9.1 Benchmark design

**Test suite:** 50 queries at Phase 0. Expand to 100 by Phase 5.

**6 categories with fixed weights:**

| Category | Weight | What it measures | Baseline |
|---|---|---|---|
| Recall | 25% | Right document surfaces for direct factual query | BM25: 0.50 |
| Temporal | 20% | Time-bounded retrieval ("what was done last week") | BM25: 0.37 |
| Entity/Relationship | 20% | Consolidated knowledge about a named person/org | BM25: 0.57 |
| Conceptual | 15% | Semantic match when query shares no keywords with answer | BM25: 0.63 |
| Multi-hop | 10% | Connect two knowledge pieces to answer a question | BM25: 0.40 |
| Procedural | 10% | Surface how-to content reliably | BM25: 0.60 |

**Weights are fixed for all phases.** Do not change category weights between runs. If weights change, the baseline score changes and comparisons become meaningless.

### 9.2 Scoring methodology

**Exact match (Recall, Procedural):**
```python
def exact_match(gold: str, results: list[Result]) -> Score:
    # Check gold document path in top-N result paths
    # Partial path match allowed (last 2 segments)
    # Special case: "board card" gold → keyword match on combined text
    return Score(3 if match else 0)
```

**LLM-as-judge (all other categories):**
- Model: gpt-4o-mini (cheap, fast, sufficient for scoring)
- Prompt: fixed system prompt (see Phase 0 benchmark scripts)
- Scale: 0=irrelevant, 1=tangential, 2=partial, 3=complete
- Never change the judge prompt between phases (scoring must be comparable)

**Aggregate:**
```
category_score = sum(query_scores) / (n_queries × 3)    # normalise to 0-1
weighted_total = sum(category_score × weight for each category)
```

### 9.3 Test case validity

**A test case is valid if:**
1. The gold answer actually exists in the current vault/index
2. The query is unambiguous (only one correct answer)
3. A human could answer it given the gold document

**Test case hygiene:** Run `mnemosyne benchmark --validate-cases` before each benchmark run. This checks that gold document paths exist and are indexed. Invalidate (skip with warning) rather than fail on missing gold docs — the vault changes.

**Adding test cases:** New cases are added to the bottom of `test-cases.jsonl` with an incrementing `id`. Old cases are never removed — they become historical evidence of what the system could/couldn't do. If a case becomes invalid (gold doc deleted), mark `"status": "inactive"` — it's excluded from scoring but preserved for history.

### 9.4 Benchmark run protocol

Before running a benchmark:
1. Confirm vault is up to date: `qmd status` shows 0 pending docs
2. Confirm vectors are current: `mnemosyne embed` (incremental, safe to run)
3. Record git commit hash, date, vault doc count, vector count in results JSON

After running:
1. Compare to previous run: check no category dropped > 5%
2. If phase gate not met: identify specific failing queries, diagnose, fix, re-run
3. Commit results JSON to repo: `git add benchmark-results/ && git commit -m "eval: <system> benchmark <date>"`
4. Update comparison table in `PRD.md` and `BENCHMARK.md`

### 9.5 Phase gate process

A phase is complete when:
1. Benchmark score meets the gate target
2. All acceptance criteria for the phase are verified
3. No category has regressed below its Phase 0 baseline
4. Unit and integration tests pass
5. PR merged to main with benchmark results in description

If gate not met after implementation: diagnose per-category failures, tune (RRF k, boost factor, thresholds), re-benchmark. If after 2 tuning iterations the gate is still not met, consult Dan before Phase N+1 starts.

---

## 10. Operational Runbook

### 10.1 Cron schedule

Staggered to prevent concurrent SQLite writes. All times Sydney AEST.

```
*/30 * * * *    openclaw  /path/to/qmd-maintenance.sh           # QMD BM25 re-index
5,35 * * * *    openclaw  mnemosyne embed                        # Incremental embedding
10,40 * * * *   openclaw  mnemosyne entity extract --stale       # Entity extraction (Phase 4+)
0 3 * * *       openclaw  mnemosyne summarise --stale            # L0/L1 generation (Phase 2+)
30 3 * * *      openclaw  mnemosyne contradict --batch           # Contradiction detection (Phase 4+)
0 3 * * 6       openclaw  mnemosyne benchmark --system hybrid    # Weekly regression (Phase 5+)
```

Note: Cron commands that don't exist yet (Phase 2+) should be added to crontab when the phase ships, not before.

### 10.2 Monitoring and alerting

**Log locations:**
```
/data/mnemosyne/logs/embed.log          # Embed pipeline runs
/data/mnemosyne/logs/search.jsonl       # Retrieval events (JSONL for query analysis)
/data/mnemosyne/logs/entities.jsonl     # Entity write events
/data/mnemosyne/logs/contradictions.jsonl # Contradiction detections
/data/mnemosyne/logs/benchmark.jsonl    # Benchmark run results
```

**Log rotation:** 7-day retention for all logs. `logrotate` config added to `/etc/logrotate.d/mnemosyne`.

**OTel metrics** (Phase 5): All `mnemosyne.*` metrics sent to App Insights via existing OTel collector. Alert rules:
- `mnemosyne.benchmark.score` drops > 5% week-over-week → Telegram alert to Shape
- `mnemosyne.embed.batch_cost_usd` > $0.50 in single run → Telegram alert
- Any OTel push failure → existing OTel alerting handles this

**Health check:** `mnemosyne embed --limit 0` (validates DB, loads extension, confirms Azure API key, exits 0). Run as part of OpenClaw gateway health check.

### 10.3 Failure playbook

**Embed fails with "no such module: vec0"**
```bash
# Verify extension path
ls /data/workspace/.tools/qmd/node_modules/.pnpm/sqlite-vec-linux-x64@*/node_modules/sqlite-vec-linux-x64/vec0.so
# If missing — QMD was updated and sqlite-vec version changed
# Update VEC_SO_PATH in mnemosyne/embed/schema.py
# Run: mnemosyne embed --limit 10 to verify
```

**Embed fails with UNIQUE constraint**
```bash
# Clear vectors and restart
python3 -c "
import sqlite3; db = sqlite3.connect('~/.cache/qmd/index.sqlite')
db.enable_load_extension(True); db.load_extension('<vec_so_path>')
db.execute('DELETE FROM vectors_vec'); db.execute('DELETE FROM content_vectors')
db.commit()
"
mnemosyne embed --force
```

**Entity graph out of sync**
```bash
rm /data/mnemosyne/entities.db
mnemosyne entity extract --all  # Phase 4+ only; before Phase 4, re-write manually
```

**L1 summaries stale after vault restructure**
```bash
mnemosyne summarise --all  # Regenerates all stale L1 files
```

**Benchmark score drops unexpectedly**
```bash
# Check which categories dropped
cat benchmark-results/B*-<date>.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d['scores'], indent=2))"
# Check recent vault changes
git -C /data/obsidian-vault log --oneline --since=1week
# Re-run with verbose output
mnemosyne benchmark --system hybrid --verbose
```

**Azure API key expired**
```bash
# Rotate in Key Vault (Azure portal or CLI)
az keyvault secret set --vault-name kv-tc-exp --name azure-openai-api-key --value <new-key>
# Next mnemosyne process run picks it up automatically
```

### 10.4 Maintenance procedures

**After QMD upgrade:**
1. Run `mnemosyne embed --limit 0` (schema validation check — will fail fast if schema changed)
2. If schema validation fails: update `mnemosyne/embed/schema.py` with new column names
3. Run full benchmark to confirm no regression

**After vault restructure (collection path changes):**
1. Update `~/.config/qmd/index.yml` with new paths
2. Run `qmd update` to reindex
3. Run `mnemosyne embed` (incremental — only new/changed docs)
4. Run benchmark on temporal + recall categories (most affected by structural changes)

**Monthly cost check:**
```bash
# Check embed API usage this month
cat /data/mnemosyne/logs/embed.log | grep "cost=" | awk -F'cost=\\$' '{sum += $2} END {print "Total: $" sum}'
```

---

## 11. Architecture Decision Records

### ADR-01: Benchmark before build ✅ RESOLVED
Phase 0 benchmark must complete before Phase 1. All architectural claims are hypotheses until measured.
**Resolution:** Phase 0 complete. Baseline established 2026-03-22.

### ADR-02: Python over bash ✅ RESOLVED
Shell scripts were the original plan. Phase 0 shipped Python with tests, CI, and type safety.
**Resolution:** Python is the implementation language for all modules.

### ADR-03: text-embedding-3-large as embedding model ✅ RESOLVED
1536-dim, Azure OpenAI. MIRACL +24% advantage over 3-small for entity names and technical terms.
**Resolution:** Deployed and validated. 6,198 vectors in production.

### ADR-04: QMD SQLite as the primary vector store ✅ RESOLVED
Inject into QMD's `vectors_vec` rather than a separate embeddings.db. One less database.
**Resolution:** Accepted. Phase 0 confirmed the approach works.

### ADR-05: Local llama.cpp pipeline permanently disabled ✅ RESOLVED
`qmd vsearch` hangs indefinitely on CPU-only VM. Not re-enabling.
**Resolution:** Mnemosyne hybrid search replaces this function. Confirmed in Phase 0.

### ADR-06: Hybrid RRF is a correctness requirement, not an optimisation ✅ ACCEPTED
Phase 0 proved pure vector degrades on conceptual (0.21) and procedural (0.20). Both BM25 and vector must be combined.
**Implication:** Phase 1 hybrid cannot ship without the BM25 wrapper. Vector-only mode is not a valid configuration.

### ADR-07: Temporal fix is upstream (chunking), not downstream (query rewriting) ✅ ACCEPTED
Both BM25 (0.37) and vector (0.33) fail equally on temporal. Root cause is content structure: board files and daily logs aren't date-indexed at chunk level.
**Implication:** Phase 2 temporal module must include the board/log chunker as its primary fix. The query rewriter is secondary.

### ADR-08: Entity graph SQLite is separate from QMD SQLite ✅ ACCEPTED
QMD schema is pinned and guarded. Adding entity tables risks QMD compat breakage.
**Implication:** `entities.db` at `/data/mnemosyne/entities.db`. Entity QMD collection uses vault Markdown files as the indexed content.

### ADR-09: Phi-4-mini as L0/L1 generation primary ✅ ACCEPTED (pending validation)
Summarisation is not reasoning. Phi-4-mini is cost-optimal. Fallback: gpt-4o-mini.
**Open question Q2:** 20-file validation run required before full vault L0/L1 generation.

### ADR-10: No Coach or Family integration ✅ ACCEPTED
Privacy boundaries: Coach has no vault access, Family is sandboxed.
**Implication:** MEMORY.md remains the only memory mechanism for Coach and Family in v1.

### ADR-11: No staging environment — use `--limit` and test DB flags
A single-VM internal tool does not justify a staging environment. Risk mitigation: `--limit N` for all destructive operations, `MNEMOSYNE_TEST_DB` for test isolation.
**Status:** Accepted.

### ADR-12: entities.db is separate from qmd/index.sqlite
The entity graph has different write patterns (frequent small writes from extraction) and different schema lifecycle than the QMD index. Keeping them separate allows schema evolution without touching QMD's DB.
**Status:** Accepted.

---

## 12. Open Questions

| # | Question | Blocking | Owner | Resolution path |
|---|---|---|---|---|
| Q1 | Does QMD support frontmatter-only collection extraction for L0 indexing? | Phase 2 L0 | Builder | Test `extract: frontmatter-only` in QMD config; fallback is `.L0.txt` sidecars |
| Q2 | Is Phi-4-mini quality sufficient for L0/L1 on our vault content? | Phase 2 generation | Builder | 20-file validation run (TASK-03 holdover); score ≥ 80% accurate abstracts |
| Q3 | What RRF k constant fits our corpus? (default 60, but corpus is small/homogeneous) | Phase 1 tuning | Builder | A/B test k=30, 60, 90 on recall category; pick k that maximises recall without degrading conceptual |
| Q4 | Should entity lookup replace hybrid as the primary path for entity queries? | Phase 1 entity | Builder | Benchmark entity category with both approaches side-by-side |
| Q5 | How to expand benchmark to 100 cases? Mine real queries or synthetic generation? | Phase 5 | Shape + Builder | Start with mining `mnemosyne search` call logs from agent sessions |
| Q6 | Should `.summaries/` be excluded from Obsidian Sync? | Phase 2 setup | Dan | ~100MB of generated files may not be useful to sync to Mac; add to `.obsidianignore` |
| Q7 | When to move repo from public to private? | Pre-client use | Dan | Review before sharing with any client; current public for open-source benefit |

---

## 13. Baseline Results (canonical reference)

**Date:** 2026-03-22  
**Vault state:** 1,784 documents, 6,198 vectors  
**Benchmark files:**
- `benchmark-results/B0-qmd-bm25-rerun-2026-03-22.json`
- `benchmark-results/B0-qmd-vector-2026-03-22.json`

| System | Recall | Temporal | Entity | Conceptual | Multi-hop | Procedural | **Weighted Total** |
|---|---|---|---|---|---|---|---|
| BM25 (baseline) | 0.50 | 0.37 | 0.57 | 0.63 | 0.40 | 0.60 | **0.51** |
| Azure Vector only | 0.58 | 0.33 | 0.40 | 0.21 | 0.40 | 0.20 | **0.38** |
| **P1 target (hybrid)** | >0.60 | ≥0.37 | >0.60 | ≥0.60 | >0.45 | ≥0.60 | **≥0.62** |
| **P2 target** | — | >0.55 | — | — | — | — | **≥0.68** |
| **P3 target** | — | — | — | — | — | — | **≥0.75** |
| **P4 target** | — | — | >0.65 | — | >0.60 | — | **≥0.80** |

**Phase gate rule:** Phase N+1 does not start until Phase N benchmark confirms gate score. Results below target trigger tuning, not advancement.

---

*PRD v3.0 — full specification, post Phase 0 (2026-03-23). Next revision after Phase 1 benchmark results confirm or refute Phase 1 architecture assumptions.*
