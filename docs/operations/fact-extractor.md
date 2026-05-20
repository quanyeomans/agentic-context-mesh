# Fact extractor — operator guide

## What this is for

The fact extractor turns chat-shaped data into structured facts. When `kairix ingest-chat` reads a JSONL transcript, it slices the conversation into windows of turns (default 5) and sends each window to a language model with a prompt that asks "what entity-attribute-value claims appear here?" The model returns a small JSON array; kairix parses it, deduplicates against existing facts, and persists each new fact to the fact store with the source turn ids attached for evidence.

This is the layer that lets an agent ask "what's agent-alpha's current engagement?" and get a one-sentence answer with a citation, rather than getting back a 1,500-token blob of meeting transcript and having to read it themselves.

## How it works

The pipeline runs in `ingest_chat` (see `kairix/use_cases/ingest_chat.py`) and decomposes into four stages:

1. **Window** — slice the turn sequence into non-overlapping windows of `--window-turns` turns (default 5).
2. **Extract** — for each window, call `LLMFactExtractor.extract(turns=window)`. The extractor:
   - Loads the bundled prompt template at `kairix/core/facts/prompts/fact_extractor_v1.txt`.
   - Renders the window into the prompt's `{{turns}}` placeholder.
   - Calls the configured LLM backend via `LLMBackend.chat()` (whichever provider plug-in is wired in `kairix.config.yaml`).
   - Parses the response as a JSON array; tolerates malformed output (logs WARNING and skips).
   - Each emitted fact carries `entity`, `attribute`, `value`, `confidence`, `evidence_turn_ids`.
3. **Stamp namespace** — every fact gets the `--namespace` engagement tag stamped on (defaults to `shared`).
4. **Consolidate** — for each newly-extracted fact, `ConsolidationPass` queries the fact store for prior facts with the same `(entity, attribute)` and checks whether the new value contradicts. If yes, the older fact is marked `superseded_by=<new_id>`. The old fact stays retrievable for audit; default search excludes superseded rows.

Facts persist to a SQLite database (`SQLiteFactStore`) — `<data_dir>/kairix/facts.sqlite` by default — keyed on a deterministic id derived from `(entity, attribute, source_turn_ids)`. Re-ingesting the same window is idempotent.

## When to enable it

**Enable it (default) when:**
- You're ingesting meeting transcripts, chat exports, or interview notes where the same entity appears across many turns and you want agents to query facts directly.
- You want agents to detect contradictions ("we decided X in Q1, but this conversation says we're doing Y now").
- You can afford one LLM call per window of turns.

**Disable it (`--no-extract`) when:**
- You're pre-flighting the transcript shape (does the JSONL parse? are the windows sensible?) before paying for the LLM pass.
- You're indexing chat for *retrieval only* — you want the chunks searchable but you don't need structured facts.
- The transcript is large and you want to validate document-level retrieval first, then re-ingest with extraction once the document chunks look good.

## Cost model

One LLM call per window. A 100-turn transcript with the default `--window-turns=5` runs 20 extraction calls. Each call:

- Prompt: ~600 tokens of instructions + ~`window_turns × avg_turn_tokens` of conversation content. For 5 turns at ~80 tokens/turn that's ~1,000 tokens prompt.
- Completion: capped at `_MAX_TOKENS=2000` in `extractor.py`. Typical responses are far smaller (200–500 tokens) — most windows surface 0–3 facts.

So budget roughly **(0.5–1.5K prompt + 0.2–0.5K completion) × (turn_count / window_size)** tokens per transcript. At GPT-4-class pricing that's a few cents per 100 turns; at Bedrock Haiku pricing it's well under a cent.

**Concurrency.** The extractor calls the LLM sequentially per window, but `LLMBackend` implementations may pool / coalesce concurrent calls if the underlying provider plug-in supports it. See `docs/architecture/provider-plugin-architecture.md` for the transport-layer pooling story.

**Tuning the window.** Larger `--window-turns` gives the model more context (so it can attribute "she said" to the right speaker) but costs more tokens per call. The sweet spot for most consultancy-shape conversations is 5–8 turns. Single-utterance windows (`--window-turns=1`) usually under-extract because the model can't see the context.

## Prompt customisation

The default prompt lives at:

```
kairix/core/facts/prompts/fact_extractor_v1.txt
```

It's bundled as Python package data and loaded via `importlib.resources.files`. To customise:

1. Copy the file to `kairix/core/facts/prompts/fact_extractor_<your-id>.txt`.
2. Edit the system instructions, the few-shot examples, or the output schema (be careful — the parser keys on `entity`, `attribute`, `value`, `confidence`, `evidence_turn_ids`; renaming these breaks `extractor.py`).
3. Wire it through `LLMFactExtractor(llm=..., prompt_template=<your-loaded-string>)` in a CLI override or factory.

The packaging is `importlib.resources` so this works from both an editable install and a built wheel.

Why a file rather than a yaml setting: prompts are version-controlled artefacts. A bad prompt regresses your fact-extraction quality silently; treating them as code means changes go through code review, get unit-tested against the gold corpus, and get cleared by the eval gate before they ship.

## Namespace hygiene

Every extracted fact gets a namespace tag. Default is `shared`; pass `--namespace engagement-alpha` to scope facts to a specific engagement. Cross-namespace queries are explicit — by default, queries see only their own namespace plus `shared`.

If you accidentally ingest a transcript without the namespace flag and the facts end up in `shared`, you can re-scope them with:

```bash
kairix entity purge --namespace shared --entity-pattern '<pattern>'
```

Then re-ingest with the correct `--namespace`.

## Troubleshooting

**`kairix ingest-chat` runs but `facts added: 0`.**

The LLM is returning `[]` (no facts found in the window) for every window. `fix:` check the kairix log at DEBUG level — `LLMFactExtractor` logs the raw response per call. If responses look reasonable but you genuinely have no extractable facts, this is normal for casual chitchat windows. `next:` if responses are malformed, see "LLM output is malformed" below.

**`facts added: 0, windows extracted: N` with N > 0 — but the LLM log shows real responses.**

The parser is rejecting the responses for missing required keys. `fix:` check the prompt template — every example must produce `entity`, `attribute`, `value`, `confidence`, `evidence_turn_ids`. Missing one key drops the row with a WARNING. `next:` add `-v DEBUG` to the kairix command to see exactly which key is missing per row.

**`facts superseded` count is unexpectedly high.**

Every ingest is rewriting prior facts. Either the model's outputs are noisy (so similar-but-not-identical values keep displacing each other) or you genuinely ingested a contradiction-rich conversation. `fix:` look at the consolidation log entries — they print the old + new value per supersession. `next:` if the noise is real, raise the extractor's confidence floor or tighten the prompt's "only extract claims with explicit evidence" instruction.

**LLM output is malformed.**

The extractor never raises on output shape; it logs WARNING and skips. `fix:` run with `--no-extract` first to confirm the transcript itself is fine, then check the LLM provider plug-in — most often the model is returning trailing prose around the JSON array. `next:` if the provider supports it, add `response_format=json` to the backend config (check the provider plug-in docs).

**Cost is higher than expected.**

Most likely `--window-turns` is small. `fix:` raise it to 8 or 10 — fewer calls per transcript. `next:` if cost is still high, profile via `kairix probe-config --perf` to see per-call latency + token counts.

## See also

- [consultancy-in-a-box.md](consultancy-in-a-box.md) — end-to-end engagement workflow that uses ingest-chat
- [eval-suite.md](eval-suite.md) — measuring extractor F1 against a ground-truth corpus
- [MCP-ingest-tools.md](MCP-ingest-tools.md) — calling ingest from an agent over MCP
- [`docs/architecture/fact-layer.md`](../architecture/fact-layer.md) — ADR + design
- `kairix/core/facts/prompts/fact_extractor_v1.txt` — the default prompt
- `kairix/core/facts/extractor.py` — the extractor implementation
- `reference-library/conversations/README.md` — corpus format for testing extractor quality
