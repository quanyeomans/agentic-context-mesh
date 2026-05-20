# Reference Library — Conversation Corpora

Synthetic chat-shaped reference corpora for evaluating kairix's
conversation-paradigm retrieval (Plan B-parity Week 1+, Capability #1
through #5).

The document-shaped reference library (`reference-library/<collection>/`)
exists for the vault paradigm — markdown documents the operator might
write themselves. This subdirectory exists for the **conversation
paradigm** — turn-shaped data the operator's agents might ingest from
chat history, meeting transcripts, slack exports.

## Why a separate corpus

Conversation-shaped data has different retrieval shape than document
chunks: short utterances, scattered facts about persistent entities,
temporal references like "yesterday" / "last week", multi-turn
context required for many questions. The fact-extractor layer
(Capability #2) is specifically designed for this shape; evaluating
it against the document corpus would miss the point.

## Corpus organisation

```
reference-library/conversations/
  README.md                              this file
  <engagement-name>/
    session-001.jsonl                    one session = one jsonl file
    session-002.jsonl
    ...
    ground-truth-facts.json              canonical facts the extractor should produce
    ground-truth-queries.json            queries + expected answers for eval scoring
```

Each engagement-named directory is a synthetic multi-session
conversation between agent personas. Names are generic
(`engagement-alpha`, `engagement-beta`, …) — no real client, vendor,
or person ever appears. New corpora go in their own subdirectory;
the eval harness discovers them automatically.

## File formats

### `session-NNN.jsonl`

One turn per line. Each turn is a JSON object:

```json
{"id": "s001-t001", "speaker": "agent-alpha", "role": "user", "content": "...", "timestamp": "2026-01-15T09:00:00Z"}
```

Required keys: `id`, `speaker`, `content`. Optional: `role`,
`timestamp`. The kairix ingest path is tolerant of extra keys —
they round-trip into the document metadata.

### `ground-truth-facts.json`

Canonical facts the fact-extractor SHOULD produce when run against
the full conversation:

```json
[
  {
    "entity": "agent-alpha",
    "attribute": "current-engagement",
    "value": "engagement-alpha",
    "evidence_turn_ids": ["s001-t003", "s001-t005"]
  }
]
```

The extractor's output is compared against this list. Precision =
extractor facts ∩ ground-truth / extractor facts. Recall = matched /
ground-truth. The eval gate's fact-extraction score is F1 of these.

### `ground-truth-queries.json`

Queries + expected answers for the RAG-style scoring:

```json
[
  {
    "question": "What is agent-alpha's current engagement?",
    "answer": "engagement-alpha",
    "category": "single-hop",
    "evidence_turn_ids": ["s001-t003"]
  }
]
```

`category` is one of `single-hop`, `multi-hop`, `temporal`,
`open-domain`, `adversarial` — matches LoCoMo's taxonomy so per-
category subscores are comparable across corpora.

## How the eval gate uses this

```bash
# Score the fact extractor against ground truth
kairix eval --suite reference-library/conversations/engagement-alpha \
    --metric extractor-f1

# Score end-to-end retrieval against ground truth queries
kairix eval --suite reference-library/conversations/engagement-alpha \
    --metric query-pass-rate

# Per-PR CI gate
kairix eval --suite reference-library/conversations --regression-against expected/
```

The per-PR gate runs ALL conversation corpora in
`reference-library/conversations/` and asserts no regression against
the pinned baseline in `reference-library/conversations/expected/`.

## Confidentiality + synthesis discipline

Per the public-repo norms (no real client, vendor, person, or
organisation in any artefact):

- Personas use generic names: `agent-alpha`, `agent-beta`, `growth-coach`, …
- Engagements use generic names: `engagement-alpha`, `engagement-beta`, …
- Topics are plausible consultancy/agent-coordination shape but
  fully invented — no real product name, real deal name, real PII
- Dates are real-looking ISO timestamps but tied to fictional events

If a corpus needs to be added later, the same discipline applies.
The detect-secrets + confidential-check pre-commit hooks scan
this tree.

## Status

Scaffold + format documented (this README). Synthetic corpora
land alongside Capability #2 (the LLM fact extractor) so the
extractor and its eval gate can be iterated together.
