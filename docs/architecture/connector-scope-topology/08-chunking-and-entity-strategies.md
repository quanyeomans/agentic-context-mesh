# Chunking + entity-modelling strategies per source kind

Research note for `kairix/core/connectors/silver.py` evolution beyond the
current uniform-paragraph chunker. Frames the problem, surveys per-kind
strategies, then proposes a dispatch shape that keeps F38 (singular
Silver surface) intact while admitting kind-aware behaviour.

## Background — why uniform chunking fails

Hybrid retrieval (BM25 + dense vector + Reciprocal Rank Fusion) operates
at chunk granularity: the chunk is the smallest unit that gets indexed,
scored, and returned. Chunk shape therefore decides what the ranker can
*see* — a Jira ticket sliced into 1000-character paragraph windows loses
its atomicity (status, assignee, blocked-by edge all dissolve into prose),
while a 30-line Python function sliced the same way splits an `if`
condition from its branches and an import from its first use. Uniform
chunking optimises for a hypothetical "average document" that no source
in a real knowledge stack actually produces.

The empirical evidence is consistent. The LangChain team's "Evaluating
RAG Pipelines" study (2023) showed up to 30% recall@5 swing on identical
corpora just by switching from fixed-window to structure-aware chunkers.
LlamaIndex's `SemanticSplitterNodeParser`
([GitHub](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/node_parser/text/semantic_splitter.py))
ships specifically because their benchmark suite (BeIR, MTEB-Retrieval)
penalised naive splits. Anthropic's "Contextual Retrieval" post
([anthropic.com, 2024](https://www.anthropic.com/news/contextual-retrieval))
reports 49% failed-retrieval reduction by prepending chunk-relative
context, which is itself an admission that chunks alone under-represent
their parent document. The corollary: chunkers must be source-aware, and
the entity-extraction layer that feeds the graph must respect the same
boundaries.

The sections below cover the twelve source kinds in scope for the
Onyx-comparative connector catalogue (see
[`06-onyx-comparative-analysis.md`](06-onyx-comparative-analysis.md)).

---

## 1. Markdown / wiki-doc

Covers Notion, Confluence, Obsidian, Bookstack, Outline, GitBook, Slab,
MediaWiki, Coda, Document360, Discourse, Drupal, Canvas, Guru, Highspot,
Axero, Xenforo, Wikipedia, Google Sites.

### A. Chunking
- **Chunk unit**: a heading-bounded section. H2 / H3 boundaries are the
  natural semantic units of a wiki page.
- **Granularity**: target 256–512 tokens per chunk at retrieval time;
  pack to 1024 tokens for synthesis-stage re-fetch. Sections shorter
  than 100 tokens fold up into their parent H1; sections longer than 1024
  tokens recursively split on H4 / paragraph.
- **Overlap**: zero. Heading boundaries already give the retriever a
  semantic anchor; sliding overlap inflates the index and double-counts
  hits in RRF.
- **Hierarchical preservation**: every chunk carries a breadcrumb
  `page_title > H2 > H3` prepended to the chunk text *and* held as
  structured metadata. The breadcrumb improves BM25 hit on
  navigation-style queries ("the SSO section of the onboarding page").
- **Per-chunk metadata**: `page_id`, `page_title`, `space_key` (Confluence)
  or `database_id` (Notion), `heading_path` (list), `chunk_index_in_page`,
  `chunk_index_in_section`, `last_edited_by`, `last_edited_at`,
  `wikilink_targets` (resolved IDs of `[[wikilink]]` / `@mention` targets).

### B. Entity extraction
- **Entity types that matter**: Page (the document itself), Heading
  (section), Person (author / mentioned), Tag (frontmatter / page-property),
  Wikilink target (cross-page edge).
- **Native vs NER**: Page, Heading, Tag and Wikilink are *native* — the
  markdown structure gives them for free. Person and Org need NER (spaCy
  `en_core_web_trf` or GLiNER zero-shot for non-English).
- **Relationship types**: `page LINKS_TO page` (wikilink edge),
  `page AUTHORED_BY person`, `page TAGGED tag`, `page CHILD_OF page`
  (Notion sub-page / Confluence hierarchy), `heading PART_OF page`.
- **Provenance**: every entity carries
  `(page_id, chunk_index, char_span)` so a graph hit traces back to the
  exact paragraph.

### C. Libraries
- [`markdown-it-py`](https://github.com/executablebooks/markdown-it-py) — CommonMark + GFM AST, gives heading tree directly.
- [`mistune`](https://github.com/lepture/mistune) — faster alternative when AST customisation isn't needed.
- [`markitdown`](https://github.com/microsoft/markitdown) — Microsoft's Office-to-markdown converter; already in use in the Obsidian connector.
- [`obsidiantools`](https://github.com/mfarragher/obsidiantools) — wikilink graph extraction for Obsidian vaults.
- [`spaCy`](https://github.com/explosion/spaCy) with `en_core_web_trf` — Person / Org / GPE NER.
- [`GLiNER`](https://github.com/urchade/GLiNER) — zero-shot NER, useful when label set is operator-defined.

### D. Failure modes
- **Frontmatter pollution**: YAML frontmatter shows up as a code block in some parsers; must be stripped before chunking or it dominates the BM25 score on the first chunk.
- **Wikilinks to unresolved targets**: `[[Some Page That Doesn't Exist]]` produces a dangling edge; the graph layer must distinguish "edge to known node" from "edge to ghost".
- **Embedded code blocks**: a 200-line code fence inside a markdown page is *not* a markdown chunk — split it out as a code-typed sub-chunk so the code-aware retriever path picks it up.
- **Notion toggle blocks / callouts**: collapsible by design; the parser must descend into them or content vanishes.
- **MediaWiki templates**: `{{Infobox …}}` expands to structured data; treat as a table-shaped chunk, not free prose.

---

## 2. Office documents (docx / xlsx / pptx / pdf)

Applies via SharePoint, Google Drive, OneDrive, Dropbox, Egnyte, Box,
local-FS.

### A. Chunking
- **Chunk unit, per format**:
  - `docx`: heading-bounded section (same shape as markdown).
  - `xlsx`: per-row for wide-table workbooks (CRM exports, asset
    registers); per-named-range for analyst workbooks; per-sheet
    summary chunk in addition.
  - `pptx`: one chunk per slide. Slides are atomic — title +
    bullets + speaker notes belong together.
  - `pdf`: layout-aware paragraph extraction, then heading-bounded
    section the same way as `docx`. Two-column PDFs require column
    reflow before paragraph detection.
- **Granularity**: 256–512 tokens for `docx` / `pdf` sections;
  whole-slide for `pptx` regardless of token count (a 1500-token slide
  is still one slide); per-row for tabular `xlsx`.
- **Overlap**: zero for `pptx` / `xlsx`. For long `pdf` sections that
  exceed 1024 tokens, fall back to sliding window with 64-token overlap.
- **Hierarchical preservation**: `docx` → heading tree; `pptx` → slide
  number + section name (PowerPoint sections, not bullet sections);
  `xlsx` → sheet name + header row prepended to every row chunk
  ("Q3 Pipeline > Closed Won > 2026-04-15, Acme Corp, $40k"); `pdf` →
  outline / bookmark tree where present, falling back to font-size
  heuristics.
- **Per-chunk metadata**: format-specific. Slide number, slide layout
  name, speaker-notes-present flag (pptx). Sheet name, column headers,
  row index (xlsx). Page number, bbox (pdf). Author, last-modified-by,
  track-changes-present flag (docx).

### B. Entity extraction
- **Entity types**: Person (author, mentioned, slide author), Org,
  Product, Project (mentioned in slide titles / doc headings), Number
  with unit (revenue, headcount, dates — high-value in pptx / xlsx).
- **Native vs NER**: docx authors and pptx slide-master metadata are
  native; everything in the body text is NER. xlsx is structured —
  column headers *are* entity-type declarations ("Customer", "Region",
  "ARR") and should be treated as schema, not extracted from prose.
- **Relationship types**: `slide PART_OF deck`, `deck PRESENTED_BY person`,
  `row IN_TABLE sheet`, `entity APPEARS_ON slide` (good for "find the
  slide where Acme was mentioned").
- **Provenance**: page / slide / row coordinates are the
  reproducible deep link.

### C. Libraries
- [`markitdown`](https://github.com/microsoft/markitdown) — Microsoft's reference converter for `docx` / `xlsx` / `pptx` / `pdf` to markdown; battle-tested, single-dep.
- [`python-pptx`](https://github.com/scanny/python-pptx) — slide-level fidelity, speaker-notes access.
- [`python-docx`](https://github.com/python-openxml/python-docx) — heading-tree extraction.
- [`openpyxl`](https://github.com/python-openxml/openpyxl) — xlsx row iteration without LibreOffice dep.
- [`pdfplumber`](https://github.com/jsvine/pdfplumber) — layout-aware pdf paragraph + table extraction.
- [`unstructured`](https://github.com/Unstructured-IO/unstructured) — multi-format with element-typed output, but heavyweight (depends on Tesseract, Detectron2 optional).
- [`docling`](https://github.com/DS4SD/docling) — IBM Research; strong table-in-pdf handling.

### D. Failure modes
- **Track-changes in docx**: accepted vs rejected text both live in the XML; default extractors return the accepted view, but operators sometimes want the proposed view (especially in legal-doc connectors).
- **pptx speaker notes**: half of presentations carry the real content in the speaker notes; extracting only slide text loses it.
- **xlsx merged cells**: header rows that span multiple columns confuse row iteration; must unmerge / propagate header text down.
- **xlsx formula cells**: `openpyxl` returns the formula string by default; use `data_only=True` after a LibreOffice / Excel recalc pass.
- **PDF two-column / scanned**: reading order is wrong by default; OCR'd PDFs need `ocrmypdf` pre-pass.
- **Embedded objects** (an Excel sheet embedded in a PowerPoint): most extractors silently drop them; flag in metadata so operators know what they're missing.

---

## 3. Source code (GitHub / GitLab / Bitbucket / local repos)

### A. Chunking
- **Chunk unit**: AST-level — function, method, class. A whole-file
  chunk is a fallback for files where the AST parse fails.
- **Granularity**: per-function for files where average function size
  is < 1024 tokens; per-class for tightly-coupled small classes; whole-
  file (with sliding window) for config / data-as-code files (`yaml`,
  `toml`, `json`).
- **Overlap**: zero between functions. Within an over-1024-token function,
  split on top-level statement boundaries with 1 line of overlap (the
  function signature is repeated as a context header for every sub-chunk).
- **Hierarchical preservation**: chunk text prepended with
  `# file: path/to/file.py\n# class: Foo` so retrieval matches
  qualified-name queries.
- **Per-chunk metadata**: language, file path, function name, class
  name, line range, imports (function-level — what does this function
  depend on?), docstring (extracted separately for hybrid scoring),
  `git_blame_recent_author`, `tests_referencing` (count of test files
  that import the symbol — derived; useful for ranking).

### B. Entity extraction
- **Entity types**: Module, Class, Function, Symbol-Import, TestCase.
  These are native to the AST — there is no NER step for code.
- **Native vs NER**: 100% native via AST. NER on comments / docstrings
  is a *separate* sub-pass that produces Person / Issue-ref entities
  (commit messages and TODO comments often contain GitHub issue
  references that belong in the graph).
- **Relationship types**: `function CALLS function`,
  `class INHERITS_FROM class`, `module IMPORTS module`,
  `test EXERCISES function` (resolved via import + call analysis),
  `commit TOUCHES function`. The call graph is the prize.
- **Provenance**: `(repo, commit_sha, file_path, line_range)`.

### C. Libraries
- [`tree-sitter`](https://github.com/tree-sitter/tree-sitter) — incremental parser; language grammars cover Python, TS / JS, Go, Rust, Java, Ruby, C / C++, and 40+ others. The de-facto standard for code chunking.
- [`tree-sitter-languages`](https://github.com/grantjenks/py-tree-sitter-languages) — pre-built Python bindings.
- [`ast-grep`](https://github.com/ast-grep/ast-grep) — pattern-matching on tree-sitter ASTs; useful for the entity-extraction pass.
- [`Aider`](https://github.com/Aider-AI/aider) — its repo-map code is a reference implementation of tree-sitter chunking + symbol-graph extraction for retrieval.
- [`Sourcegraph SCIP`](https://github.com/sourcegraph/scip) — the most complete cross-language symbol-graph format; consume their indexers rather than rebuild call-graph extraction.
- [`code-splitter`](https://github.com/wangxj03/code-splitter) — LangChain-compatible AST splitter.

### D. Failure modes
- **Minified files** (`*.min.js`, `bundle.js`): single 50,000-character line that breaks every chunker. Detect via `lines_per_file < 5 && avg_line_length > 500` and skip.
- **Generated files**: `*_pb2.py`, `*.generated.ts`, OpenAPI clients. Skip via gitattributes (`linguist-generated=true`) and `.gitignore`-style allow / deny lists.
- **Vendor directories**: `node_modules/`, `vendor/`, `third_party/`. Skip by convention.
- **Lock files**: `package-lock.json`, `poetry.lock`. Skip — they break BM25 with thousands of dependency names.
- **Mixed-language files** (`.vue`, `.svelte`, `.astro`): need a delegating chunker that splits by language fence first, then dispatches each fence to its own tree-sitter grammar.
- **Tree-sitter grammar failures on partial files**: real-world code includes parse errors; the chunker must accept partial trees, not bail on first error.

---

## 4. Issue / ticket (Jira / Linear / Asana / ClickUp / Zendesk / Freshdesk / Productboard / TestRail)

### A. Chunking
- **Chunk unit**: one chunk per ticket-as-a-whole *plus* one chunk per
  comment. The ticket-as-a-whole chunk carries title + description +
  fields; each comment is its own chunk.
- **Granularity**: do not split a ticket body further. A ticket is
  designed to be read whole; splitting destroys the "what was the
  decision?" context. If a single comment exceeds 1024 tokens, fall
  back to paragraph chunking *within* that comment.
- **Overlap**: zero. Tickets and comments are atomic.
- **Hierarchical preservation**: every comment chunk carries the parent
  ticket's title + status + key as a header.
- **Per-chunk metadata**: `ticket_key`, `status`, `priority`,
  `assignee`, `reporter`, `labels`, `components`, `sprint`, `epic_key`,
  `created_at`, `updated_at`, `comment_id` (for comment chunks),
  `comment_author`. Status and assignee are high-value filter fields;
  keep them in structured metadata so a query like "open bugs assigned
  to me about auth" reduces to filter + retrieve.

### B. Entity extraction
- **Entity types**: Ticket (native), Person (assignee / reporter /
  commenter — all native), Sprint, Epic, Component, Label (all native),
  External-link (URLs to PRs, docs, other tickets).
- **Native vs NER**: 95% native. NER is a *secondary* pass on comment
  body for Person / Org mentions that aren't in the assignee field.
- **Relationship types**: `ticket BLOCKS ticket`, `ticket DUPLICATE_OF ticket`,
  `ticket PART_OF epic`, `ticket IN_SPRINT sprint`,
  `comment ON_TICKET ticket`, `ticket MENTIONS person`,
  `ticket LINKS_TO pull_request` (cross-source edge into the code graph).
- **Provenance**: ticket key + comment ID.

### C. Libraries
- [`jira-python`](https://github.com/pycontribs/jira) — Atlassian Jira REST.
- [`linear-python`](https://github.com/jpbullalayao/linear-python) (or roll your own GraphQL client — Linear's API is small).
- [`atlassian-python-api`](https://github.com/atlassian-api/atlassian-python-api) — broader Atlassian coverage incl. Confluence.
- Comment rendering: most ticket systems use a custom markdown dialect (Jira's wiki markup, Linear's CommonMark+). Convert to canonical CommonMark via [`markdown-it-py`](https://github.com/executablebooks/markdown-it-py) plugins or vendor-supplied renderers.

### D. Failure modes
- **Custom fields**: every Jira instance has 20+ custom fields; the operator must declare which ones carry retrieval signal (don't index the auto-generated `customfield_10089`).
- **Closed / archived tickets**: still want them retrievable but not surfaced by default; sensitivity-like tier rather than hard exclude.
- **Mention edges to ex-employees**: a `@deactivated.user` should still resolve as a Person node, not a dangling string.
- **Attachment-heavy tickets** (screenshots, log files): need to route attachments through Office / image extractors and join back to the ticket.
- **Comment edits**: most APIs only give you the latest comment text; the edit history is lost unless you snapshot.

---

## 5. Chat / messaging (Slack / Teams / Discord / Zulip)

### A. Chunking
- **Chunk unit**: one chunk per thread (parent message + all replies).
  A loose channel without threading collapses to a sliding time window
  (default 30 minutes of contiguous messages).
- **Granularity**: thread granularity preserves the "what was the
  conversation?" context. DMs chunked per-conversation per-day are a
  reasonable default.
- **Overlap**: zero between threads. Within a sliding-window channel,
  overlap by one message to preserve reply context.
- **Hierarchical preservation**: every chunk carries
  `workspace > channel > thread_id`. Speaker turns inside the chunk
  use a canonical `[user] message` prefix so the retriever sees who
  said what.
- **Per-chunk metadata**: `channel_id`, `channel_name`, `is_private`,
  `thread_ts` (Slack) / `thread_id`, `participant_ids`, `message_count`,
  `reaction_summary`, `first_message_at`, `last_message_at`.

### B. Entity extraction
- **Entity types**: Person (native — from message author + `@mention`),
  Channel, Thread, External-link, Code-snippet, File-attachment.
- **Native vs NER**: Person and Channel are native (user IDs, channel
  IDs). NER on message body for Org / Product mentions.
- **Relationship types**: `message IN_THREAD thread`, `thread IN_CHANNEL channel`,
  `person AUTHORED message`, `person MENTIONED_IN message`,
  `thread REFERENCES ticket` (cross-source via URL).
- **Provenance**: `(workspace_id, channel_id, thread_ts, message_ts)`.

### C. Libraries
- [`slack-sdk`](https://github.com/slackapi/python-slack-sdk) — official, supports both Web API and Socket Mode.
- [`msgraph-sdk`](https://github.com/microsoftgraph/msgraph-sdk-python) — Teams via Microsoft Graph.
- [`discord.py`](https://github.com/Rapptz/discord.py) — gateway + REST.
- [`zulip`](https://github.com/zulip/python-zulip-api) — first-class threading model.

### D. Failure modes
- **DMs vs channels**: drastically different sensitivity defaults; DMs should default to `confidential`.
- **Slack message edits**: Slack's API gives you the current text only via `conversations.history`; edit history requires Enterprise Grid audit logs.
- **Slack threads with thousands of replies** (incidents): exceed token budgets; need per-day or per-100-message sub-chunking inside the thread.
- **Emoji-only messages** (`:eyes:`, `+1`): no retrieval value; filter from chunk text but preserve in reaction-summary metadata.
- **Bot messages**: high-volume CI / monitoring posts; opt-in inclusion list.
- **Deleted messages**: tombstones in the API; remove from index on cursor sync, not on deletion event (events get lost).

---

## 6. Email (Gmail / M365 mail / IMAP)

### A. Chunking
- **Chunk unit**: one chunk per email-in-a-thread *plus* one
  thread-summary chunk. Quoted reply text is stripped before chunking
  the individual email (otherwise every reply re-indexes the original).
- **Granularity**: per-email. Most emails are < 1024 tokens; long ones
  (newsletters, RFCs) get paragraph-split with the subject + sender as
  context header.
- **Overlap**: zero — quote-stripping handles inter-email continuity.
- **Hierarchical preservation**: `thread_subject > from > date` header
  on every chunk.
- **Per-chunk metadata**: `message_id`, `thread_id`, `in_reply_to`,
  `references` (list), `from`, `to` (list), `cc` (list), `subject`,
  `date`, `labels` (Gmail) / `categories` (M365), `has_attachments`,
  `attachment_mime_types`.

### B. Entity extraction
- **Entity types**: Person (sender, recipient — native from headers,
  Org (often derivable from email domain — `@acme.com` → Acme), Thread,
  Attachment.
- **Native vs NER**: headers are native. Body content is NER. The
  domain-to-org mapping is a high-precision native signal that often
  beats body NER for Org.
- **Relationship types**: `person SENT email`, `email IN_THREAD thread`,
  `email TO person`, `email CC person`, `email HAS_ATTACHMENT attachment`,
  `person WORKS_AT org` (derived from email-domain pattern over time).
- **Provenance**: RFC 2822 `Message-ID`.

### C. Libraries
- [`google-api-python-client`](https://github.com/googleapis/google-api-python-client) — Gmail API.
- [`msgraph-sdk`](https://github.com/microsoftgraph/msgraph-sdk-python) — M365 mail.
- [`imap-tools`](https://github.com/ikvk/imap_tools) — sane IMAP wrapper.
- [`mail-parser`](https://github.com/SpamScope/mail-parser) — RFC 822 parsing with header normalisation.
- [`talon`](https://github.com/mailgun/talon) — quoted-reply stripping (Mailgun OSS).

### D. Failure modes
- **HTML-only emails**: marketing senders use image-heavy HTML with no plaintext alt; need HTML-to-text pre-pass via [`html2text`](https://github.com/Alir3z4/html2text) or [`readability-lxml`](https://github.com/buriy/python-readability).
- **Encrypted (S/MIME / PGP) emails**: skip with `extractor=encrypted`.
- **Mailing-list digests**: one "email" is actually 30 emails concatenated; must detect and re-split on `From:` boundaries.
- **Auto-replies / OOO**: low signal, high volume; filter via Subject patterns.
- **Calendar invites delivered as email**: should route to the calendar extractor, not the email extractor.

---

## 7. Calendar / events (Outlook / Google Calendar)

### A. Chunking
- **Chunk unit**: one chunk per event. Recurring-event instances do
  *not* each get a chunk — the series is one chunk with a
  `recurrence_rule` field.
- **Granularity**: whole event. Events are atomic.
- **Overlap**: zero.
- **Hierarchical preservation**: `calendar > event_date > event_title`.
- **Per-chunk metadata**: `event_id`, `series_id`, `start`, `end`,
  `recurrence_rule`, `organiser`, `attendees` (list with response
  status), `location`, `meeting_url` (Zoom / Teams / Meet), `is_private`,
  `created_at`, `updated_at`.

### B. Entity extraction
- **Entity types**: Event (native), Person (organiser, attendees —
  native), Place / Location, Meeting (links to transcript source).
- **Native vs NER**: attendees are native. Body description gets NER
  for Project / Topic references.
- **Relationship types**: `person ORGANISED event`, `person ATTENDED event`,
  `event IN_SERIES series`, `event GENERATED_TRANSCRIPT transcript`
  (cross-source edge to meeting transcripts).
- **Provenance**: iCalendar UID.

### C. Libraries
- [`google-api-python-client`](https://github.com/googleapis/google-api-python-client) — Google Calendar.
- [`msgraph-sdk`](https://github.com/microsoftgraph/msgraph-sdk-python) — Outlook / M365 calendar.
- [`icalendar`](https://github.com/collective/icalendar) — `.ics` parsing.
- [`recurring-ical-events`](https://github.com/niccokunzmann/python-recurring-ical-events) — recurrence expansion.

### D. Failure modes
- **Recurring-event explosion**: expanding RRULE eagerly produces millions of chunks; expand lazily on query.
- **Private / "Show as busy" events**: should be redacted to `[private event]` body, but timing metadata can still index.
- **External attendees**: email addresses outside the org; useful for the Person graph but sensitivity-tier higher.
- **Cancelled events**: tombstones; treat like deletes.

---

## 8. CRM records (Salesforce / HubSpot / Dex / others)

### A. Chunking
- **Chunk unit**: one chunk per record (Contact, Account, Opportunity,
  Deal). Notes attached to a record are *additional* chunks (a
  Salesforce Note is to an Opportunity what a Jira comment is to a
  ticket).
- **Granularity**: per record. CRMs are inherently row-shaped.
- **Overlap**: zero.
- **Hierarchical preservation**: account-context header on every
  contact / opportunity chunk ("Acme Corp > Jane Smith (VP Eng)").
- **Per-chunk metadata**: every field on the record gets a structured
  metadata key. Field-set is operator-declared (you do not index all
  500 custom fields by default).

### B. Entity extraction
- **Entity types**: Person, Org, Opportunity, Deal, Activity (call /
  meeting / note). All native — CRMs are *the* structured-entity source.
  Body-text NER is almost pure noise here; trust the schema.
- **Native vs NER**: 99% native. NER on note bodies only, for
  cross-references not present as native field values.
- **Relationship types**: `person WORKS_AT org`,
  `person ATTENDED meeting`, `opportunity FOR_ORG org`,
  `opportunity OWNED_BY person`, `activity ABOUT person`,
  `activity ABOUT opportunity`. Dex's `relationships` endpoint is the
  canonical reference for the shape; Salesforce-style CRMs have richer
  variants.
- **Provenance**: per-record native ID.

### C. Libraries
- [`simple-salesforce`](https://github.com/simple-salesforce/simple-salesforce) — SOQL + REST.
- [`hubspot-api-python`](https://github.com/HubSpot/hubspot-api-python) — official HubSpot.
- [Dex API](https://docs.getdex.com/) — already wrapped in `kairix.connectors.dex_crm`.

### D. Failure modes
- **Schema drift**: CRMs let users add custom fields constantly; the connector cache of "which fields to index" must reconcile on each sync, not at install time.
- **Soft-deleted records**: most CRMs keep `is_deleted=true`; cursor sync must respect.
- **Merge events**: two contacts merged into one — the loser's edges must migrate to the winner.
- **Sensitive PII** (DOB, SSN-style fields): operator-declared field-level redaction list.

---

## 9. Web pages / crawl

### A. Chunking
- **Chunk unit**: same as markdown — heading-bounded section after
  HTML-to-markdown conversion. The main-content extraction step is the
  critical pre-pass.
- **Granularity**: 256–512 tokens.
- **Overlap**: zero.
- **Hierarchical preservation**: site > page-title > heading-path.
- **Per-chunk metadata**: `url`, `crawl_depth`, `crawled_at`,
  `canonical_url`, `http_status`, `outbound_link_count`,
  `content_language`.

### B. Entity extraction
- **Entity types**: Page (native — URL), Domain (derived), Person /
  Org / Place via NER on body, External-link.
- **Relationship types**: `page LINKS_TO page` (intra-crawl edge),
  `page CANONICAL_OF page` (dedup edge), `page ON_DOMAIN domain`.
- **Provenance**: URL + crawl timestamp.

### C. Libraries
- [`trafilatura`](https://github.com/adbar/trafilatura) — best-in-class main-content extraction.
- [`readability-lxml`](https://github.com/buriy/python-readability) — Mozilla-style readability port; lighter than trafilatura.
- [`Scrapy`](https://github.com/scrapy/scrapy) — full crawler with politeness controls.
- [`Playwright`](https://github.com/microsoft/playwright-python) — for JS-heavy sites where requests / Scrapy return shells.
- [`markitdown`](https://github.com/microsoft/markitdown) — HTML-to-markdown.

### D. Failure modes
- **JavaScript-only sites** (SPAs): static fetch returns `<div id="root"></div>`; need a headless-browser path.
- **Boilerplate** (nav / footer / cookie banner) leaking into chunks; trafilatura solves most.
- **Robots / rate limit**: respect; back-off on 429.
- **Login-walled content**: out of scope without auth integration; flag explicitly.
- **Infinite-scroll / pagination**: crawlers must cap depth and per-domain page count.

---

## 10. Meeting transcripts (Gong / Fireflies)

### A. Chunking
- **Chunk unit**: one chunk per *speaker turn group* of ~300 tokens —
  contiguous utterances by the same speaker, or by alternating speakers
  inside a single topic. Pure per-utterance chunking is too fine
  (many one-sentence utterances); whole-transcript chunking is too coarse.
- **Granularity**: speaker-bound topic segments. Use the upstream
  vendor's "topic" / "chapter" segmentation where available (Gong
  exposes this; Fireflies has a less granular version).
- **Overlap**: one prior utterance — turn-taking semantics need it.
- **Hierarchical preservation**: meeting_title > topic_label >
  speaker_turn.
- **Per-chunk metadata**: `meeting_id`, `topic_label`, `start_seconds`,
  `end_seconds`, `speakers` (list), `attendees`, `recorded_at`,
  `deep_link_url` (Gong / Fireflies link with timestamp anchor).

### B. Entity extraction
- **Entity types**: Person (native — speakers are diarised),
  Org / Account (often surfaced by the vendor — Gong tags by CRM
  account), Topic, Action-item (vendor-extracted), Question, Org /
  Product mentions via NER.
- **Native vs NER**: speakers, account, topic are native. Action items
  are vendor-extracted (semi-native — trust but verify). Body NER for
  Org / Product references.
- **Relationship types**: `person SPOKE_IN meeting`,
  `meeting ABOUT account`, `meeting GENERATED action_item`,
  `action_item ASSIGNED_TO person`.
- **Provenance**: meeting ID + start-seconds timestamp (so deep links
  jump to the right moment).

### C. Libraries
- [`gong-python`](https://pypi.org/project/gong/) — community wrapper; Gong's REST is small enough to roll directly.
- [Fireflies GraphQL API](https://docs.fireflies.ai/graphql-api/quickstart).
- [`pyannote.audio`](https://github.com/pyannote/pyannote-audio) — diarisation, if you're processing raw audio yourself.
- [`whisper`](https://github.com/openai/whisper) / [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) — transcription fallback.

### D. Failure modes
- **Diarisation errors**: speakers misattributed; downstream Person edges become wrong. Confidence threshold per turn helps.
- **Cross-talk regions**: low-confidence; consider excluding from index.
- **PII in transcripts** (credit card numbers spoken aloud, customer names): sensitivity tier should default high.
- **Action-item false positives**: vendors over-extract; treat as candidates, not ground truth.

---

## 11. Database / tabular (Airtable / BigQuery / SQL exports)

### A. Chunking
- **Chunk unit**: per-row with column-header context, *plus* per-table
  schema chunk. Wide tables get a per-row-grouped-by-natural-key chunk
  (a Contact row's columns laid out as "field: value" pairs).
- **Granularity**: row. Multi-row chunks only when rows are tightly
  coupled (e.g. line items of one invoice).
- **Overlap**: zero. Header repetition is the overlap-equivalent.
- **Hierarchical preservation**: database > schema > table > row-PK.
- **Per-chunk metadata**: every column becomes a structured key. PK,
  FK references, last-updated timestamp where available.

### B. Entity extraction
- **Entity types**: native everything. Column headers *are* the entity
  schema. `customer_id` columns become `LINKS_TO Customer` edges
  trivially.
- **Native vs NER**: 100% native unless free-text "notes" columns;
  those route to a sub-pass identical to issue-comment NER.
- **Relationship types**: FK edges are graph edges. Composite keys
  become multi-edge join nodes.
- **Provenance**: `(connection_id, schema, table, primary_key)`.

### C. Libraries
- [`pyairtable`](https://github.com/gtalarico/pyairtable) — Airtable.
- [`google-cloud-bigquery`](https://github.com/googleapis/python-bigquery) — BQ.
- [`SQLAlchemy`](https://github.com/sqlalchemy/sqlalchemy) — generic SQL.
- [`duckdb`](https://github.com/duckdb/duckdb) — for CSV / Parquet exports without an engine.

### D. Failure modes
- **Sparse rows** (mostly-null wide tables): waste tokens; filter columns where >80% null per chunk.
- **Free-text in tabular**: notes columns that contain a whole memo; route to a sub-chunker.
- **Large tables**: a 50M-row table cannot be chunked-and-embedded naively; require operator-declared sampling / filtering scope.
- **Schema migrations**: column renames break the chunker; F40-style schema-version tracking is necessary.
- **Privacy-tier columns**: explicit operator declaration of redact / hash columns.

---

## 12. File / blob storage (S3 / GCS / Azure Blob)

These connectors do not chunk themselves — they *route* by MIME type to
one of the chunkers above.

### A. Chunking
- **Chunk unit**: delegated to the format-specific chunker per MIME
  type. The blob connector is a dispatch layer, not a chunker.
- **Granularity, overlap, hierarchy**: inherited from the target
  chunker.
- **Per-chunk metadata**: bucket / container / object key, content-type,
  ETag, last-modified, storage-class, server-side-encryption flag.

### B. Entity extraction
- **Entity types**: Blob (native), Bucket / Container, plus everything
  the format-specific extractor yields.
- **Native vs NER**: blob identity is native; everything else inherits.
- **Relationship types**: `blob IN_BUCKET bucket`,
  `blob VERSIONED_BY blob` (version chain), plus the inherited shape.
- **Provenance**: `s3://bucket/key?versionId=...` etc.

### C. Libraries
- [`boto3`](https://github.com/boto/boto3) — S3.
- [`google-cloud-storage`](https://github.com/googleapis/python-storage) — GCS.
- [`azure-storage-blob`](https://github.com/Azure/azure-sdk-for-python) — Azure Blob.
- Content-type detection: [`python-magic`](https://github.com/ahupp/python-magic) (libmagic) over filename-only sniffing.

### D. Failure modes
- **Misleading extensions** (`.txt` files that are actually JSON or HTML); MIME sniffing required.
- **Huge objects** (GBs of CSV / Parquet); need streaming extractors that don't load the whole blob into memory.
- **Encrypted blobs** (KMS / customer-managed keys); require integrated decrypt or skip-with-reason.
- **Glacier / Archive tier**: cold-storage retrievals take hours; cursor sync must handle async restore.
- **Versioning**: object-version churn for "live" blobs (config files updated hourly) — dedupe by ETag, not just key.

---

## Cross-cutting recommendations

### Chunker dispatch shape

The F38 invariant is "Silver processing lives in `silver.py`". That
does not require a *single function* — it requires a single *surface*.
The recommended shape:

- `SilverProcessor` stays the public entry point.
- Internally, it dispatches via a `ChunkerRegistry` keyed by a
  `(source_kind, mime_type)` tuple, where `source_kind` is the
  connector's declared kind enum and `mime_type` is the extractor's
  detected content type.
- Each kind-aware chunker is a `Chunker` Protocol implementation
  (parallel to `Extractor`), lives under `kairix/core/connectors/
  chunkers/<kind>.py`, and is constructed once at registry-init.
- The dispatch lookup is `(kind, mime) → (kind, *) → (*, mime) →
  default-paragraph`. The fallback chain means a new connector ships
  with the current paragraph chunker until a kind-specific chunker is
  registered.

This keeps F38 honest (one Silver surface, one entry method, one place
to look) while admitting per-kind implementations. It also gives F36 a
natural hook: `tests/contracts/test_chunker_<kind>.py` per registered
chunker.

### Per-source extractor + chunker pairing

| Source kind | Extractor | Chunker |
|---|---|---|
| Markdown / wiki | `markdown-it-py` AST | heading-bounded section |
| docx | `python-docx` | heading-bounded section |
| xlsx | `openpyxl` | per-row + per-sheet |
| pptx | `python-pptx` | per-slide |
| pdf | `pdfplumber` (+ `ocrmypdf` pre-pass for scans) | layout-aware section |
| Source code | `tree-sitter` | AST function / class |
| Issue / ticket | vendor API + markdown converter | per-ticket + per-comment |
| Chat | vendor API | per-thread |
| Email | `mail-parser` + `talon` | per-email (quote-stripped) |
| Calendar | vendor API + `icalendar` | per-event |
| CRM | vendor API | per-record |
| Web | `trafilatura` | heading-bounded section |
| Meeting transcript | vendor API | speaker-turn group |
| Database | SQL driver | per-row + per-table schema |
| Blob storage | `python-magic` dispatch | delegated by MIME |

### Entity-extraction pipeline shape

Two passes, in this order:

1. **Native pass** — the extractor returns an `ExtractedDocument`
   carrying a `native_entities: tuple[EntitySignal, ...]` field
   populated from structured fields (CRM IDs, Slack user IDs, ticket
   assignees, file authors, AST symbols). This pass is deterministic
   and lossless.
2. **NER pass** — Silver runs spaCy / GLiNER over chunk text and emits
   *candidate* `EntitySignal`s tagged with confidence. The Curator
   layer (ADR-018) is responsible for resolving candidates against
   the native graph: a Person mention "Jane Smith" in a Slack message
   resolves against the Dex contact graph if available.

Per-chunk provenance: every `EntitySignal` carries
`(connector_name, page_id, chunk_index, char_span, extraction_source)`
where `extraction_source ∈ {"native", "ner_spacy", "ner_gliner",
"vendor_topic"}`. Native always wins on conflict; NER candidates with
a native resolution get their `entity_id` upgraded from
`ner:Jane Smith` to `person:dex_contact_id_42`.

### Versioning per chunker

**Yes — chunkers must be versioned.** F40 already requires
`extractor_version` on extractors; the same rationale applies more
strongly to chunkers because a chunker change re-shapes every chunk
in the index (extractor changes typically only affect new content).

Concrete proposal: add a `version: str` class-level attribute on the
`Chunker` Protocol, write through to a new `chunks.chunker_version`
column, and add a new fitness function (`F55`?) enforcing the
declaration. Re-chunk-on-version-bump becomes a background reconciler:
when the registered chunker for a `(kind, mime)` reports a higher
version than the chunks in the index, schedule re-chunking through the
existing reconciler harness.

Rationale: without this, a chunker improvement (e.g. switching markdown
from paragraph to heading-bounded) creates a permanent split-index
state where old chunks and new chunks coexist with different retrieval
characteristics. That is exactly the failure mode F40 was created to
prevent on the extractor side; chunkers have the same shape and the
same need.

### Areas of genuine debate

Three places where the community has not converged and operators
should expect to tune:

1. **Semantic-boundary chunking vs structural chunking**. Approaches
   like LlamaIndex's `SemanticSplitterNodeParser` use embedding
   distance to split mid-paragraph. They win on free-prose corpora
   but lose to structural chunking when the source has real structure
   (code, slides, tickets). Recommendation: structural where
   available, semantic only as a fallback for `text/plain` blobs.
2. **Contextual retrieval (chunk-relative summaries)**. Anthropic's
   approach prepends an LLM-generated summary of the chunk's role in
   its parent document. Real recall lift, real ingest cost. For
   kairix, this belongs as an *optional* enrichment under a feature
   flag — not a default.
3. **Chunk size for embedding vs synthesis**. The single-size approach
   under-serves both retrieval and synthesis. The emerging consensus
   is *parent-child chunking*: small chunks (256 tokens) embedded for
   retrieval, large chunks (1024–2048 tokens) returned for the answer
   stage. Worth a follow-up ADR.
