# Pinned eval baselines (Plan B-parity Week 4 Stream A)

This directory holds the per-corpus baselines that the
`1c · Conversation eval gate` CI job compares every PR against.

## Layout

One JSON file per `reference-library/conversations/engagement-*` corpus:

```
expected/
  engagement-alpha.json    # full SuiteResult — gate enforces no >2pp regression
  engagement-beta.json     # sentinel — gate runs in "establishing baseline" mode
  engagement-gamma.json    # sentinel
  engagement-delta.json    # sentinel
  engagement-epsilon.json  # sentinel
```

## File shapes

**Full baseline** — round-trip of `SuiteResult` (see
`kairix.quality.eval.suite_runner.SuiteResult`):

```json
{
  "suite_name": "engagement-alpha",
  "n_questions": 8,
  "n_passed": 1,
  "mean_score": 0.125,
  "per_category": { ... },
  "per_extraction_f1": null,
  "extraction_precision": null,
  "extraction_recall": null
}
```

**Sentinel** — corpus has not been baselined yet. The CI gate detects this
shape and records the run as "establishing baseline" (no regression gate
applied):

```json
{ "baseline": "not-yet-measured", "_note": "..." }
```

## How to pin a new baseline

After a green run on a corpus that's currently sentinel:

```bash
kairix eval reference-library/conversations/engagement-beta --json \
  > reference-library/conversations/expected/engagement-beta.json
```

Commit the diff. The next PR run will then assert no >2pp regression
against that pinned floor.

## How to refresh an existing baseline

Only when the score *improves* and the new floor should be enforced:

```bash
kairix eval reference-library/conversations/engagement-alpha --json \
  > reference-library/conversations/expected/engagement-alpha.json
```

Never widen the tolerance to paper over a regression — fix the underlying
recall/extractor delta first, then re-pin.
