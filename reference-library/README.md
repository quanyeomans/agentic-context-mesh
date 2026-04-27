# Kairix Reference Library

The reference library is a ready-made collection of documents that ships
with kairix. It gives you something useful to search the moment you start
using kairix, before you add any of your own documents.

Every document in the library is freely licensed and openly available.
Nothing is proprietary, and nothing is hidden behind a paywall.


## Why it exists

The reference library serves four purposes:

1. **First-run search experience.** When you install kairix, you can
   search high-quality content straight away -- no setup needed.

2. **Benchmark baseline.** The library is the fixed document set used by
   kairix's built-in quality checks (benchmarks). Because everyone uses
   the same documents, results are comparable across machines and versions.

3. **Entity graph seed.** When kairix builds its knowledge graph (a map
   of how people, concepts, and topics connect), the reference library
   provides a starting set of well-structured relationships.

4. **Quality comparator.** When you add your own documents, kairix can
   measure how well its search performs on your content compared to the
   reference library. This helps you spot gaps in indexing or formatting.


## What's inside

The library contains **6,161 normalised documents** drawn from
**64 openly-licensed sources** across **14 collections**.

### Collections

| Collection | Description |
|---|---|
| agentic-ai | Prompt engineering, LLM evaluation, agent frameworks |
| data-and-analysis | Analytics, experimentation, data pipelines, MLOps |
| economics-and-strategy | Business models, marketing mix, startup guides |
| engineering | Software practices, architecture records, API design, observability |
| foundations | Logic, computational neuroscience, formal methods |
| leadership-and-culture | Team development, open leadership, service design |
| operating-models | Platform engineering, ways of working |
| personal-effectiveness | Goal-setting (OKRs), spaced repetition, mindful work |
| philosophy | Classical texts -- Eastern, Western, and martial traditions |
| product-and-design | Retrospectives, product practices, digital service playbooks |
| security | Software bill of materials (SBOM), governance and compliance |
| family-and-education | Parenting and education resources (planned) |
| health-and-fitness | Health, mental health, fitness, self-tracking (planned) |
| industry-standards | Banking (BIAN), identity (MOSIP) standards (planned) |

Collections marked "planned" have sources catalogued but no normalised
documents yet.

### Knowledge domains

The 14 collections span roughly 20 knowledge domains, including:
AI and machine learning, software engineering, data science, analytics,
product management, design, security, economics, strategy, leadership,
organisational culture, platform engineering, philosophy, logic,
neuroscience, personal productivity, education, health, fitness, and
governance.


## How to install

```bash
kairix reference-library install
```

This downloads the reference library and indexes it. The command is safe
to run more than once -- it will skip documents that are already indexed.

To check what version you have installed:

```bash
kairix reference-library status
```

To remove the reference library:

```bash
kairix reference-library uninstall
```


## How it relates to your own documents

The reference library is kept completely separate from your document store
(knowledge store). They live in different locations and are indexed
independently.

- **Your documents** go into your knowledge store. You control what goes
  in, how it is organised, and when it is updated.
- **The reference library** is read-only. Kairix manages it, and updates
  come through new releases.

When you search in kairix, results can come from both sources. You can
filter results to show only your documents, only the reference library,
or both.


## Licences

Every source in the reference library uses an open licence. The licences
fall into three tiers:

| Tier | Licences | What it means |
|---|---|---|
| Tier 1 -- Public domain | CC0, Unlicense, Public Domain | No restrictions. Use however you like. |
| Tier 2 -- Permissive | MIT, Apache 2.0, MPL 2.0 | You must keep the copyright notice when you redistribute. |
| Tier 3 -- Attribution | CC-BY 3.0, CC-BY 4.0, OGL 3.0 | You must credit the original author when you share the work. |

No source in the library uses a "copyleft" licence (one that requires
you to release your own work under the same terms). No source uses a
"non-commercial" restriction.

Full attribution details for every source are in
[LICENSE-NOTICES.md](LICENSE-NOTICES.md). The complete list of sources,
file counts, and verification dates is in [CATALOGUE.md](CATALOGUE.md).


## How to contribute new sources

If you want to suggest a new source for the reference library:

1. **Check the licence.** The source must use one of the Tier 1, 2, or 3
   licences listed above. No proprietary content, no non-commercial
   restrictions.

2. **Check the quality.** The source should be well-written, reasonably
   maintained, and useful for at least one of the 20 knowledge domains.

3. **Open an issue** in the kairix repository with:
   - Source name and URL
   - Licence type
   - Which collection it belongs to
   - A short description of why it is valuable

4. **Normalisation.** The kairix team will run the source through the
   normalisation pipeline (the process that converts documents to a
   standard format). You do not need to do this yourself.

### What makes a good source

- Written in English (other languages may be supported later)
- Available as Markdown, plain text, or structured HTML
- Actively maintained or historically significant
- Covers a topic that complements the existing collections
- Large enough to be useful (at least 5 documents), or a single
  high-quality reference document


## File format

Every document in the reference library is a Markdown file. The
normalisation pipeline converts source documents to a consistent format:

- UTF-8 encoding
- Standard Markdown (CommonMark)
- Front matter stripped (metadata is tracked in the catalogue, not in
  each file)
- Relative links preserved where possible
- Binary files (images, PDFs) excluded -- only text content is kept


## Directory structure

```
reference-library-normalised/
  README.md              -- this file
  CATALOGUE.md           -- full list of sources with licence and file counts
  LICENSE-NOTICES.md     -- attribution notices for all sources
  agentic-ai/            -- collection directory
    source-name/         -- one subdirectory per source
      document.md        -- normalised document
      ...
  data-and-analysis/
    ...
  (other collections)
```


## Version

This reference library was normalised on 2026-04-25.
