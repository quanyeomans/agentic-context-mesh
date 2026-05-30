# ADR-028 — Per-type chunking strategy + chunking quality evaluation

**Status:** Proposed
**Date:** 2026-05-30
**Supersedes:** none
**Superseded by:** none
**Tracking:** GH #353 (implementation tracking issue created alongside this ADR)
**Related:** ADR-024 (test pyramid), ADR-027 (entity enrichment — chunking quality improves entity-extraction recall), ADR-021 (per-source metadata normalisation), [`fitness-functions.md`](./fitness-functions.md) F55 (chunker version threading)

## Context

Every document type kairix ingests today — Obsidian markdown, SharePoint PDF/DOCX/PPTX/XLSX/.xls/.xlsm/.xlsb, Microsoft 365 calendar + email, Slack messages, GitHub READMEs + code, Notion pages, the reflib benchmark corpus — routes through a single chunker: `ParagraphFallbackChunker` in [`kairix/core/connectors/chunker_registry.py`](../../kairix/core/connectors/chunker_registry.py). It targets ~1000 chars per chunk via a greedy-glue paragraph merge with sentence → word → char fallback for oversize paragraphs.

The only per-type differentiation is page-awareness: extractors for paginated formats (PDF, PPTX, DOCX, XLSX) populate `extracted.pages` and `DefaultSilverProcessor._chunk_pages()` chunks each page independently so chunks don't straddle page boundaries. Flat formats chunk linearly with no structural awareness.

The chunker registry is dispatch-shaped (`(kind, mime, section_kind) → chunker`) but the registry table is empty at launch — everything falls through to the default. F55 enforces `chunker_version` plumbing through to `documents_media.chunker_version` so the *infrastructure* is ready; the *plugins* aren't. There is no re-chunk-sweep mechanism if a chunker bumps version.

The evaluation surface has the same shape. F55 / F38 tests verify the architecture (chunking is centralised, version is threaded). BDD features verify generic structural invariants (chunks carry `source_uri`, `sensitivity`, `page` numbers). The recall benchmark reports `NDCG@10 = 0.8385` across six query categories but **does not slice by document source type or extractor**. The reflib benchmark corpus is markdown-only — it has never exercised PDF/DOCX/XLSX/Slack chunking quality through the retrieval path. There are no golden-file tests of the shape "this PPTX should produce N slide-bounded chunks averaging X chars", no chunk-size distribution metrics, no boundary-spanning canary suite.

**Net consequence:** we ship per-type extractors (PDF / DOCX / PPTX / XLSX-aware) into a single-strategy chunker and have no way to measure whether the result is good for any specific type. Wave F was scheduled to land per-type plugins but stalled — this ADR consolidates Wave F + the evaluation gap into one coherent plan.

## Decision

Adopt a **two-track plan**:

* **Track 1 — Per-type chunker plugins.** Ship six structural chunkers behind the existing registry dispatch, plus a contextual-prepending optional layer. Defaults below are grounded in 2024-2026 RAG-platform consensus (LangChain, LlamaIndex, Unstructured, Anthropic, Pinecone, Weaviate, Firecrawl, NVIDIA chunking benchmark).
* **Track 2 — Chunking quality evaluation.** Extend the eval harness with per-source-type recall@k slices, a boundary-spanning canary suite, and chunk-size distribution metrics. Add SharePoint + Slack + email + calendar fixtures to the reflib corpus so the benchmark actually exercises the surfaces operators use.

Both tracks gate on a re-chunk-sweep tick (Track 3) that processes a `chunker_version` bump on the worker's maintenance cycle rather than waiting for operator-triggered `embed --force`.

## Per-type chunking specification

Recommended defaults, with the source for each. The kairix recommendation column is what the chunker plugin should ship as its default; per-deployment overrides happen through `kairix.config.yaml` connector entries.

| Type | Boundary unit | Target size | Overlap | Contextual prepend | Plugin name |
|---|---|---|---|---|---|
| Markdown (Obsidian, Notion, wiki) | Heading hierarchy (H1>H2>H3), recursive fallback for oversize section | 512 tokens | 10–15% (50–75 tokens) | Yes (high lift on short notes) | `MarkdownStructuralChunker` |
| PDF (born-digital) | Layout block → page → recursive | 512–1024 tokens | 10–15% | Yes | `PdfLayoutChunker` |
| PDF (scanned / OCR) | Page-as-chunk, sub-split only if >1024 tokens | 1 page = 1 chunk | None across pages | Yes (OCR noise floor needs context) | `PdfLayoutChunker` (OCR branch) |
| DOCX | Heading hierarchy, tables indexed separately | 512–1024 tokens | 10–15% | Optional (high-value for long docs) | `DocxHeadingChunker` |
| PPTX | One slide = one chunk (canonical, no exceptions) | Whole slide (~50–300 tokens typical) | None | Yes (slides need parent-deck context) | `SlideChunker` |
| XLSX (tabular, ≥50 rows) | One row = one chunk, header row prepended | 1 row + header | None | Yes (rows lose meaning without sheet purpose) | `SheetRowChunker` |
| XLSX (small reference, <50 rows) | Whole sheet = one chunk | Whole sheet | None | Yes | `SheetRowChunker` (small-sheet branch) |
| .xlsm / .xlsb | Same as XLSX modern | — | — | — | `SheetRowChunker` (needs `openpyxl` for .xlsm, `pyxlsb` for .xlsb — separate extractor wave) |
| Slack / chat | Thread = primary chunk; >500 tokens splits by token cap | ~500 tokens | 0–50 tokens | Yes (short messages = worst case for embeddings) | `ThreadChunker` |
| GitHub code | Language-aware split on `class`/`def`/etc. (tree-sitter via LangChain language splitters) | 1000 chars / ~250 tokens | 50–100 tokens (low, to avoid signature dup) | Yes | `CodeChunker` |
| GitHub prose (README, commits) | Markdown heading → recursive | 512 tokens | 10–15% | Yes | `MarkdownStructuralChunker` |
| Email (M365 + others) | Thread = document, message = chunk, quoted reply stripped | Cap at 1024 tokens, sub-split if exceeded | 0–50 tokens | Yes (subject + thread + date as prefix) | `EmailThreadChunker` |
| Calendar event | One event = one chunk (no split) | Whole event (~100–300 tokens) | None | Yes (5-word titles need attendee+project context) | `CalendarEventChunker` |

### Markdown — `MarkdownStructuralChunker`

LangChain's [`MarkdownHeaderTextSplitter`](https://docs.langchain.com/oss/python/integrations/splitters/markdown_header_metadata_splitter) is the canonical primitive: split on `#`/`##`/`###` boundaries, write the header path into each chunk's metadata, apply overlap *only within* an over-large section (never across header boundaries — this stops Topic A bleeding into Topic B). Standard pairing is `MarkdownHeaderTextSplitter → RecursiveCharacterTextSplitter(chunk_size=512, chunk_overlap=64)` for oversize sections ([Weaviate chunking guide](https://weaviate.io/blog/chunking-strategies-for-rag), [LlamaIndex structured-document recipe](https://www.llamaindex.ai/glossary/document-chunking-strategies)).

**Failure mode of today's 1000-char fallback on markdown:** (a) H2/H3 orphaned from their bullets — chunk says "key risks: …" with no recoverable parent heading; (b) wikilinks (`[[link]]`) cut mid-token, breaking lexical match; (c) YAML front-matter polluting body embeddings.

### PDF — `PdfLayoutChunker`

Two branches: born-digital and scanned/OCR.

**Born-digital:** layout-aware parse (Docling, Marker-PDF, PyMuPDF4LLM are the top open-source pipelines per [Firecrawl 2026 survey](https://www.firecrawl.dev/blog/best-pdf-parsers)) → markdown → header split → recursive at 512–1024 tokens with 10–15% overlap.

**Scanned/OCR:** page-as-chunk. NVIDIA's 2024 chunking benchmark found page-level chunking won on paginated documents (0.648 accuracy, lowest variance). Do *not* overlap across page boundaries — table or argument that spans pages gets sliced ([NVIDIA chunking benchmark via substack](https://nandigamharikrishna.substack.com/p/rag-chunking-strategies-and-embeddings)). The "layout error cascading" failure mode — minor layout-detection mistakes causing catastrophic OCR garbage downstream — is what makes contextual prepending essential here ([LlamaIndex PDF OCR analysis](https://www.llamaindex.ai/blog/pdf-character-recognition)).

### DOCX — `DocxHeadingChunker`

Convert to markdown (via Unstructured or Pandoc), split on heading hierarchy with metadata-tagged section path, **index tables as separate chunks** rather than linearising into prose. Unstructured's `by_title` strategy combines consecutive elements up to the size cap while never crossing a heading boundary, emitting separate `Table`/`TableChunk` elements ([Unstructured chunking best practices](https://unstructured.io/blog/chunking-for-rag-best-practices)).

**Failure modes of today's fallback:** tables linearised into prose lose row/column semantics; numbered-list continuations severed from parent; "5.2.1 Risk register" loses the H1 "Chapter 5: Compliance" context.

### PPTX — `SlideChunker`

One slide = one chunk. Always. Capture title, slide number, speaker notes, OCR'd image text, table text into a single chunk per slide. No overlap (slide boundary *is* the unit). Consensus across Unstructured's `by_page`, LlamaIndex's PPTX parser, and the practitioner guides ([slide-as-chunk PPTX recipe](https://medium.com/codex/chunking-for-rag-powerpoint-b2070b145715)). Metadata: deck path, slide number, slide title, picture/table/chart counts, OCR flag.

**Failure mode of flat splitting:** "a slide titled 'Q3 Architecture Decision' with a diagram carries most of its meaning in the diagram, not in the six bullets underneath" — a flat splitter that ignores slide boundaries returns chunks with no way to recover *which slide* or *which deck*. Visual context is unrecoverable.

### XLSX — `SheetRowChunker`

For tabular sheets, one row per chunk, with the header row prepended into each chunk's text and the sheet name in metadata. For small reference sheets (<50 rows), embed the whole sheet as one chunk. LlamaIndex's Excel recipe via LlamaParse uses row-level nodes with `file_name`, `sheet_name`, `row_index`/`cell_ref` metadata ([LlamaIndex Excel RAG](https://adasci.org/blog/implementing-rag-over-excel-sheets-through-llamaindex), [Preprocess.co Excel guide](https://preprocess.co/xls)). Non-standard sheets (merged cells, multi-table layouts) need a hybrid two-pass approach: identify structural regions first, chunk per region ([non-standard Excel chunking](https://ragaboutit.com/mastering-document-chunking-for-non-standard-excel-files-a-software-engineers-guide/)).

**Failure modes of prose-style chunking on XLSX:** row `[42, "Acme", "2025-03", 17000]` embedded without column headers matches nothing useful; chunks spanning rows merge unrelated records; sheet-level context ("FY26 Forecast" vs "FY25 Actuals") lost.

Note: `.xlsm` needs `openpyxl` (already installed); `.xlsb` needs `pyxlsb` (not installed — separate extractor wave). The #337 `markitdown[xls]` fix added `xlrd` for legacy BIFF `.xls` files only.

### Slack / chat — `ThreadChunker`

Thread = primary chunk. If thread exceeds 500 tokens, sub-split by token cap. For non-threaded streams, group by 5-minute window. Attach `channel`, `thread_ts`, `user_ids`, `time_range` to metadata. A Slack-RAG case study reports **5–6% retrieval-accuracy lift** vs naive character-count chunking, growing with corpus size ([Slack RAG smarter chunking](https://dev.to/criscmd/how-i-boosted-slack-rag-accuracy-by-5-6-with-smarter-chunking-1kf9), [Luna contextual RAG](https://withluna.ai/blog/contextual-rag-product-meeting-notes-slack)).

**Failure modes of flat splitting:** one-line messages embed as near-noise; replies separated from parent question; emoji-only / "+1" messages dilute the embedding pool.

### GitHub — `CodeChunker` + `MarkdownStructuralChunker`

Code: `RecursiveCharacterTextSplitter.from_language(language=Language.PYTHON, chunk_size=1000, chunk_overlap=100)` — splits on `\nclass ` / `\ndef ` before falling back to generic separators ([LangChain code splitter API](https://python.langchain.com/api_reference/text_splitters/markdown/langchain_text_splitters.markdown.MarkdownHeaderTextSplitter.html), [Databricks chunking guide](https://community.databricks.com/t5/technical-blog/the-ultimate-guide-to-chunking-strategies-for-rag-applications/ba-p/113089)). Overlap deliberately low (50–100 tokens) to avoid duplicating function signatures across chunks ([Buildmvpfast 2026 guide](https://www.buildmvpfast.com/blog/chunking-strategies-rag-semantic-fixed-size-recursive-2026)). READMEs + commit messages use the markdown chunker.

**Failure modes of flat splitting:** splitting mid-function breaks symbol-based search; docstrings severed from their function; commit-message bodies truncated mid-bullet.

### Email — `EmailThreadChunker`

Thread = document. Each message in the thread = one chunk (cap at 1024 tokens; sub-split only if exceeded). Strip quoted-reply chains. Attach `from`, `to`, `subject`, `sent_at`, `thread_id`, `attachment_refs` as metadata. Thread-aware is the consensus pattern ([RAG-Mail GitHub](https://github.com/ManiAm/RAG-Mail), [RAG on email data guide](https://medium.com/@jojokirby/rag-on-email-data-a-general-guide-based-on-my-professional-experience-bb7f55b11412)). For M365 specifically, preserve Outlook metadata as governance + retrieval primitives ([Colligo M365 email metadata](https://www.colligo.com/email-metadata-key-to-your-document-management-strategy/)).

**Failure modes of flat splitting:** same quoted reply appears N times across N reply messages → near-duplicate pollution; subject-line context lost mid-thread; signature blocks embedded as content.

### Calendar event — `CalendarEventChunker`

One event = one chunk. Do not split. Title + description + attendees + location + recurrence rule + linked-doc URIs in a single text block; metadata holds structured fields (RRULE, start, duration, attendee IDs) for filter-side retrieval. Recurrence as metadata, not text — recurring "30 min sync" events otherwise inflate the index with near-duplicates that all match the same query.

**Failure modes of flat splitting:** title separated from attendees; recurring events deduplicate poorly; embedding "30 min sync" returns useless matches across hundreds of standups.

## Cross-cutting techniques

### Contextual retrieval (Anthropic, Sep 2024)

Anthropic's [contextual retrieval](https://www.anthropic.com/news/contextual-retrieval) prepends a 50–100 token LLM-generated summary of the parent document's relevance to each chunk before embedding. Reported lift:

| Configuration | Top-20 failure rate | Reduction |
|---|---|---|
| Baseline (chunk-only embeddings + BM25) | 5.7% | — |
| + contextual embeddings | 3.7% | 35% |
| + contextual BM25 | 2.9% | 49% |
| + reranking | 1.9% | 67% |

**Cost:** ~$1.02 per million document tokens with prompt caching enabled (caching cuts ~90%); each chunk grows by 50–100 tokens, reducing how many fit in the LLM's answer context.

**Adopt for kairix:** yes, as an optional per-type layer applied after structural chunking. Justification: the corpus is multi-source, chunks are often short (Slack, calendar, slide bullets), corpus grows monotonically. The cost is amortised by prompt caching; the lift is largest on exactly the surfaces kairix is weakest on today (slides, rows, calendar events, short messages).

**Don't use for:** (a) total corpus < 200K tokens — just stuff into context, no RAG at all; (b) per-chunk content is already self-describing (full DOCX sections with headings inline); (c) update cadence so high that re-contextualising every churn costs more than the recall gain ([Cloudurable RAG analysis](https://cloudurable.com/blog/is-rag-dead-anthropic-says-no/)).

### Recursive vs structural vs semantic chunking

* **Recursive** (LangChain `RecursiveCharacterTextSplitter`) — split on biggest separator, fall back to finer. Right default for heterogeneous corpora; no embedding cost. Vecta's Feb 2026 benchmark on 50 academic papers had recursive at 512 tokens beat semantic chunking **69% vs 54%**; Chroma's tests showed recursive at 400–512 tokens delivering 85–90% recall ([Firecrawl chunking 2026](https://www.firecrawl.dev/blog/best-chunking-strategies-rag)).
* **Structural** (heading-aware, table-aware, slide-aware) — best where the document format encodes the boundaries. Always preferable to recursive *when the structure is reliable* (DOCX, PPTX, markdown, HTML, calendar). This ADR's per-type plugins are structural-first.
* **Semantic** (split where embedding similarity drops) — wins on uniform long-form prose but produces wildly uneven chunk sizes that hurt retrieval calibration; benchmark vs recursive before adopting per type, don't assume.

### Late chunking (Jina, EMNLP 2024)

[Late chunking](https://arxiv.org/pdf/2409.04701) inverts the order: embed the whole document with a long-context model (Jina v2/v3, 8192 tokens) first, then mean-pool per chunk from the token-level representations. Each chunk's embedding contains cross-chunk context for free; dramatically cheaper than contextual retrieval (no per-chunk LLM call). **Caveat for kairix:** Azure OpenAI `text-embedding-3-small` doesn't expose the token-pooling hook needed for canonical late chunking. To adopt, we'd need to swap to Jina v2/v3 or run sentence-transformers locally. **Flag as a future option, not a default.** Listed here so the next provider review picks it up.

## Quality evaluation

The metrics practitioners actually use ([Production RAG strategies](https://towardsai.net/p/machine-learning/production-rag-the-chunking-retrieval-and-evaluation-strategies-that-actually-work), [Braintrust RAG eval](https://www.braintrust.dev/articles/rag-evaluation-metrics), [retrieval quality precision@k / recall@k / F1@k](https://towardsdatascience.com/how-to-evaluate-retrieval-quality-in-rag-pipelines-precisionk-recallk-and-f1k/)):

1. **Recall@k and Precision@k per document type slice.** Measure separately for each source (markdown, PPTX, Slack, …). A global average hides catastrophic failure on one type. **Highest-value addition for kairix.**
2. **MRR + NDCG@k.** Reward early-position hits — maps best to "did the user see the answer". Already reported globally; needs per-type slicing.
3. **Boundary-spanning canary suite.** Deliberately construct queries whose answer crosses two chunks; measure whether *both* surface. **Breaks loudly when a chunker regression splits an atomic unit** (a slide, a calendar event, a row).
4. **Chunk-size distribution** — mean, p50, p95, p99 per source type. A long tail of 50-token chunks signals fragmentation; near-uniform 512 means the splitter is over-aggressive on natural boundaries. Emit as a maintenance-tick telemetry sample.
5. **BM25 vs dense-retrieval divergence as a smell.** If the two retrievers disagree heavily on a type, chunks are probably too short for dense to discriminate, or too long for BM25 term-density. RRF-fused systems should show convergence on well-chunked types.
6. **Context Precision / Context Recall** (Ragas-style). Fraction of retrieved chunks actually used in the generated answer; fraction of needed evidence retrieved. Ties chunking quality to end-user outcome rather than retrieval intermediate.
7. **Adaptive-chunking baseline comparison.** [Cited adaptive-chunking paper](https://unstructured.io/insights/rag-evaluation-a-data-pipeline-performance-framework) showed precision 0.17→0.50 and recall 0.40→0.88 vs baseline per-type tuning is measurably worth the effort.

**Corpus extension.** The reflib benchmark today is markdown-only. Add per-type fixtures so the eval harness actually exercises what operators run:

* 50 sample PPTX decks (mix of bullet-heavy, image-heavy, dense-table)
* 50 sample PDFs (born-digital + scanned mix)
* 30 sample DOCX (short memos + long policy docs)
* 30 sample XLSX (tabular + small-reference mix)
* 200 sample Slack threads (short + long + threaded + non-threaded)
* 100 sample emails (subject-heavy + body-heavy + thread + with-attachments)
* 50 sample calendar events (recurring + one-off)

Source: synthetic + the reflib reference library (no real client / personal content; F32 honoured).

## Mechanics

### Registry dispatch

The existing `chunker_registry.py` already supports `(kind, mime, section_kind) → Chunker` dispatch. This ADR populates the registry:

```python
register(("obsidian", "text/markdown", None), MarkdownStructuralChunker())
register(("notion", "text/markdown", None), MarkdownStructuralChunker())
register(("sharepoint", "application/pdf", "born_digital"), PdfLayoutChunker(branch="layout"))
register(("sharepoint", "application/pdf", "scanned"), PdfLayoutChunker(branch="ocr"))
register(("sharepoint", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", None), DocxHeadingChunker())
register(("sharepoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation", None), SlideChunker())
register(("sharepoint", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", None), SheetRowChunker())
register(("sharepoint", "application/vnd.ms-excel", None), SheetRowChunker())  # legacy .xls via xlrd
register(("slack", "text/plain", None), ThreadChunker())
register(("github", "text/markdown", None), MarkdownStructuralChunker())
register(("github", "text/x-python", None), CodeChunker(language="python"))
register(("m365_email_headers", "message/rfc822", None), EmailThreadChunker())
register(("m365_calendar", "text/calendar", None), CalendarEventChunker())
# fallback stays as ParagraphFallbackChunker for unregistered (kind, mime) pairs
```

`section_kind` discrimination (e.g. PDF born-digital vs OCR) is set by the extractor when it builds `extracted.pages` based on character density / OCR confidence; the chunker sees it as a routing hint.

### `chunker_version` threading + re-chunk-sweep tick

F55 already requires every `Chunk(...)` to carry `chunker_version=self.version`. ADR-028 adds:

* A **re-chunk-sweep tick** in the worker (cadence: maintenance-tick alongside FTS-rebuild / orphan-prune). On each tick:
  1. Query `documents_media WHERE chunker_version != current_version(kind, mime)`.
  2. Bound per tick by F66 declarations (`per_tick_max_items`, `disk_watermark_min_free_bytes`).
  3. Re-extract → re-chunk → re-embed → atomic swap of `content_vectors` rows.
  4. Emit per-tick observability (rows-rechunked, chunker-version-from/to, elapsed).
* A `kairix chunkers status` CLI subcommand (mirrors `kairix features status`) showing current chunker version per `(kind, mime)`, count of documents on stale version, projected re-chunk-sweep time-to-drain.

### Contextual prepending — optional layer

A separate `ContextualPrependLayer` decorator wraps any structural chunker. Reads a per-deployment config flag (`contextual_retrieval.enabled`) and per-source override list. When enabled:
* For each chunk, generate a 50–100 token context summary via Anthropic Claude Haiku (cheapest model with prompt caching).
* Prepend to the chunk text before embedding; store the raw chunk separately for display.
* Cache by `(document_hash, chunker_version)` so re-chunk-sweep doesn't pay the LLM cost twice per document.

Flag-gated via `FlagGatedCapability` (ADR-026 Track C — depends on #346).

## Fitness functions this work will trip

Per the existing fitness-function catalogue, the implementation will tip several F-rules; each becomes an acceptance criterion:

| Rule | Why it fires | Acceptance |
|---|---|---|
| **F55** (chunker_version threading) | Every new chunker plugin must declare `version: str` + pass `chunker_version=self.version` | Already enforced; new plugins must satisfy |
| **F66** (per-tick budget + watermark) | Re-chunk-sweep tick is a tick-driven component | Declare `per_tick_max_items` + `disk_watermark_min_free_bytes` on the deps dataclass + class |
| **F70** (schema-writer symmetry) | New `chunker_telemetry` table (chunk-size distribution samples) | INSERT site at sweep-tick emit time |
| **F43** (plugin contract tests) | Each new `Chunker` plugin needs a contract test | `tests/contracts/test_<name>_chunker_protocol.py` |
| **F45** (new-capability BDD) | `kairix chunkers status` is a new CLI subcommand | `tests/bdd/features/cli_chunkers_status.feature` |
| **F30** (operator-outcome tests) | Same CLI + per-type re-chunk-sweep outcome | Outcome tests asserting stdout shape + envelope |
| **F53** (status surface) | Operator-facing chunker status | `kairix chunkers status` mirrors `kairix features status` |
| **F54** / future F78 (flag both-branch) | Contextual prepending is flag-gated | OFF + ON BDD + integration tests |
| **F68** (Protocol failure modes) | New `Chunker` subclasses + contextual LLM HTTP client | Failure-mode contracts (HTTP timeout, partial OCR, malformed PPTX) |
| **F64** (HTTP rate-limit) | Contextual prepending calls Anthropic API | Rate-limit + Retry-After tests |
| **F69** (scale-bound integration) | Re-chunk-sweep against ≥10K-row corpus | Scale-bound integration test |
| **F72** (cross-layer integrity) | Chunker version drift invariant | Soak-tier invariant: no row in `documents_media` carries a `chunker_version` not present in any installed plugin |

## Definition of done

| # | Criterion | Verification |
|---|---|---|
| 1 | `kairix/chunkers/` directory exists with the 6 plugins above | Imports + F43 contract tests |
| 2 | Chunker registry populated for every `(kind, mime)` pair across active connectors | Integration test confirms no registered connector falls through to `ParagraphFallbackChunker` |
| 3 | `chunker_version` on `documents_media` reflects the actual plugin version used | F55 + spot-check SQL query |
| 4 | Re-chunk-sweep tick wired into worker with F66 budget declarations | F66 green; sweep tick observed in worker log on freshly-bumped version |
| 5 | `kairix chunkers status` CLI + F45/F30/F53 tests | All four green |
| 6 | Reflib corpus extended with the per-type fixtures listed above | Eval suite runs end-to-end against extended corpus |
| 7 | Per-type Recall@10 + NDCG@10 slices in the benchmark output | Eval report includes per-source-type breakdown table |
| 8 | Boundary-spanning canary suite with at least 3 queries per atomic unit type (slide, row, event, message) | Each canary fails when chunker is sabotaged to split the unit |
| 9 | Chunk-size distribution telemetry (`chunker_telemetry` table + sweep-tick emit) | F70 green; SQL query returns per-source p50/p95/p99 |
| 10 | Contextual prepending available as opt-in `FlagGatedCapability` (depends on #346 Track C) | F54/F78 green; OFF + ON paths both tested |
| 11 | `docs/architecture/chunking-strategy-per-source.md` operator-facing doc | Doc exists; links from `docs/architecture/ENGINEERING.md` and `docs/evaluation/EVALUATION.md` |

## Open decisions

1. **Contextual prepending model choice.** Anthropic recommends Claude Haiku for cost; alternatives are GPT-4o-mini or a local Llama-3.1-8B. Recommend Haiku as the default for v1 because prompt caching is mature; revisit when local Llama on tc-agents has sufficient GPU.
2. **Re-chunk-sweep wall-clock budget.** Full corpus re-chunk at the new defaults is roughly the same scale as `kairix embed --force` (and that just OOM'd at 1.27M vectors on this VM — see GH #352). Need to confirm whether the sweep tick streams or batches. Recommend streaming (read N docs, re-chunk + re-embed + atomic swap, free, repeat) so memory never spikes.
3. **`.xlsm` / `.xlsb` extractor depth.** `.xlsm` works via openpyxl today (macro stripped); `.xlsb` needs `pyxlsb`. Decide whether to ship `[xlsb]` extra alongside this wave or defer to a separate connector enhancement.
4. **PPTX OCR'd image text.** PowerPoint slides routinely carry meaning in embedded images (architecture diagrams, screenshots). Should `SlideChunker` invoke OCR per image at extract time? Recommend yes for slides that have <100 tokens of text + ≥1 image (skip otherwise — OCR is expensive).
5. **Calendar recurrence semantics.** Store each occurrence as a separate row (filterable but duplicates the same body N times), or store the master event + RRULE (cleaner but needs query-side expansion). Recommend master + RRULE; filtering happens in the retrieval-side metadata filter.
6. **Per-deployment override surface.** Should `kairix.config.yaml` allow per-connector chunker overrides (e.g. an operator who wants 2048-token Slack threads instead of 500)? Recommend yes — emit a per-deployment chunker dispatch override map under `connectors[].chunker_overrides`.

## Sequencing

This is multi-wave platform work. Proposed phasing:

| Wave | Scope | Estimated duration |
|---|---|---|
| **F.0** | Chunker registry hardening: ship contract test scaffolding; populate registry with current `ParagraphFallbackChunker` as default for every connector kind explicitly (no behaviour change). Land the per-type fixture additions to the reflib corpus. | 1 week |
| **F.1** | Ship `MarkdownStructuralChunker` + `SlideChunker` + `SheetRowChunker` (highest-impact three: covers Obsidian + Notion + every SharePoint deck + every spreadsheet). Includes BDD + integration + golden-file tests per plugin. | 2 weeks |
| **F.2** | Ship `DocxHeadingChunker` + `PdfLayoutChunker` (born-digital + OCR branches) + `EmailThreadChunker` + `CalendarEventChunker` + `ThreadChunker` + `CodeChunker`. | 2 weeks |
| **F.3** | Eval harness: per-source-type Recall@10 + NDCG@10 slices, boundary-spanning canary suite, chunk-size telemetry, sweep-tick observability. | 1 week |
| **F.4** | Re-chunk-sweep tick + `kairix chunkers status` CLI + MCP `tool_chunkers_status`. | 1 week |
| **F.5** | Contextual prepending as optional `FlagGatedCapability` layer (depends on ADR-026 Track C → #346 merged). | 2 weeks |

Total: 9 weeks of focused work. F.0 + F.1 + F.3 form a viable v1 release (4 weeks); F.2 + F.4 + F.5 are follow-ups.

## Related work

* ADR-024 — test pyramid (the per-type fixture additions extend the corpus this ADR depends on)
* ADR-026 — cross-cutting primitives (Track C `FlagGatedCapability` is the gating mechanism for contextual prepending)
* ADR-027 — entity enrichment (improved chunking quality lifts entity-extraction recall; better PPTX slide chunks → better entity discovery from decks)
* GH #347 — ADR-027 implementation (this ADR cited as a prerequisite for high-quality entity recall on slide/spreadsheet content)
* GH #348 — #337 SharePoint follow-ups (MIME sniffing converges with the PDF born-digital vs OCR branch detection in this ADR)
* GH #349 — #329 chunk_date backfill verification (related but independent — this ADR's re-chunk-sweep tick is the right vehicle for that backfill)
* GH #352 — embed --force OOM under operator's memswap=24g configuration (will inform the re-chunk-sweep streaming-vs-batching open decision)
