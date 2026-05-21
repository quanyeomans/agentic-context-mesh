# kairix/corpus/

Unified corpus-ingest layer. The shared primitive every conversational
ingest path eventually routes through: `kairix ingest-chat`, the
`SuiteRunner` reference-library evals, and the LoCoMo benchmark
harness. Spike C1 documented the three divergent paths the codebase
carried before this module landed; the goal of `kairix/corpus/` is to
collapse them onto one well-tested entry point.

## Public surface

```python
from kairix.corpus.ingest import (
    SessionPayload,        # one conversational session, source-agnostic
    IngestRequest,         # a corpus-shaped unit of work
    IngestResult,          # counters returned to operators + tests
    ingest_corpus,         # the single shared primitive
)
```

`ingest_corpus(request, *, paths, fact_store, fact_extractor, ...)`
composes the optional `DocumentWriter`, `CorpusEmbedder`, and
`ConsolidationPass` collaborators. Both `DocumentWriter` and
`CorpusEmbedder` are nullable — passing `None` is the documented
opt-out for chunks-only or facts-only modes.

## Where each piece lives

- `ingest.py` — `ingest_corpus` + the four dataclasses. Pure
  orchestration; no I/O of its own beyond what the injected
  collaborators perform.
- `__init__.py` — exports the public symbols.

Production wire-ups (Phase 2+: `MarkdownDocumentWriter`,
`EmbedPipelineEmbedder`, the `_resolve_production_*` helpers) will
land in `wiring.py` as the three call sites get migrated. P1 is the
foundation phase — only the new module + its unit tests land here.

## Where each test lives

- `tests/core/corpus/test_ingest.py` — unit tests pinning the public
  contract: dataclass round-trips, happy-path orchestration, the
  null-writer / null-embedder / null-consolidation branches, the
  Lever A `session_metadata` propagation, and the `skipped_sessions`
  partial-failure mode.
- Protocols (`DocumentWriter`, `CorpusEmbedder`) are pinned in
  `tests/contracts/` alongside the other Protocol conformance tests.

## What does NOT belong here

- CLI argument parsing — that's `kairix/use_cases/ingest_chat.py:main`
  (and equivalent dispatch points for `kairix eval`, etc.).
- Production-wire helpers (`_resolve_production_fact_store`, etc.) —
  those move into `wiring.py` in later phases.
- LoCoMo / suite-runner adapter glue — each caller keeps its own
  CLI-input concerns; the corpus layer is what they call into.
- Document-shaped (non-conversational) ingest — reference-library
  NDCG suites still go through `kairix embed`'s document-scan
  pipeline. `ingest_corpus` is for **conversational** corpora only.

## Architecture fitness functions touched

- **F22**: `kairix/corpus/*.py` snake_case. `__init__.py` and
  `ingest.py` satisfy.
- **F23**: this README is the resolver for `kairix/corpus/` — what
  belongs, what doesn't, where to find tests.
- **F26**: `kairix/corpus/` may import `kairix.core.protocols`,
  `kairix.paths`, `kairix.core.facts.consolidation`. Must NOT
  import `kairix.providers.*` or `kairix.transport.*`.

See `docs/architecture/fitness-functions.md` for the canonical
listing.
