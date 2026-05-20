# Fact layer — architecture decision record (Plan B-parity)

## What this is for

This ADR captures the design of kairix's structured fact layer — the protocols, components, and contracts that let kairix turn chat-shaped data into evidence-backed claims, persist them through engagement lifecycles, and surface them alongside chunk-based retrieval. It exists so a reader can answer four questions in one read:

1. Why did kairix add a fact layer when it already had document chunks and entities?
2. What are the layer's components and how do they compose?
3. Which "surface" does each component live on?
4. Where do I go from here to read the deeper design?

## Why the fact layer exists

Chunk retrieval is great for "give me the relevant paragraph"; it's poor for "what is agent-alpha's current engagement?" The agent has to read 1,500 tokens of chunks and infer the answer, which burns context tokens and is brittle when the answer is scattered across many turns.

Plan B-parity adds a fact layer so:

- **Agents can ask structured questions** ("`facts_about(agent-alpha)`") and get a small evidence-cited answer set — typically <200 tokens.
- **Contradictions are detectable**: when a conversation revises a prior claim, the consolidation pass marks the old fact superseded rather than silently leaving both in the index.
- **Retrieval federates**: the `SearchPipeline` blends fact-store hits with chunk hits so `kairix prep` answers benefit from both.

This is *not* a replacement for chunk retrieval. Chunks remain the canonical store for free-text; facts are a distillation layer over the same conversation transcripts. The two layers cite each other (every fact's `evidence_turn_ids` point back at the source turns).

## Surfaces

Plan B-parity organises the fact layer into four surfaces, each owning a distinct concern:

- **Surface A — capture.** The `kairix ingest-chat` use case reads JSONL transcripts, writes one markdown chunk per conversation, and (when extraction is enabled) feeds windowed turns through the LLM fact extractor. This is where chat data first enters the system as structured records.
- **Surface B — persistence.** `SQLiteFactStore` (and its `FactStore` Protocol) own all reads and writes against the fact database. The store is namespace-aware, supports supersession for contradictions, and is idempotent on a fact's deterministic id.
- **Surface C — consolidation.** `ConsolidationPass` is the rule engine that runs on every newly-extracted fact: query prior facts with the same `(entity, attribute)`, run a `Contradict` predicate, and supersede the old when the new fact wins. The `default_contradict` predicate ships with conservative semantics (value mismatch + recent extracted_at).
- **Surface D — federation.** The `SearchPipeline` integrates a `FactRetriever` alongside its existing chunk + entity retrievers so a single `prep` / `search` call returns a blended result set. From the agent's perspective there's one tool call; from the system's perspective two retrieval surfaces converge.

The split exists so each surface can evolve independently. Swapping the extractor (Surface A) doesn't touch the consolidation rules (Surface C). Moving the fact store from SQLite to Postgres (Surface B) is a Protocol substitution that needs no edits to Surfaces A / C / D.

## The five capabilities

Plan B-parity lands as a numbered capability series. The numbering is the order in which the capabilities were merged; readers asking "what's where in the codebase?" should treat this as the index:

1. **Capability #1 — `ingest-chat` use case.** `kairix/use_cases/ingest_chat.py`. Parses JSONL, groups by conversation, writes markdown chunks, drives Surface A. CLI: `kairix ingest-chat <jsonl>`.
2. **Capability #2 — `LLMFactExtractor`.** `kairix/core/facts/extractor.py`. Production `FactExtractor` Protocol implementation; bundled prompt template at `kairix/core/facts/prompts/fact_extractor_v1.txt`. Tolerant of malformed LLM output.
3. **Capability #3 — `SQLiteFactStore`.** `kairix/core/facts/store.py`. `FactStore` Protocol implementation backed by SQLite; namespace-aware; supports `find_conflicts` + `supersede` for the consolidation flow.
4. **Capability #4 — `ConsolidationPass` + `kairix eval` suite runner.** `kairix/core/facts/consolidation.py` and `kairix/use_cases/eval_suite.py`. The rule engine (Surface C) and the eval gate that scores extractor + retrieval quality against ground-truth corpora.
5. **Capability #5 — fact federation in `SearchPipeline`.** Edits to `kairix/core/search/pipeline.py` so a `FactRetriever` runs alongside the existing retrievers and contributes to the blended ranking. Surface D, hidden behind the existing search API.

## The federation pattern

Capability #5 is worth highlighting because it's the surface most likely to surprise readers: kairix did not add a new top-level "fact search" CLI. Instead the existing `SearchPipeline` grew a `FactRetriever` injection point. When an operator runs `kairix prep "what's the current pricing strategy?"`, the pipeline:

1. Runs the chunk retriever (BM25 + vector).
2. Runs the entity retriever (graph lookup).
3. Runs the fact retriever — a new step that queries `FactStore` for facts whose `(entity, attribute, value)` plausibly answers the question.
4. Blends, ranks, and budgets the combined result set under a single response envelope.

From the agent's perspective the tool call is the same as before. From the operator's perspective there's no new endpoint to monitor. The cost is one additional database query per search (microseconds, vs. the millisecond-scale chunk retrieval and the much larger LLM-extract-on-ingest cost). Performance budgets for the federated pipeline live in `kairix/quality/probe/` and surface through `kairix probe-config --perf`.

## Why these design choices

**Protocols first.** Every component on every surface targets a Protocol in `kairix/core/protocols.py` — `FactRecord`, `FactStore`, `FactExtractor`, `FactRetriever`. Production wires the concrete implementations; tests inject `FakeFactStore` / `FakeFactExtractor` from `tests/fakes.py`. F1 is enforced — no monkeypatching, no internal-attribute reassignment. The cost is a small extra constructor surface in `ingest_chat` and `eval_suite`; the benefit is that swapping any layer for an alternate implementation (SQLite → Postgres, LLM extractor → rule-based extractor) is a Protocol substitution with no test rewrite.

**SQLite for persistence.** Same reasoning as the existing kairix chunk + entity stores — one file per engagement, zero ops, atomic teardown via `docker compose down -v`. The fact volume per engagement is small (thousands to tens of thousands of facts, not millions); SQLite is comfortable in that range.

**Supersession over deletion.** Contradicted facts are marked `superseded_by` rather than deleted. Default search excludes them; audit queries can include them with a future `include_superseded=True` kwarg. This preserves the evidence chain — an agent revisiting "why did we change our minds about pricing?" can see both the old and new claim and the turn that triggered the change.

**Out-of-band embed.** `kairix ingest-chat` writes markdown chunks but does not embed them. The operator runs `kairix embed` separately afterwards. This keeps the ingest path cheap and re-runnable; it also lets the operator re-embed (after a model change) without re-ingesting (and vice versa).

## Open questions for next iterations

- **Confidence calibration.** The extractor returns `confidence` per fact but the consolidation rule doesn't yet use it. A future pass should weight confidence into the supersession decision.
- **Cross-namespace federation.** Facts are namespace-scoped today. A controlled-disclosure flow for "show me facts from this engagement plus everything tagged `shared`" exists; finer-grained cross-namespace queries (e.g. "facts about agent-alpha across all engagements they appeared in") need a separate design.
- **Streaming ingest.** `kairix ingest-chat` is batch (one JSONL → one ingest pass). A streaming ingest endpoint (one turn arrives → one extraction call → one consolidation pass) is in scope but not yet on the roadmap.

## Vault decision docs

The full design discussion — including alternatives considered, the decision log, and the per-week execution plan — lives in the project's internal knowledge store under `02-Areas/02-Three-Cubes-Ventures/Kairix-Platform/Delivery/Sprints/`. The public repo carries the ADR (this file) and the operator-facing docs only.

## See also

- [`docs/operations/consultancy-in-a-box.md`](../operations/consultancy-in-a-box.md) — operator workflow that exercises every surface
- [`docs/operations/fact-extractor.md`](../operations/fact-extractor.md) — Surface A operator guide
- [`docs/operations/eval-suite.md`](../operations/eval-suite.md) — Capability #4 eval gate
- [`docs/operations/MCP-ingest-tools.md`](../operations/MCP-ingest-tools.md) — agent-callable MCP surface
- [`docs/architecture/ENGINEERING.md`](ENGINEERING.md) — broader architecture (Protocols, pipelines, factories)
- [`docs/architecture/provider-plugin-architecture.md`](provider-plugin-architecture.md) — three-layer split that the fact layer composes through
- `kairix/core/protocols.py` — `FactRecord`, `FactStore`, `FactExtractor`, `FactRetriever` Protocols
- `kairix/core/facts/` — Surface B + C implementations
- `kairix/use_cases/ingest_chat.py` — Surface A use case
- `kairix/use_cases/eval_suite.py` — Capability #4 eval CLI
