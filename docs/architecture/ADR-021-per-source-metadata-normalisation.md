# ADR-021 — Per-source metadata normalisation

**Status:** Accepted 2026-05-28
**Issues:** #329 (logged 2026-05-28); chunk_date_populated onboard check at 2% post-SharePoint ingestion
**Related:** ADR-018 (DLT connector framework — the layer this extends); ADR-020 (per-tick budget — sibling Wave E.5 work); F39 (chunk metadata invariants — F66 extends F39 with richer metadata)

## Context

Every source kairix ingests carries structured metadata in its native envelope: dates, authors, tags, categories. Only Markdown (Obsidian) flows that metadata through to the `Chunk` model today, via frontmatter parsing. Every other source drops it before the silver stage:

| Source | Available metadata | Reaching `Chunk` today | Gap |
|---|---|---|---|
| Markdown (Obsidian) | `date:` / `author:` / `tags:` frontmatter | Yes — `chunk_date`, entity_signals, tags | None |
| PDF | Creation date, modification date, author, title, subject, keywords (XMP/Info) | extracted text only | dates + author + categorisation lost |
| DOCX/XLSX/PPTX | Core properties: created, modified, author, last_modified_by, keywords, category, title, subject | extracted text only | dates + author + categorisation lost |
| SharePoint envelope | `lastModifiedDateTime`, `createdBy.displayName`, `webUrl`, `file.mimeType` | `source_modified_at` + `source_uri` via F39 | `createdBy` (author entity!) lost; `lastModifiedDateTime` not in `chunk_date` |
| Slack messages | `ts`, `user`, `thread_ts`, channel kind, reactions, files | extracted text only | timeline + author + thread context lost |
| GitHub items | commit date, author email, branch, PR/issue labels, milestone | extracted text only | timeline + author + categorisation lost |
| Notion pages | `last_edited_time`, `created_time`, properties (status, owner, tags) | extracted text only | timeline + entity + tags lost |
| M365 calendar | event date, organiser, attendees, location | varies | attendee entities lost (key for graph!) |
| M365 email headers | sender, recipients, subject keywords, sent date | text only | sender as author entity, sent date as `chunk_date` |

This was surfaced on 2026-05-27 by the `chunk_date_populated` onboard check failing at 2% (48,881 / 2,224,949 chunks) after the v2026.5.24 SharePoint ingestion. The check reports real signal: SharePoint chunks dominate the post-ingestion corpus, none of them have `chunk_date`, and temporal-boost search degrades to BM25 for that 95%+ of content.

The cost isn't only temporal boost. Three downstream surfaces suffer:

1. **Timeline boost** — only Obsidian chunks get accurate `chunk_date`. "Most recent" / "last week" / "this quarter" search intents miss SharePoint, GitHub, Slack content.
2. **Entity graph** — authors, attendees, Slack users, GitHub committers, SharePoint document owners are first-class people who should land in `entity_signals` as Person nodes. Currently only entities the text-extractor's NER catches do; structured-metadata authors don't.
3. **Vector context** — chunks lack the metadata header that would improve embedding context for filterable queries ("Slack messages from <person> last week about <topic>"). Embeddings see only body text.

## Decision

Introduce a **`SourceMetadata` value object** plus a **per-connector `metadata_for(item_id)` Protocol method** and a **per-extractor `metadata_for(raw, mime)` Protocol method**. The silver stage merges the three metadata sources (connector envelope > extractor-extracted > defaults) into the `Chunk` record, which gains first-class `author`, `tags`, and `metadata: Mapping[str, str]` fields. `EntityGraphSink` emits `Person` entity signals automatically from `SourceMetadata.author` and `SourceMetadata.author_email`.

### Protocol shape

```python
@dataclass(frozen=True)
class SourceMetadata:
    """Structured metadata extracted from a source item's native envelope.

    All fields default to None / empty so connectors implement only what
    their source surfaces. Silver merges this with extractor-extracted
    metadata (PDF XMP, Office core properties) using last-write-wins
    in the priority connector > extractor > defaults.
    """
    modified_at: str | None = None        # ISO 8601 UTC; landing target: Chunk.chunk_date when no extractor override
    created_at: str | None = None
    author: str | None = None             # display name; landing target: Chunk.author + EntitySignal(kind=person)
    author_email: str | None = None       # secondary identifier; EntitySignal hint
    tags: tuple[str, ...] = ()            # landing target: Chunk.tags
    properties: Mapping[str, str] = field(default_factory=dict)  # per-source extensions (Slack thread_ts, GitHub PR label, Notion property values)


class SourceConnector(Protocol):
    name: str
    per_tick_max_items: int  # ADR-020
    disk_watermark_min_free_bytes: int | None  # ADR-020
    ...

    def metadata_for(self, item_id: str) -> SourceMetadata:
        """Return SourceMetadata populated from the source's envelope.

        Called per-item by silver, BEFORE extractor runs. Connectors
        that surface no structured metadata can return SourceMetadata().
        Connectors with envelope metadata MUST surface it — F65 blocks
        connectors that return SourceMetadata() when their source
        provides identifiable metadata (verified by per-connector
        integration test in tests/integration/test_<name>_metadata_propagation.py).
        """
        ...


class Extractor(Protocol):
    name: str
    version: str
    ...

    def metadata_for(self, raw: bytes, mime: str) -> SourceMetadata:
        """Return SourceMetadata extracted from the raw bytes.

        For PDFs: read XMP/Info dictionary (CreationDate, Author, Title, Keywords).
        For Office: read core properties (created, modified, creator, keywords, category).
        For markdown: parse `---` frontmatter block.
        For passthrough text: SourceMetadata() (no extractor-side metadata).
        """
        ...
```

### Chunk model extension

```python
@dataclass(frozen=True)
class Chunk:
    # existing F39 fields:
    source_name: str
    source_uri: str
    source_modified_at: str
    sensitivity: Sensitivity
    text: str
    hash: str
    chunk_date: str | None  # already exists; now populated from metadata

    # NEW in ADR-021:
    author: str | None = None
    author_email: str | None = None
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)  # extras slot
```

Back-compat: new fields default to None / empty, existing callers that construct `Chunk(...)` without them continue to compile.

### Silver merge logic

```python
class DefaultSilverProcessor:
    def process(
        self,
        ref: BronzeRef,
        doc: ExtractedDocument,
        *,
        source_uri: str,
        source_modified_at: str,
        sensitivity: Sensitivity,
        connector_metadata: SourceMetadata,   # NEW — populated by ConnectorPipeline before silver
        extractor_metadata: SourceMetadata,   # NEW — extractor.metadata_for() result
    ) -> SilverOutput:
        # last-write-wins merge: connector > extractor > defaults
        merged_chunk_date = (
            connector_metadata.modified_at
            or extractor_metadata.modified_at
            or source_modified_at
        )
        merged_author = connector_metadata.author or extractor_metadata.author
        merged_tags = tuple({*connector_metadata.tags, *extractor_metadata.tags})
        merged_props = {**extractor_metadata.properties, **connector_metadata.properties}

        chunks = tuple(
            Chunk(
                ...,
                chunk_date=merged_chunk_date,
                author=merged_author,
                author_email=connector_metadata.author_email or extractor_metadata.author_email,
                tags=merged_tags,
                metadata=merged_props,
            )
            for ...
        )
        # EntitySignal auto-population
        entity_signals = list(_extract_text_signals(doc))
        if merged_author:
            entity_signals.append(EntitySignal(
                kind="person",
                value=merged_author,
                source_uri=source_uri,
                modified_at=merged_chunk_date or source_modified_at,
                confidence=1.0,
                sensitivity=sensitivity,
            ))
        ...
```

## Alternatives considered

**A. Pass metadata via `ChangeEvent` field rather than per-item `metadata_for()`.**
Rejected. `ChangeEvent` is emitted from `list_changes` before the orchestrator decides what to do. Some metadata (e.g. SharePoint `createdBy`) requires an additional Graph round-trip the connector wouldn't want to do for every list_changes call. `metadata_for(item_id)` lets the connector fetch lazily, only when the orchestrator decides to process the item.

**B. Extract metadata only from extractors (no connector-side `metadata_for`).**
Rejected. Connectors have authoritative envelope metadata that extractors can't recover (SharePoint's `createdBy` doesn't appear inside the PDF; Slack's `user` doesn't appear inside the message body). Connector-side `metadata_for` is the only way to surface envelope-level facts.

**C. Add `Chunk.metadata: dict[str, Any]` and skip the typed fields.**
Rejected. Untyped extras bag defeats the type system + F42 frozen-dataclass discipline. `chunk_date` + `author` + `tags` are first-class fields because search ranking, the entity graph, and the embedding context-header all read them by name. `metadata: Mapping[str, str]` is the typed extras slot for per-source per-source-kind extensions (Slack `thread_ts`, GitHub `pr_number`).

**D. Adaptive inference (predict tags from text via LLM).**
Out of scope. Start with declarative-only: surface what the source provides. Inference is a layer that can land later if needed; we don't know yet what classes of inference are worth the cost.

**E. Person disambiguation in this ADR (the same human as a SharePoint author + GitHub committer + Slack user).**
Out of scope. This ADR populates `EntitySignal(kind=person, value=<display name>)` per source; downstream entity-resolution pass (separate ADR + future work) disambiguates across sources.

## Acceptance criteria

- [ ] `SourceMetadata` frozen dataclass in `kairix/core/protocols.py` per F42
- [ ] `SourceConnector.metadata_for(item_id) -> SourceMetadata` added to Protocol; default impl returns `SourceMetadata()` for back-compat
- [ ] `Extractor.metadata_for(raw, mime) -> SourceMetadata` added to Protocol; default impl returns `SourceMetadata()` for back-compat
- [ ] `Chunk` dataclass gains `author: str | None`, `author_email: str | None`, `tags: tuple[str, ...]`, `metadata: Mapping[str, str]`. All default to None / empty for back-compat.
- [ ] `DefaultSilverProcessor.process()` accepts `connector_metadata` + `extractor_metadata` kwargs; merges per the priority above.
- [ ] `ConnectorPipeline._process_item` calls `connector.metadata_for(item_id)` + `extractor.metadata_for(raw, mime)` and threads both into `silver.process(...)`.
- [ ] Per-connector implementations (Wave E.5 scope): obsidian, dex_crm, m365_email_headers, m365_calendar, sharepoint, slack, github, notion. Each surfaces all metadata its envelope provides.
- [ ] Per-extractor implementations: markitdown (Office core properties), pdf_fallback (PDF XMP/Info dict), passthrough (markdown frontmatter), ocr (no metadata — empty SourceMetadata).
- [ ] `EntityGraphSink` auto-emits `EntitySignal(kind=person)` when `SourceMetadata.author` is present.
- [ ] F65 fitness function (added in Wave E.5) — every connector must implement `metadata_for` AND have `tests/integration/test_<name>_metadata_propagation.py` asserting `Chunk.chunk_date` + `Chunk.author` propagate.
- [ ] BDD `tests/bdd/features/connector_metadata_propagation.feature` — generic scenario walks one connector through the pipeline + asserts metadata lands.
- [ ] `chunk_date_populated` onboard check passes (>80%) after the per-connector implementations land + a re-extract sweep of the existing corpus completes.

## Operational implications

**Re-extract required**: existing chunks need re-extraction to populate the new fields. `kairix worker reextract --source-name <name>` (Bug D from v2026.5.27a1) walks every dead-lettered AND live item; a `--rewrite-metadata` flag is added to force re-extract on items that are healthy but lack metadata.

**Storage impact**: `Chunk.author` (~30 chars avg) + `tags` (~3 tags × 20 chars) + `metadata` (typically empty, ~50 chars when set) = ~150 chars per chunk additional. Across 2M chunks: ~300 MB additional SQLite storage. Below the 6000× headroom from streaming bronze.

**Backwards compatibility**: chunks indexed before this ADR carry the new fields as None / empty. Search ranking gracefully degrades to text-only matching for those chunks; new chunks get the full structured surface. Re-extract upgrades the older chunks at operator pace.

## Pairing with ADR-020

ADR-020 is per-tick **work bound**. ADR-021 is per-item **information bound** — how much we know about each item. Independent dimensions; both ship in Wave E.5.

## Migration

Wave E.5 ships the protocol + connector + extractor + Chunk + silver changes. F65 baseline starts at all current connectors; pay down per F49 as each connector's `metadata_for` lands.

Old chunks lacking the new fields stay searchable but lack temporal-boost / author-entity coverage until they're re-extracted. Operators decide when to fire `kairix worker reextract --rewrite-metadata` against their corpus.
