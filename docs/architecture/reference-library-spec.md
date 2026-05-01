# Kairix Reference Library — Starter Library Specification

**Purpose:** Define the subset of openly-licensed sources that ship with kairix as a reference library. Provides first-run search experience, reproducible benchmarks, and domain demonstration.

**Licence requirement:** T1–T3 only (CC0, MIT, Apache, CC-BY). No CC-BY-SA, no NC, no verify-needed sources. Every source must have a confirmed licence from the repo LICENSE file.

**Date:** 2026-04-25

---

## Collection Structure

```
reference-library/
├── CATALOGUE.md                    # Source tracking, licence, version, date verified
├── LICENSE-NOTICES.md              # Attribution notices for MIT/Apache/CC-BY sources
│
├── agentic-ai/                    # Domain 1
│   ├── openai-cookbook/
│   ├── dair-ai-prompts/
│   ├── panaversity-agentic/
│   ├── ms-gen-ai-beginners/
│   ├── ms-prompts-edu/
│   └── awesome-ai-system-prompts/
│
├── engineering/                    # Domain 4
│   ├── adr-examples/
│   ├── madr/
│   ├── soc-docs/
│   ├── 18f-guides/
│   └── 12factor/
│
├── data-and-analysis/             # Domain 5
│   ├── dbt-docs/
│   ├── mlops-guide/
│   └── posthog-docs/
│
├── product-and-design/            # Domains 3, 7
│   ├── gong-practices/
│   ├── usds-playbook/
│   ├── posthog-handbook/
│   └── awesome-retrospectives/
│
├── operating-models/              # Domain 2
│   ├── cncf-platform-model/
│   └── jph-ways-of-working/
│
├── leadership-and-culture/        # Domains 6, 8
│   ├── awesome-open-company/
│   ├── jph-awesome-developing/
│   └── manager-readmes/
│
├── economics-and-strategy/        # Domains 10, 11
│   ├── jph-business-model-canvas/
│   └── jph-startup-guide/
│
├── personal-effectiveness/        # Domain 12
│   ├── jph-okrs/
│   ├── open-spaced-repetition/
│   └── mindful-programming/
│
├── health-and-fitness/            # Domain 13
│   ├── exercises-db/              # wrkout or yuhonas (pick one)
│   ├── awesome-quantified-self/
│   ├── awesome-healthcare/
│   ├── awesome-mental-health/
│   ├── usda-dietary-guidelines/
│   └── circadiaware/
│
├── philosophy/                    # Domain 14
│   ├── classical-eastern/         # Tao Te Ching, Art of War, Dhammapada, Bhagavad Gita
│   ├── classical-western/         # Meditations, Enchiridion, Discourses
│   ├── indian-philosophy/         # Vivekananda, Gandhi, Arthashastra, Yoga Sutras
│   ├── martial-arts-philosophy/   # Bushido, Hagakure, Book of Five Rings
│   └── suttacentral/             # Buddhist suttas (CC0)
│
├── family-and-education/          # Domains 15, 17
│   ├── cdc-milestones/
│   ├── openstax-lifespan/
│   ├── openstax-child-development/
│   ├── montessori-method/
│   ├── dewey-democracy-education/
│   ├── australian-eylf/
│   └── awesome-parenting/
│
├── industry-standards/            # Domain 19
│   ├── bian-apis/
│   ├── hl7-fhir/
│   └── mosip-docs/
│
├── regulatory/                    # Domain 20 (AU/NZ confirmed CC-BY)
│   ├── apra-prudential/
│   ├── asic-guides/
│   └── nz-treasury/
│
└── eval/                          # Benchmark suite
    ├── queries.json               # 150+ queries with gold doc mappings
    ├── relevance-judgments.json    # Graded relevance per query-doc pair
    └── README.md                  # Eval methodology
```

---

## Source List — Confirmed T1–T3

### agentic-ai/ (6 sources, ~230+ files)

| Source | Licence | Clone command | Extract |
|--------|---------|--------------|---------|
| OpenAI Cookbook | MIT | `git clone --depth 1 https://github.com/openai/openai-cookbook.git` | `examples/**/*.md`, `articles/**/*.md` |
| DAIR.AI Prompt Engineering Guide | MIT | `git clone --depth 1 https://github.com/dair-ai/Prompt-Engineering-Guide.git` | `guides/**/*.md` |
| Panaversity Learn Agentic AI | Apache 2.0 | `git clone --depth 1 https://github.com/panaversity/learn-agentic-ai.git` | `**/*.md` |
| MS Generative AI for Beginners | MIT | `git clone --depth 1 https://github.com/microsoft/generative-ai-for-beginners.git` | `??-*/*.md` |
| MS Prompts for Education | MIT | `git clone --depth 1 https://github.com/microsoft/prompts-for-edu.git` | `**/*.md` |
| Awesome AI System Prompts | MIT | `git clone --depth 1 https://github.com/dontriskit/awesome-ai-system-prompts.git` | `**/*.md` |

### engineering/ (5 sources, ~200+ files)

| Source | Licence | Clone command | Extract |
|--------|---------|--------------|---------|
| JPH Architecture Decision Records | CC0 | `git clone --depth 1 https://github.com/joelparkerhenderson/architecture-decision-record.git` | `**/*.md` |
| MADR | MIT | `git clone --depth 1 https://github.com/adr/madr.git` | `docs/**/*.md`, `template/**/*.md` |
| SOC Documentation | MIT | `git clone --depth 1 https://github.com/madirish/ossocdocs.git` | `**/*.md` |
| 18F Guides | CC0 | `git clone --depth 1 https://github.com/18F/guides.git` | `content/**/*.md` |
| 12-Factor App | CC0 | `git clone --depth 1 https://github.com/heroku/12factor.git` | `content/**/*.md` |

### data-and-analysis/ (3 sources, ~290+ files)

| Source | Licence | Clone command | Extract |
|--------|---------|--------------|---------|
| dbt Core Docs | Apache/CC-BY | `git clone --depth 1 https://github.com/dbt-labs/docs.getdbt.com.git` | `website/docs/**/*.md` |
| MLOps Guide | Apache 2.0 | `git clone --depth 1 https://github.com/MLOps-Guide/MLOps-Guide.git` | `docs/**/*.md` |
| PostHog Docs | MIT | `git clone --depth 1 https://github.com/PostHog/posthog.com.git` | `contents/docs/**/*.md` |

### product-and-design/ (4 sources, ~350+ files)

| Source | Licence | Clone command | Extract |
|--------|---------|--------------|---------|
| Gong Product Practices | CC-BY 4.0 | `git clone --depth 1 https://github.com/gong-io/product-practices.git` | `**/*.md` |
| USDS Playbook | CC0 | `git clone --depth 1 https://github.com/usds/playbook.git` | `**/*.md` |
| PostHog Handbook | MIT | (same repo as docs) | `contents/handbook/**/*.md` |
| Awesome Retrospectives | MIT | `git clone --depth 1 https://github.com/josephearl/awesome-retrospectives.git` | `**/*.md` |

### operating-models/ (2 sources, ~65+ files)

| Source | Licence | Clone command | Extract |
|--------|---------|--------------|---------|
| CNCF TAG App Delivery | Apache 2.0 | `git clone --depth 1 https://github.com/cncf/tag-app-delivery.git` | `**/*.md` |
| JPH Ways of Working | CC0 | `git clone --depth 1 https://github.com/joelparkerhenderson/ways-of-working.git` | `**/*.md` |

### leadership-and-culture/ (3 sources, ~170+ files)

| Source | Licence | Clone command | Extract |
|--------|---------|--------------|---------|
| Awesome Open Company | CC0 | `git clone --depth 1 https://github.com/opencompany/awesome-open-company.git` | `**/*.md` |
| JPH Awesome Developing | CC0 | `git clone --depth 1 https://github.com/joelparkerhenderson/awesome-developing.git` | `**/*.md` |
| Manager READMEs | CC0 (curator) | verify current repo URL | `**/*.md` |

### economics-and-strategy/ (2 sources, ~60+ files)

| Source | Licence | Clone command | Extract |
|--------|---------|--------------|---------|
| JPH Business Model Canvas | CC0 | `git clone --depth 1 https://github.com/joelparkerhenderson/business-model-canvas.git` | `**/*.md` |
| JPH Startup Guide | CC0 | `git clone --depth 1 https://github.com/SixArm/startup-business-guide.git` | `**/*.md` |

### personal-effectiveness/ (3 sources, ~65+ files)

| Source | Licence | Clone command | Extract |
|--------|---------|--------------|---------|
| JPH OKRs | CC0 | `git clone --depth 1 https://github.com/joelparkerhenderson/objectives-and-key-results.git` | `**/*.md` |
| Open Spaced Repetition | MIT | `git clone --depth 1 https://github.com/open-spaced-repetition/fsrs4anki.git` | `**/*.md` |
| Mindful Programming | CC0 | `git clone --depth 1 https://github.com/code-in-flow/mindful-programming.git` | `**/*.md` |

### health-and-fitness/ (6 sources, ~3,300+ entries)

| Source | Licence | Clone command | Extract |
|--------|---------|--------------|---------|
| Free Exercise DB | Unlicense | `git clone --depth 1 https://github.com/yuhonas/free-exercise-db.git` | `exercises/**/*.json` → convert |
| Awesome Quantified Self | CC0 | `git clone --depth 1 https://github.com/woop/awesome-quantified-self.git` | `README.md` |
| Awesome Healthcare | CC0 | `git clone --depth 1 https://github.com/kakoni/awesome-healthcare.git` | `README.md` |
| Awesome Mental Health | CC0 | `git clone --depth 1 https://github.com/dreamingechoes/awesome-mental-health.git` | `README.md` |
| USDA Dietary Guidelines | Public Domain | Download PDF from realfood.gov | Convert with markitdown |
| Circadiaware | CC-BY | `git clone --depth 1 https://github.com/Circadiaware/VLiDACMel-entrainment-therapy-non24.git` | `**/*.md`, `**/*.html` |

### philosophy/ (public domain canon, ~200+ files)

| Source | Licence | Method | Texts |
|--------|---------|--------|-------|
| Standard Ebooks | CC0 | Clone individual repos | Tao Te Ching, Art of War |
| Project Gutenberg / GITenberg | Public Domain | Clone per-book repos | Dhammapada, Bhagavad Gita, Yoga Sutras, Chuang Tzu, Bushido, Analects, Meditations, Enchiridion, Discourses |
| SuttaCentral | CC0 | Bulk download | Satipatthana Sutta, Metta Sutta, Dhammapada, Anapanasati Sutta |
| Bhagavad Gita (structured) | CC0/PD | `git clone --depth 1 https://github.com/vedicscriptures/bhagavad-gita-data.git` | Verse-by-verse JSON |
| Vivekananda/Gandhi/Tagore | Public Domain | Gutenberg download | Complete works selections |
| Hatha Yoga Pradipika | Public Domain | Archive.org PDF | 4 chapters |
| Arthashastra | Public Domain | Archive.org PDF | 15 books |
| Charaka Samhita | Public Domain | Archive.org PDF | 8 sthanas |
| Gandhi Ashram Observance | Public Domain | mkgandhi.org PDF | 11 observances |

### family-and-education/ (7 sources, ~130+ files)

| Source | Licence | Method | Extract |
|--------|---------|--------|---------|
| CDC Milestones | Public Domain | Download from cdc.gov | HTML → markdown |
| OpenStax Lifespan Development | CC-BY 4.0 | Download from openstax.org | Chapter PDFs → markdown |
| OpenStax Child Development | CC-BY 4.0 | Download from LibreTexts | Chapter HTML → markdown |
| Montessori Method | Public Domain | UPenn Digital Library | HTML → markdown |
| Dewey Democracy & Education | Public Domain | Gutenberg #852 | Text → markdown |
| Australian EYLF | AU Govt/PD | Download PDF from acecqa.gov.au | PDF → markdown |
| Awesome Parenting | CC0 | `git clone --depth 1 https://github.com/daugaard/awesome-parenting.git` | `README.md` |

### industry-standards/ (3 sources)

| Source | Licence | Clone command | Extract |
|--------|---------|--------------|---------|
| BIAN Semantic APIs | Apache 2.0 | `git clone --depth 1 https://github.com/bian-official/public.git` | `release/**/*.yaml` |
| HL7 FHIR | CC0 | Download spec from build.fhir.org | Resource definitions |
| MOSIP Docs | MPL 2.0/MIT | `git clone --depth 1 https://github.com/mosip/documentation.git` | `docs/**/*.md` |

### regulatory/ (3 sources — AU/NZ confirmed CC-BY)

| Source | Licence | Method | Extract |
|--------|---------|--------|---------|
| APRA Prudential Standards | CC-BY 4.0 | Scrape apra.gov.au/industries/1/standards | HTML → markdown |
| ASIC Regulatory Guides | CC-BY 4.0 | Scrape asic.gov.au regulatory guides | PDF → markdown |
| NZ Treasury | CC-BY 4.0 | Download from treasury.govt.nz | PDF → markdown |

---

## Eval Suite Design

Each collection gets 10–15 benchmark queries with gold document mappings:

```json
{
  "query_id": "AGI-001",
  "query": "How does chain-of-thought prompting improve reasoning?",
  "domain": "agentic-ai",
  "gold_docs": ["dair-ai-prompts/guides/prompts-advanced-usage.md"],
  "relevance": 3,
  "category": "conceptual"
}
```

**Target:** 150+ queries across all collections, graded relevance (0–3), covering:
- Conceptual search ("what is X?")
- Procedural search ("how to do X?")
- Entity search ("who/what wrote about X?")
- Cross-collection search ("compare approaches to X")

---

## Normalisation Requirements

All sources must be normalised to consistent markdown before inclusion:

1. **File naming:** kebab-case, no spaces, `.md` extension
2. **Frontmatter:** YAML frontmatter with `title`, `source`, `licence`, `url`
3. **Encoding:** UTF-8, LF line endings
4. **Images:** Strip or convert to alt-text descriptions
5. **Links:** Convert internal links to relative paths within collection
6. **Maximum file size:** Split files >50KB at heading boundaries
7. **Minimum file size:** Merge files <500 bytes into parent

---

## Packaging

```
# Install as optional during setup
kairix setup --with-reference-library

# Or install separately
kairix reference-library install

# Verify installation
kairix reference-library status
```

The reference library is downloaded from a GitHub release artifact (not bundled in the pip package to keep install size small). The `install` command downloads, extracts, and indexes the collection.
