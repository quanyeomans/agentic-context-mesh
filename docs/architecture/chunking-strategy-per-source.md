# Chunking strategy + per-source quality baseline (ADR-028 measurement)

This is the **measurement baseline** for ADR-028's per-type chunking work: how to
measure chunk quality per source type, the production baseline captured before the
cutover, and the per-type targets the registry chunkers move toward.

## How to measure

```sh
kairix eval chunk-stats --db-path /var/lib/kairix/index.sqlite
```

emits, per source type, the chunk-size distribution (`mean / p50 / p95 / p99`) plus
an **embed-truncation-risk** block (`kairix/quality/eval/chunk_stats.py`). Retrieval
quality per source type comes from the benchmark runner, which already slices NDCG
by source type:

```sh
kairix benchmark run --suite <suite.yaml>   # summary["per_source_type"]
```

Together these answer "are this source's chunks the right size, and does retrieval
on it hold up?" without inspecting the index by hand.

## Production baseline (2026-06, Customer-Zero VM, ~2.26M chunks)

| source type        | chunks  | avg chars | max chars | assessment |
|--------------------|---------|-----------|-----------|------------|
| sharepoint         | ~2.17M  | 832       | 1000      | well-bounded by the Silver paragraph chunker |
| linear (projects)  | ~1.3K   | 715       | ~1000     | well-bounded |
| **projects**       | small   | 16,899    | **926,384** | **oversized** — whole-document chunks |
| **entity-summaries** | small | 21,737    | **926,384** | **oversized** — whole-document chunks |

**Finding.** `text-embedding-3-large` truncates silently at its 8191-token limit
(~32K chars at the production ~4 chars/token density). The `projects` and
`entity-summaries` collections carry chunks far above that — they embed only their
first ~32K chars and the tail never reaches retrieval. `chunker_version` is `None`
(no per-type chunker ever stamped them), confirming they never went through a
bounded structural chunker. `chunk-stats` now flags this directly.

## Per-type targets

| source type           | chunker (registry)        | target |
|-----------------------|---------------------------|--------|
| markdown (obsidian/notion/github) | `MarkdownStructuralChunker` | heading-aware sections, ~512 tokens, overlap within a section |
| DOCX                  | `DocxHeadingChunker`      | heading-aware |
| PPTX                  | `SlideChunker`            | one chunk per slide *(page-path; per-page dispatch is a later wave)* |
| XLSX                  | `SheetRowChunker`         | one chunk per row + header *(page-path; later wave)* |
| Slack                 | `ThreadChunker`           | ~500 tokens / thread window |
| email                 | `EmailThreadChunker`      | per message |
| calendar              | `CalendarEventChunker`    | one chunk per event |
| anything unregistered | bounded paragraph fallback | ≤ ~1000 chars |

Every registry chunker is **bounded**, so routing a source through the registry
(rather than emitting a whole document as one chunk) removes the truncation cliff.

## How the cutover addresses this

The per-type chunker registry exists (`build_default_registry`) and is wired into
Silver behind the `chunker_registry_dispatch_enabled` flag (default OFF — see the
flag description for the flip caveats). When ON, passthrough markdown / DOCX
dispatch to the registry's bounded per-type chunkers and chunks carry the per-type
`chunker_version`, so a re-chunk sweep can find and retire the old oversized chunks.

**Reproduce after a flip:** run `kairix eval chunk-stats` again — the
embed-truncation-risk block should empty out for the cutover sources once they have
re-chunked + re-embedded.
