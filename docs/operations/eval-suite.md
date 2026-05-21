# Eval suite — running and reading `kairix eval`

## What this is for

`kairix eval` scores retrieval and fact-extraction quality against a ground-truth corpus. You point it at a directory containing one or more `session-NNN.jsonl` transcripts plus a `ground-truth-queries.json` (and optionally `ground-truth-facts.json`), and it reports per-category pass-rates, mean score, and — if you want — fails the run when the score regresses against a pinned baseline. That regression-gate pattern is what makes `kairix eval` viable as a CI gate: you pin a baseline once, then every PR that changes retrieval or extraction code must hold the line.

This is the layer that lets you decide objectively whether a prompt edit, a retrieval-weight tweak, or a new provider plug-in made your agents better or worse.

## Walkthrough

### 1. Build the suite directory

A suite is a directory with this layout (the reference library at `reference-library/conversations/engagement-alpha/` is the canonical example):

```
my-suite/
  session-001.jsonl              one session = one jsonl, one turn per line
  session-002.jsonl
  ground-truth-queries.json      queries + expected answers
  ground-truth-facts.json        (optional) canonical facts the extractor should produce
```

The JSONL format and ground-truth file shapes are documented in [`reference-library/conversations/README.md`](../../reference-library/conversations/README.md). Use generic engagement names — no real client names in the suite.

### 2. Run the suite

```bash
kairix eval reference-library/conversations/engagement-alpha
```

Default output is human-readable:

```
Suite: engagement-alpha (path=reference-library/conversations/engagement-alpha)
  Questions  : 18/20 (90%)
  Mean score : 0.873
  By category:
    multi-hop      6/7  (86%) mean=0.821
    single-hop    10/10 (100%) mean=0.940
    temporal       2/3  (67%) mean=0.703
  Extractor F1: 0.84 (precision 0.91, recall 0.78)
```

For machine-readable output, pass `--json`. The full `SuiteResult` dataclass serialises with per-category stats and per-extraction breakdown.

### 3. Pick a metric

```bash
# Score retrieval / RAG quality only
kairix eval my-suite --metric query-pass-rate

# Score fact-extractor F1 against ground-truth-facts.json only
kairix eval my-suite --metric extractor-f1

# Both (default)
kairix eval my-suite --metric both
```

`query-pass-rate` measures end-to-end retrieval: each question runs through the search pipeline, the answer is judged against the expected answer, and the per-question score rolls up into a mean. `extractor-f1` runs the LLM fact extractor over every session and compares the emitted facts to `ground-truth-facts.json` — precision is `extractor ∩ ground-truth / extractor`, recall is `matched / ground-truth`, F1 is the harmonic mean.

### 4. Pick a backend

```bash
# Default — the production kairix retrieval + fact stack
kairix eval my-suite --backend kairix-native

# Compare against the mem0 backend (LoCoMo nightly workflow uses this)
kairix eval my-suite --backend mem0
```

The `--backend` flag is how you sanity-check claims like "kairix beats mem0 on multi-hop." Run the same suite through both backends, then diff the per-category breakdown.

## Regression-gate pattern

Once a suite is healthy, pin a baseline:

```bash
mkdir -p reference-library/conversations/expected
kairix eval reference-library/conversations/engagement-alpha --json \
  > reference-library/conversations/expected/engagement-alpha.json
```

The baseline file is a serialised `SuiteResult` keyed on the suite name. Commit it to the repo.

From then on, every PR that touches retrieval or extraction code runs:

```bash
kairix eval reference-library/conversations/engagement-alpha \
  --regression-against reference-library/conversations/expected
```

Exit code:

- `0` — score is within 2 percentage points of the baseline (the `_REGRESSION_TOLERANCE_PP` constant in `kairix/use_cases/eval_suite.py`).
- `1` — score regressed more than 2pp. The PR fails its gate.
- `2` — operator error (missing suite, missing baseline). The error envelope carries `fix:` / `next:` markers.

When the regression is real and intentional (e.g. you raised the extractor's confidence floor and accepted a recall hit for cleaner outputs), re-pin the baseline in the same PR. Reviewers can diff the `expected/<suite>.json` to see exactly which categories shifted.

## Integrating into CI

The Plan B-parity Week 4 Stream A workflow lands `.github/workflows/conversation-eval.yml`, which runs `kairix eval --regression-against` on every PR that touches retrieval or extraction code. The LoCoMo nightly workflow (the broader multi-backend benchmark) lives at `.github/workflows/locomo-nightly.yml`.

To add a new suite to the gate:

1. Drop `your-suite/` into `reference-library/conversations/`.
2. Build a baseline (`kairix eval your-suite --json > expected/your-suite.json`).
3. Commit both.

The workflow auto-discovers every directory under `reference-library/conversations/` that has a `ground-truth-queries.json` — no workflow edit needed.

## Reference-library / conversations format

Suites use the same JSONL + ground-truth shapes as the seeded reference library. The full reference is `reference-library/conversations/README.md`; the short version:

- `session-NNN.jsonl` — one turn per line, JSON object with `id`, `speaker`, `content`, optional `role`, `timestamp`.
- `ground-truth-facts.json` — array of `{entity, attribute, value, evidence_turn_ids}` objects the extractor should produce.
- `ground-truth-queries.json` — array of `{question, answer, category, evidence_turn_ids}` objects. `category` is one of `single-hop`, `multi-hop`, `temporal`, `open-domain`, `adversarial` (matches LoCoMo's taxonomy).

Five seeded corpora ship: `engagement-alpha` (single-hop heavy), `engagement-beta` (multi-hop), `engagement-gamma` (multi-session strategy), `engagement-delta` (contradiction-rich), `engagement-epsilon` (temporal-heavy). Start with one of these as a template when you build a suite for your own engagement.

## Customisation knobs

| Knob | Effect | Where |
|------|--------|-------|
| `--metric` | `query-pass-rate` / `extractor-f1` / `both` (default) | CLI flag |
| `--backend` | `kairix-native` (default) or `mem0` for cross-backend benchmarking | CLI flag |
| `--regression-against <dir>` | Fail (exit 1) if mean score drops >2pp vs the pinned baseline in `<dir>` | CLI flag |
| `--json` | Emit the full `SuiteResult` dataclass as JSON (for CI parsing or baseline pinning) | CLI flag |
| Per-category passing threshold | Set in the runner; modify `SuiteRunner` if you want stricter per-category gates | code (`kairix/quality/eval/suite_runner.py`) |

## Troubleshooting

**`kairix eval: suite directory has no session-*.jsonl files`.**

The suite path doesn't point at a directory of sessions. `fix:` confirm the path with `ls <suite>/session-*.jsonl`. `next:` if the sessions live one level deeper, point `--suite` at that subdirectory.

**`kairix eval: baseline file not found: <dir>/<suite>.json`.**

You passed `--regression-against` but never built the baseline. `fix:` re-run without `--regression-against`, redirect `--json` output to `<dir>/<suite>.json`, then re-run with the flag. The error envelope already includes this fix.

**Mean score regressed but I think the change was an improvement.**

The 2pp tolerance is conservative. `fix:` re-pin the baseline in the same PR (`kairix eval <suite> --json > expected/<suite>.json`) and explain in the commit body why the score moved. Reviewers can diff the baseline file. `next:` if the regression is on one category only (e.g. temporal dropped but multi-hop improved), call that out — the per-category breakdown is the real signal.

**`kairix eval --backend mem0` errors with "mem0 backend not configured."**

The mem0 backend uses the `mem0` Python package; it's an optional dependency. `fix:` `pip install kairix[mem0]` then re-run. `next:` if you don't need mem0 comparisons, stick with the default `kairix-native` backend.

**Extractor F1 is much lower than `query-pass-rate`.**

The extractor and the retriever score different things — extractor F1 is "did the LLM produce the canonical fact set?" while query-pass-rate is "could the retrieval pipeline answer the question?" `fix:` if extractor F1 is low but queries pass, the retriever is making up for it via chunk-based RAG; that's fine for now. `next:` if you need higher extractor F1, iterate the prompt at `kairix/core/facts/prompts/fact_extractor_v1.txt` and re-eval.

## See also

- [consultancy-in-a-box.md](consultancy-in-a-box.md) — operator workflow that uses eval as the validation step
- [fact-extractor.md](fact-extractor.md) — extractor mechanics + cost model
- [`docs/architecture/fact-layer.md`](../architecture/fact-layer.md) — ADR
- [`reference-library/conversations/README.md`](../../reference-library/conversations/README.md) — full suite format reference
- [`docs/evaluation/EVALUATION.md`](../evaluation/EVALUATION.md) — broader evaluation methodology (document corpora, gold-suite generation)
- `kairix/use_cases/eval_suite.py` — CLI source
- `kairix/quality/eval/suite_runner.py` — runner internals
