# Open Knowledge Source Directory

**A reference guide for building AI agent knowledge bases with ground truth documents.**

146+ openly-licensed document collections across 20 knowledge domains. Compiled from 300+ web queries via deep research and parallel GitHub verification. All URLs and licence claims sourced from primary references.

**Last updated:** April 2026
**Licence:** This directory itself is released under CC0 (Public Domain). Use it however you like.

---

## Table of Contents

- [Licence Tier Key](#licence-tier-key)
- [Domain 1: AI / Agentic Operations](#domain-1-ai--agentic-operations)
- [Domain 2: Operating Models](#domain-2-operating-models)
- [Domain 3: Product Management](#domain-3-product-management)
- [Domain 4: Engineering Practices](#domain-4-engineering-practices)
- [Domain 5: Data & Analytics](#domain-5-data--analytics)
- [Domain 6: Leadership & Management](#domain-6-leadership--management)
- [Domain 7: Task & Project Management](#domain-7-task--project-management)
- [Domain 8: Culture & Ways of Working](#domain-8-culture--ways-of-working)
- [Domain 9: Marketing & Growth](#domain-9-marketing--growth)
- [Domain 10: Sales & Relationships](#domain-10-sales--relationships)
- [Domain 11: Economics & Strategy](#domain-11-economics--strategy)
- [Domain 12: Personal Effectiveness & PKM](#domain-12-personal-effectiveness--pkm)
- [Domain 13: Health, Fitness & Longevity](#domain-13-health-fitness--longevity)
- [Domain 14: Eastern Philosophy & Contemplative Practice](#domain-14-eastern-philosophy--contemplative-practice)
- [Domain 15: Family, Relationships & Parenting](#domain-15-family-relationships--parenting)
- [Domain 16: Mental Wellbeing & Neurodiversity](#domain-16-mental-wellbeing--neurodiversity)
- [Domain 17: Education Philosophy & Learning Science](#domain-17-education-philosophy--learning-science)
- [Domain 18: Community & Place](#domain-18-community--place)
- [Domain 19: Industry Framework Standards](#domain-19-industry-framework-standards)
- [Domain 20: APAC Regulatory Frameworks](#domain-20-apac-regulatory-frameworks)
- [Gap Analysis](#gap-analysis)
- [Proprietary & Commercial Equivalents](#proprietary--commercial-equivalents)
- [How to Use This Directory](#how-to-use-this-directory)

---

## Licence Tier Key

Every source in this directory is tagged with a licence tier. Verify the LICENSE file at source before using any collection.

| Tier | Licence | What it means |
|:----:|---------|---------------|
| **T1** | CC0 / Public Domain / Unlicense | Maximum freedom. No attribution required. No restrictions. |
| **T2** | MIT / Apache 2.0 / MPL | Permissive open source. Attribution required in code/docs. |
| **T3** | CC-BY 4.0 / CC-BY 3.0 | Free use including commercial, with attribution. |
| **T4** | CC-BY-SA | Free use with attribution, but derivatives must carry the same licence. |
| **T5** | CC-BY-NC / CC-BY-NC-SA | Non-commercial use only. Cannot redistribute in commercial products. |
| **T6** | OGL v3 / Government Open Licence | Government publications. Very permissive but distinct from CC. Verify per jurisdiction. |
| **T7** | Access-available, no explicit licence | Publicly readable but copyright reserved by default. Verify before redistribution. |
| **T8** | Proprietary / restricted | Cannot redistribute. Reference only. |

**Rule of thumb:** T1-T3 sources can be freely used in most knowledge base projects. T4 requires share-alike consideration. T5+ requires careful licence review.

---

## How to Read Each Entry

Every source is listed with:
- **Name** -- the collection or repository name
- **URL** -- canonical link to the source
- **Licence** -- confirmed tier with specific licence noted
- **Format** -- native format and conversion notes
- **Volume** -- estimated document/file count
- **Description** -- one-line practitioner summary

Each domain section ends with a **Priority Acquisition Table** listing sources from highest to lowest signal.

---

## Domain 1: AI / Agentic Operations

*Prompt engineering, agent design patterns, LLM integration, evaluation methods.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 1 | OpenAI Cookbook | https://github.com/openai/openai-cookbook | T2 (MIT) | Jupyter + Markdown | 60+ notebooks | Canonical open pattern library for AI implementation -- RAG, function calling, agents, fine-tuning. |
| 2 | DAIR.AI Prompt Engineering Guide | https://github.com/dair-ai/Prompt-Engineering-Guide | T2 (MIT) | Markdown | 20+ guides | Definitive open reference on prompt engineering -- zero-shot, few-shot, chain-of-thought, ReAct, agentic patterns. |
| 3 | Microsoft Generative AI for Beginners | https://github.com/microsoft/generative-ai-for-beginners | T2 (MIT) | Markdown | 21 lessons | End-to-end curriculum from prompt basics through agentic architectures and responsible AI. |
| 4 | Panaversity -- Learn Agentic AI | https://github.com/panaversity/learn-agentic-ai | T2 (Apache) | Markdown | 50+ docs | Compares agent frameworks (OpenAI Agents SDK, CrewAI, AutoGen, LangGraph), covers handoffs and guardrails. |
| 5 | Microsoft Prompts for Education | https://github.com/microsoft/prompts-for-edu | T2 (MIT) | Markdown | 50+ prompts | Prompt patterns by role and use case. Contributors include leading AI-in-work researchers. |
| 6 | Awesome AI System Prompts | https://github.com/dontriskit/awesome-ai-system-prompts | T2 (MIT) | Markdown | 30+ docs | Real system prompts from shipping products (v0, Manus, ChatGPT, Cursor). |
| 7 | LangChain Documentation & Examples | https://github.com/langchain-ai/langchain | T2 (MIT) | Markdown + Python | 150+ docs | Design patterns for agent systems, memory management, tool/retrieval workflows, multi-agent orchestration. |
| 8 | Awesome AI Agent Papers 2026 | https://github.com/VoltAgent/awesome-ai-agent-papers | T2 (MIT) | Markdown | 2 files | Weekly-curated academic papers on agent engineering, memory systems, evaluation, workflows. |
| 9 | Awesome AI Agents | https://github.com/jim-schwoebel/awesome_ai_agents | T2 (Apache) | Markdown | 1 large file | Catalogue of 1500+ agent tools, frameworks, and resources. |
| 10 | LangChain Docs (standalone) | https://github.com/langchain-ai/langchain | T2 (MIT) | Markdown | 100+ docs | Agent system documentation covering memory, tools, and orchestration patterns. |

### Priority Acquisition -- Domain 1

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | OpenAI Cookbook | T2 (MIT) | Low -- native markdown |
| 2 | DAIR.AI Prompt Engineering Guide | T2 (MIT) | Low -- native markdown |
| 3 | Microsoft Generative AI for Beginners | T2 (MIT) | Low -- native markdown |
| 4 | Panaversity Learn Agentic AI | T2 (Apache) | Low -- native markdown |
| 5 | LangChain Docs + Examples | T2 (MIT) | Medium -- large repo |
| 6 | Awesome AI System Prompts | T2 (MIT) | Low -- native markdown |
| 7 | Microsoft Prompts for Education | T2 (MIT) | Low -- native markdown |

---

## Domain 2: Operating Models

*Team topologies, platform teams, capability models, organisational design.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 11 | CNCF Platform Engineering Maturity Model | https://github.com/cncf/tag-app-delivery | T2 (Apache) | Markdown | 15+ docs | Authoritative four-level maturity framework for platform engineering. |
| 12 | GitLab Handbook (full) | https://handbook.gitlab.com | T4 (CC-BY-SA) | Markdown | 2,000+ pages | Most comprehensive publicly available operating model for a modern technology company. |
| 13 | 18F Engineering & Agile Guides | https://github.com/18F/guides | T1 (CC0) | Markdown | 60+ guides | US government digital service perspective on DevOps, security, accessibility, procurement. |
| 14 | UK GDS Service Manual | https://www.gov.uk/service-manual | T6 (OGL v3) | Markdown + HTML | 50+ guides | 14-point Service Standard -- one of the most influential operating frameworks in government digital delivery. |
| 15 | Joel Parker Henderson -- Ways of Working | https://github.com/joelparkerhenderson/ways-of-working | T1 (CC0) | Markdown | 50+ docs | Working agreement templates, team health frameworks, decision-making structures. |

### Priority Acquisition -- Domain 2

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | 18F Engineering & Agile Guides | T1 (CC0) | Low |
| 2 | Joel Parker Henderson -- Ways of Working | T1 (CC0) | Low |
| 3 | CNCF Platform Engineering Model | T2 (Apache) | Low |
| 4 | GitLab Handbook | T4 (CC-BY-SA) | Medium -- large repo, SA notice needed |
| 5 | UK GDS Service Manual | T6 (OGL v3) | Medium -- HTML conversion |

---

## Domain 3: Product Management

*Discovery, delivery, strategy, roadmapping, user research.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 16 | Gong Product Handbook | https://github.com/gong-io/product-practices | T3 (CC-BY) | Markdown | 30+ docs | Real product practices from a high-growth B2B SaaS company. |
| 17 | PostHog Handbook | https://github.com/PostHog/posthog.com/tree/master/contents/handbook | T2 (MIT) | Markdown | 250+ docs | Full company handbook including product strategy, product-led growth, and engineering practices. |
| 18 | USDS Digital Services Playbook | https://github.com/usds/playbook | T1 (CC0) | Markdown | 20+ docs | 13 plays covering product design, user research, agile delivery, and measurement. |
| 19 | Intercom Product Management Book | https://www.intercom.com/resources/books/intercom-product-management | T7 (no licence) | Web | ~150 pages | Product strategy, customer research, prioritisation frameworks. Reference only. |

### Priority Acquisition -- Domain 3

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | USDS Digital Services Playbook | T1 (CC0) | Low |
| 2 | PostHog Handbook | T2 (MIT) | Low |
| 3 | Gong Product Handbook | T3 (CC-BY) | Low |

---

## Domain 4: Engineering Practices

*Architecture decisions, incident management, observability, deployment, system design.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 20 | Joel Parker Henderson -- ADRs | https://github.com/joelparkerhenderson/architecture-decision-record | T1 (CC0) | Markdown | 30+ docs | Canonical open reference on Architecture Decision Records with multiple template formats. |
| 21 | MADR (Markdown ADR) | https://github.com/adr/madr | T2 (MIT) | Markdown | 15+ docs | Lean ADR template format widely adopted in open source projects. |
| 22 | Twelve-Factor App | https://github.com/heroku/12factor | T1 (CC0) | Markdown | 40+ docs | Foundational deployment philosophy for cloud-native engineering. |
| 23 | Open Source SOC Documentation | https://github.com/madirish/ossocdocs | T2 (MIT) | Markdown | 54 files | SOPs, policies, and procedures for Security Operations Centres. |
| 24 | Architecture of Open Source Applications | http://aosabook.org | T3 (CC-BY 3.0) | HTML | 20+ deep dives | Real-world architecture walkthroughs of major systems (nginx, LLVM, Git). |
| 25 | Professional Programming (charlax) | https://github.com/charlax/professional-programming | T7 (verify) | Markdown | 22 files | Curated collection of engineering craft practices -- code review, debugging, testing, mentoring. |
| 26 | System Design Primer | https://github.com/donnemartin/system-design-primer | T4 (CC-BY-SA) | Markdown | 23 files | Most starred system design resource on GitHub -- scalability, caching, distributed systems. |
| 27 | Fast.ai Fastbook | https://github.com/fastai/fastbook | T4 (CC-BY-SA) | Jupyter + Markdown | 65+ files | Best-in-class applied ML curriculum with top-down teaching approach. |

### Priority Acquisition -- Domain 4

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | Joel Parker Henderson -- ADRs | T1 (CC0) | Low |
| 2 | Twelve-Factor App | T1 (CC0) | Low |
| 3 | MADR | T2 (MIT) | Low |
| 4 | Open Source SOC Documentation | T2 (MIT) | Low |
| 5 | 18F Engineering Guides | T1 (CC0) | Low |
| 6 | Architecture of Open Source Applications | T3 (CC-BY 3.0) | Medium -- HTML conversion |
| 7 | System Design Primer | T4 (CC-BY-SA) | Low -- SA notice needed |

---

## Domain 5: Data & Analytics

*Data literacy, analytics engineering, MLOps, experiment design, causal inference.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 28 | dbt Core Documentation | https://github.com/dbt-labs/docs.getdbt.com | T2/T3 (Apache/CC-BY) | Markdown | 50+ docs | Analytics engineering guides, SQL patterns, maturity models, testing patterns. |
| 29 | PostHog Documentation (Analytics) | https://github.com/PostHog/posthog.com/tree/master/contents/docs | T2 (MIT) | Markdown | 200+ docs | Product analytics, A/B testing, feature flags, experiment design, data governance. |
| 30 | MLOps Guide | https://github.com/MLOps-Guide/MLOps-Guide | T2 (Apache) | Markdown | 40+ docs | Full MLOps flow from data versioning through model serving and monitoring. |
| 31 | Microsoft EconML / Uber CausalML | https://github.com/Microsoft/EconML / https://github.com/uber/causalml | T2 (Apache) | Notebooks | 40+ examples | Advanced experiment design -- causal inference, bias detection, heterogeneous treatment effects. |
| 32 | Locally Optimistic (data strategy blog) | https://github.com/locallyoptimistic/locallyoptimistic.github.io | T3 (CC-BY, verify) | Markdown (Jekyll) | 80+ articles | One of the most respected data strategy publications, written by senior data practitioners. |
| 33 | Data Science Knowledge Vault | https://github.com/jfabend/datascience_knowledge_vault | T7 (verify) | Markdown + wikilinks | 516 files | Largest single-source interlinked knowledge base covering ML, statistics, data engineering. |
| 34 | Data Engineer Handbook | https://github.com/DataExpert-io/data-engineer-handbook | T7 (verify) | Markdown | 28 files | Comprehensive data engineering curriculum -- modelling, orchestration, quality, governance. |

### Priority Acquisition -- Domain 5

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | dbt Core Documentation | T2/T3 | Medium -- large repo |
| 2 | MLOps Guide | T2 (Apache) | Low |
| 3 | PostHog Docs (Analytics) | T2 (MIT) | Low |
| 4 | Microsoft EconML / Uber CausalML | T2 (Apache) | Medium -- notebooks |
| 5 | Locally Optimistic | T3 (verify) | Low -- Jekyll extraction |

---

## Domain 6: Leadership & Management

*Delegation, feedback, 1:1s, team health, performance, management operating systems.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 35 | Open Leadership & Management Cookbook | https://github.com/dobromirmontauk/open-leadership-management-cookbook | T7 (verify) | Markdown | 30-50 recipes | Structured management "recipes" -- 1:1s, delegation, feedback, org health checks. |
| 36 | Awesome Open Company | https://github.com/opencompany/awesome-open-company | T1 (CC0) | Markdown | 20+ entries | Curated list of companies with publicly available handbooks and operating procedures. |
| 37 | Manager READMEs (curated) | https://github.com/mgrassoh/awesome-manager-readmes | T1 (CC0 curator) | Markdown | 100+ READMEs | Diverse leadership philosophies as 1-3 page manager manifestos. |
| 38 | Joel Parker Henderson -- Awesome Developing | https://github.com/joelparkerhenderson/awesome-developing | T1 (CC0) | Markdown | 50+ docs | Management frameworks, preference statements, decision templates. |

### Priority Acquisition -- Domain 6

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | Awesome Open Company | T1 (CC0) | Low |
| 2 | Joel Parker Henderson -- Awesome Developing | T1 (CC0) | Low |
| 3 | Manager READMEs | T1 (CC0 curator) | Low |
| 4 | Open Leadership & Management Cookbook | T7 (verify) | Low |

---

## Domain 7: Task & Project Management

*Agile delivery, kanban, sprint ceremonies, retrospectives, estimation.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 39 | Awesome Retrospectives | https://github.com/josephearl/awesome-retrospectives | T2 (MIT) | Markdown | 50+ formats | Most comprehensive open collection of retrospective techniques and facilitation guides. |
| 40 | Basecamp/37signals Handbook | https://github.com/basecamp/handbook | T7 (no licence) | Markdown | 25+ docs | How the creators of Basecamp organise work -- async-first, 6-week cycles, Shape Up companion. |
| 41 | 18F Agile Guides | https://github.com/18F/agile | T1 (CC0) | Markdown | 25+ guides | Methodology guidance for running agile in complex, regulated organisations. |
| 42 | USDS Playbook (Agile Plays) | https://github.com/usds/playbook | T1 (CC0) | Markdown | Subset of 13 | Plays 4-8 directly address agile delivery, sprint structure, iterative development. |
| 43 | UK GDS Agile Delivery | https://www.gov.uk/service-manual/agile-delivery | T6 (OGL v3) | HTML | 20+ guides | Discovery, alpha, beta, live phases with concrete entry/exit criteria. |

### Priority Acquisition -- Domain 7

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | 18F Agile Guides | T1 (CC0) | Low |
| 2 | USDS Playbook | T1 (CC0) | Low |
| 3 | Awesome Retrospectives | T2 (MIT) | Low |
| 4 | UK GDS Agile Delivery | T6 (OGL v3) | Medium -- HTML conversion |

---

## Domain 8: Culture & Ways of Working

*Remote work, psychological safety, learning organisations, values operationalisation.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 44 | GitLab All-Remote Guide | https://handbook.gitlab.com/handbook/company/culture/all-remote/guide/ | T4 (CC-BY-SA) | Markdown | 50+ docs | Most detailed publicly available playbook for remote-first organisational design. |
| 45 | Automattic Design Handbook | https://github.com/Automattic/design-handbook | T7 (verify) | Markdown | 30+ docs | How distributed creative teams work, communicate, and maintain culture. |
| 46 | Valve Employee Handbook | https://github.com/nuba/valve-handbook-for-new-employees | T7 (no licence) | PDF | 1 doc (~50pp) | Historically important document on flat/autonomous organisational culture. |
| 47 | PostHog Handbook -- Culture | https://github.com/PostHog/posthog.com/tree/master/contents/handbook | T2 (MIT) | Markdown | 30+ docs | Radical transparency about compensation, team structure, hiring, and culture principles. |
| 48 | Awesome Psychological Safety | https://github.com/psychological-safety-yogis/awesome-psych-safety | T7 (verify) | Markdown | 30+ resources | Psychological safety research, team health models, inclusivity practices. |
| 49 | Open Organization Workbook | https://github.com/open-organization | T3 (CC-BY, verify) | PDF | 150+ pages | Community-built guide on transparency, collaboration, and adaptability in organisations. |

### Priority Acquisition -- Domain 8

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | PostHog Handbook -- Culture | T2 (MIT) | Low |
| 2 | Open Organization Workbook | T3 (CC-BY, verify) | Medium -- PDF conversion |
| 3 | GitLab All-Remote Guide | T4 (CC-BY-SA) | Low -- SA notice needed |

---

## Domain 9: Marketing & Growth

*Content strategy, positioning, GTM, customer acquisition, growth experimentation.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 50 | GitLab Handbook -- Marketing | https://handbook.gitlab.com/handbook/marketing/ | T4 (CC-BY-SA) | Markdown | 80+ docs | Full marketing operating model -- demand gen, content, events, brand, competitive intelligence. |
| 51 | PostHog Handbook -- Growth & Marketing | https://github.com/PostHog/posthog.com/tree/master/contents/handbook | T2 (MIT) | Markdown | 20+ docs | Brand positioning, product-led growth motion, customer acquisition without traditional sales. |

**Note:** This is the weakest professional domain for open-licensed collections. Most marketing methodology (Reforge, Maven, HubSpot Academy) is paywalled.

### Priority Acquisition -- Domain 9

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | PostHog Growth & Marketing | T2 (MIT) | Low |
| 2 | GitLab Marketing Handbook | T4 (CC-BY-SA) | Low -- SA notice needed |

---

## Domain 10: Sales & Relationships

*Consultative selling, account management, customer success, procurement.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 52 | GitLab Handbook -- Sales | https://handbook.gitlab.com/handbook/sales/ | T4 (CC-BY-SA) | Markdown | 80+ docs | Most detailed open enterprise sales operating model -- MEDDPIC, account planning, territory management. |
| 53 | PostHog -- Customer Success | https://github.com/PostHog/posthog.com/tree/master/contents/handbook/cs-and-onboarding | T2 (MIT) | Markdown | 20+ docs | Customer onboarding as structured practice without a traditional CS team. |
| 54 | USDS -- Vendor & Procurement Guidance | https://playbook.usds.gov / https://techfarhub.usds.gov | T1 (CC0) | Markdown + HTML | 20+ docs | Definitive open resource for consultative selling into government. |
| 55 | Joel Parker Henderson -- Startup Business Guide | https://github.com/SixArm/startup-business-guide | T1 (CC0) | Markdown | 35+ docs | OKRs with revenue metrics, pitch structures, fundraising, business development. |

### Priority Acquisition -- Domain 10

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | USDS Procurement Guidance | T1 (CC0) | Low |
| 2 | Joel Parker Henderson -- Startup Guide | T1 (CC0) | Low |
| 3 | PostHog Customer Success | T2 (MIT) | Low |

---

## Domain 11: Economics & Strategy

*Competitive dynamics, pricing, market analysis, business model frameworks.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 56 | Joel Parker Henderson -- Business Model Canvas | https://github.com/joelparkerhenderson/business-model-canvas | T1 (CC0) | Markdown | 25+ docs | Value propositions, customers, channels, resources, cost structure, revenue streams. |
| 57 | Open Business Models (protontypes) | https://github.com/protontypes/open-business-models | T7 (verify) | Markdown | 35+ entries | Sustainable business strategies -- open-core, dual licensing, SaaS, freemium, crowdfunding. |
| 58 | GitLab -- Competitive Intelligence & Pricing | https://handbook.gitlab.com/handbook/company/strategy/ | T4 (CC-BY-SA) | Markdown | 20+ docs | Actual pricing strategy rationale and competitive positioning decisions from a public company. |

**Note:** This is the weakest domain overall. Porter, Blue Ocean, and BCG frameworks are all proprietary or academic.

### Priority Acquisition -- Domain 11

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | Joel Parker Henderson -- Business Model Canvas | T1 (CC0) | Low |
| 2 | Open Business Models | T7 (verify) | Low |

---

## Domain 12: Personal Effectiveness & PKM

*Learning methods, note-taking systems, knowledge curation, goal design.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 59 | Joel Parker Henderson -- OKRs | https://github.com/joelparkerhenderson/objectives-and-key-results | T1 (CC0) | Markdown | 40+ docs | OKR examples, templates, grading methods, and personal effectiveness frameworks. |
| 60 | Open Spaced Repetition | https://github.com/open-spaced-repetition | T2 (MIT) | Markdown + code | 25+ docs | FSRS algorithm documentation -- evidence-based learning scheduling. |
| 61 | Awesome Knowledge Management | https://github.com/brettkromkamp/awesome-knowledge-management | T7 (verify) | Markdown | 50+ resources | Guide to PKM tools, Zettelkasten implementations, and knowledge curation systems. |
| 62 | Digital Gardeners (MaggieAppleton) | https://github.com/MaggieAppleton/digital-gardeners | T7 (verify) | Markdown | 25+ examples | Digital garden philosophy, visual metaphors for knowledge, learning-in-public approaches. |
| 63 | Zettelkasten.de | https://zettelkasten.de/introduction/ | T3 (CC-BY, verify) | HTML | 20+ docs | Canonical documentation of the Zettelkasten method -- atomic notes, time-based IDs, connection principles. |

### Priority Acquisition -- Domain 12

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | Joel Parker Henderson -- OKRs | T1 (CC0) | Low |
| 2 | Open Spaced Repetition | T2 (MIT) | Low |
| 3 | Zettelkasten.de | T3 (CC-BY, verify) | Medium -- web scrape |

---

## Domain 13: Health, Fitness & Longevity

*Exercise databases, nutrition science, sleep, quantified self, circadian health.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 64 | wrkout/exercises.json | https://github.com/wrkout/exercises.json | T1 (Unlicense) | JSON | 2,500+ exercises | Most comprehensive open exercise database -- name, muscle group, equipment, instructions. |
| 65 | yuhonas/free-exercise-db | https://github.com/yuhonas/free-exercise-db | T1 (Unlicense) | JSON | 800+ exercises | Curated exercise database with hosted images. Quality over quantity. |
| 66 | Awesome Quantified Self | https://github.com/woop/awesome-quantified-self | T1 (CC0) | Markdown | 1 file | Meta-index for the QS ecosystem -- devices, apps, platforms by category. |
| 67 | Awesome Biomarkers | https://github.com/markwk/awesome-biomarkers | T7 (verify) | Markdown + JSON | ~10 files | Blood tests, biomarkers, optimal ranges, tracking companies. |
| 68 | Circadiaware (circadian health) | https://github.com/Circadiaware/VLiDACMel-entrainment-therapy-non24 | T3 (CC-BY) | Markdown + HTML | 15 files | Deep dive into circadian rhythm science with practical therapeutic protocol. |
| 69 | Open Food Facts | https://world.openfoodfacts.org/data | T4 (ODbL/CC-BY-SA) | JSONL/CSV | 3M+ products | Largest open nutrition database -- ingredients, Nutri-Score, NOVA classification. |
| 70 | Awesome Healthcare | https://github.com/kakoni/awesome-healthcare | T1 (CC0) | Markdown | 5 files | Directory of open-source healthcare software, standards (FHIR, HL7), EHR systems. |
| 71 | USDA Dietary Guidelines 2025-2030 | https://www.fns.usda.gov/cnpp/dietary-guidelines-americans | T1 (Public Domain) | PDF + HTML | 50+ docs | Current authoritative US nutrition policy framework. First edition to prioritise whole foods. |
| 72 | NHMRC Australian Dietary Guidelines | https://www.eatforhealth.gov.au | T6 (AU Govt) | PDF | 40+ docs | Authoritative Australian dietary guidance with practical implementation materials. |
| 73 | OpenStax Anatomy & Physiology | https://openstax.org | T3 (CC-BY) | HTML/PDF | 40+ chapters | Rigorous physiology grounding for nutrition -- macronutrient metabolism, caloric balance. |
| 74 | WHO Open Health Publications | https://iris.who.int | T5 (CC-BY-NC-SA, mixed) | PDF + HTML | 200+ docs | Global consensus on preventive health, chronic disease, mental health, healthy ageing. |
| 75 | NIH / PubMed Central Open Access | https://pmc.ncbi.nlm.nih.gov | T3 (CC-BY, mixed) | PDF + XML | Curate | Rapidly expanding open access biomedical research -- longevity, metabolic health, sleep science. |
| 76 | Bryan Johnson Blueprint | https://blueprint.bryanjohnson.com | T7 (no licence) | Web | 50+ protocols | Most systematically documented personal longevity protocol. Reference only. |
| 77 | MIT OCW Weight Training (PE 720) | https://ocw.mit.edu/courses/pe-720-weight-training-spring-2006/ | T5 (CC-BY-NC-SA) | PDF + HTML | 15+ modules | University-level resistance training principles -- progressive overload, periodisation. |
| 78 | Archive.org Strength Training Texts | https://archive.org | T1/T7 (verify per title) | PDF | 30+ texts | Historical and contemporary strength training methodology texts. |
| 79 | Awesome Mental Health | https://github.com/dreamingechoes/awesome-mental-health | T1 (CC0) | Markdown | 5 files | Curated links on burnout, anxiety, impostor syndrome in knowledge-worker contexts. |
| 80 | Shinrin-yoku Research (PubMed Central) | https://pmc.ncbi.nlm.nih.gov | T3 (CC-BY) | PDF | 50+ articles | Open access research on forest bathing -- immune function, cortisol, blood pressure. |

### Priority Acquisition -- Domain 13

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | wrkout/exercises.json | T1 (Unlicense) | Low |
| 2 | yuhonas/free-exercise-db | T1 (Unlicense) | Low |
| 3 | USDA Dietary Guidelines | T1 (Public Domain) | Low |
| 4 | Awesome Quantified Self | T1 (CC0) | Low |
| 5 | Awesome Healthcare | T1 (CC0) | Low |
| 6 | OpenStax Anatomy & Physiology | T3 (CC-BY) | Low |
| 7 | Circadiaware | T3 (CC-BY) | Low |

---

## Domain 14: Eastern Philosophy & Contemplative Practice

*Classical texts, meditation, martial arts philosophy, yoga, Indian philosophy, Japanese methodology.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 81 | Project Gutenberg -- Eastern Canon | https://www.gutenberg.org | T1 (Public Domain) | Text/HTML | 8-10 core texts | Tao Te Ching, Art of War, Dhammapada, Bhagavad Gita, Yoga Sutras, Confucian Analects. |
| 82 | Standard Ebooks -- Eastern Texts | https://github.com/standardebooks | T1 (CC0) | XHTML | 2+ books | Highest-quality open-format editions of Tao Te Ching and Art of War. |
| 83 | GITenberg (Gutenberg on GitHub) | https://gitenberg.github.io/ | T1 (Public Domain) | Multiple | Varies | Git-native repos per public domain book. |
| 84 | DharmicData -- Hindu Sacred Texts | https://github.com/bhavykhatri/DharmicData | T6 (ODbL) | JSON | 7+ texts | Bhagavad Gita, Mahabharata, Ramayana, Vedas in structured JSON with Sanskrit and English. |
| 85 | Internet Sacred Text Archive | https://archive.sacred-texts.com | T1/T5 (PD/CC-BY-NC) | HTML | 300+ texts | Most comprehensive single collection of digitised Eastern sacred/philosophical texts. |
| 86 | SuttaCentral -- Buddhist Suttas | https://suttacentral.net | T1 (CC0) | HTML/Text | 1,000+ suttas | Most comprehensive multilingual collection of early Buddhist texts. CC0 for maximum accessibility. |
| 87 | Access to Insight -- Buddhist Practice | https://www.accesstoinsight.org | T7 (verify) | HTML | 1,500+ docs | Practice-oriented Buddhist study guides, translator essays, structured learning paths. |
| 88 | Hagakure / Book of Five Rings / Bushido | https://www.gutenberg.org/ebooks/12096 | T1 (Public Domain) | PDF/HTML | 3 texts | Martial arts philosophy -- bushido ethics, strategic principles, warrior philosophy. |
| 89 | Awesome Meditation | https://github.com/legrk/awesome-meditation | T7 (verify) | Markdown | 1 file | Curated entry point for meditation apps, research centres, and guided practices. |
| 90 | Mindful Programming | https://github.com/code-in-flow/mindful-programming | T1 (CC0) | Markdown | 1 doc | Bridges Jon Kabat-Zinn's mindfulness framework with daily professional work. |
| 91 | Project Gutenberg -- Stoic Texts | https://www.gutenberg.org | T1 (Public Domain) | Text/HTML | 6+ texts | Meditations (Marcus Aurelius), Enchiridion (Epictetus), Letters (Seneca). |
| 92 | Project Gutenberg -- Vivekananda, Gandhi, Tagore | http://www.gutenberg.org | T1 (Public Domain) | Text/HTML | 100+ texts | Complete works of three major modern Indian thinkers -- Vedanta, satyagraha, philosophical essays. |
| 93 | Bhagavad Gita (GitHub structured) | https://github.com/vedicscriptures/bhagavad-gita-data | T1 (CC0/PD) | JSON | 1 structured dataset | Verse-by-verse JSON with multiple translations -- perfect for atomic knowledge entries. |
| 94 | Hatha Yoga Pradipika | https://archive.org/details/HathaYogaPradipika-SanskritTextWithEnglishTranslatlionAndNotes | T1 (Public Domain) | PDF | 1 text | Oldest surviving Hatha Yoga text (15th century). Physical yoga as preparation for meditation. |
| 95 | Kautilya Arthashastra | https://archive.org/details/kautilyasarthasastraenglishtranslationshamasastrir.1929 | T1 (Public Domain) | PDF | 1 text | Ancient Indian statecraft, economics, diplomacy, and management -- 15 books, 150 chapters. |
| 96 | Charaka Samhita (Ayurveda) | https://archive.org/details/CharakaSamhitaTextWithEnglishTanslationP.V.Sharma | T1 (Public Domain) | PDF | 1 text | Foundational Ayurvedic medical text -- tridosha theory, diagnostics, diet, therapeutics. |
| 97 | Vipassana Texts (Dhamma.org) | https://www.dhamma.org | T7 (verify scope) | Web + audio | 30+ texts | Goenka tradition insight meditation -- technique instructions and discourse texts. |
| 98 | Wikisource -- Philosophy Texts | https://en.wikisource.org | T4 (CC-BY-SA) | Wikitext | 100+ texts | Tao Te Ching, Analects, I Ching, Hagakure, Stoic texts, Western philosophy classics. |
| 99 | Stanford Encyclopedia of Philosophy (Asian) | https://plato.stanford.edu | T5 (CC-BY-NC-ND) | HTML | 50+ entries | Most rigorous open philosophical reference -- Confucius, Daoism, Buddhism, Japanese aesthetics. |
| 100 | Archive.org -- Indian Philosophy | https://archive.org | T1 (Public Domain) | PDF | 100+ texts | Dasgupta's History of Indian Philosophy, Nagarjuna, Sacred Books of the Jainas. |
| 101 | Yoga Sutras of Patanjali | https://www.gita-society.com/wp-content/uploads/PDF/Patanjali-yogasutra.IGS.pdf | T7 (verify) | PDF | 1 text | 196 sutras on the nature of mind and the eight-limbed path of psychological development. |
| 102 | UNESCO ICH -- India | https://ich.unesco.org/en/state/india-IN | T3 (CC-BY) | HTML | 16 entries | Substantive dossiers on yoga, Kumbh Mela, Vedic chanting, and other cultural practices. |
| 103 | Gandhi -- Ashram Observance | https://www.mkgandhi.org/ebks/ashramobservance.pdf | T1 (Public Domain) | PDF | 1 doc | Practical instructions for ethical community design -- 11 observances for daily life. |

### Priority Acquisition -- Domain 14

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | SuttaCentral | T1 (CC0) | Low |
| 2 | Project Gutenberg -- Eastern Canon | T1 (Public Domain) | Low |
| 3 | Standard Ebooks | T1 (CC0) | Low |
| 4 | Bhagavad Gita (GitHub JSON) | T1 (CC0) | Low |
| 5 | Project Gutenberg -- Vivekananda, Gandhi, Tagore | T1 (Public Domain) | Low |
| 6 | Hatha Yoga Pradipika | T1 (Public Domain) | Low -- PDF |
| 7 | Kautilya Arthashastra | T1 (Public Domain) | Low -- PDF |
| 8 | Charaka Samhita | T1 (Public Domain) | Low -- PDF |
| 9 | Gandhi Ashram Observance | T1 (Public Domain) | Low -- PDF |
| 10 | UNESCO ICH India | T3 (CC-BY) | Low -- HTML |

---

## Domain 15: Family, Relationships & Parenting

*Child development, attachment, communication frameworks, family finance, education methodology.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 104 | OpenStax Lifespan Development | https://openstax.org/details/books/lifespan-development | T3 (CC-BY) | HTML/PDF | 16 chapters | Gold-standard open textbook -- Piaget, Vygotsky, Erikson, attachment theory. |
| 105 | Child Growth & Development (LibreTexts) | https://socialsci.libretexts.org/Bookshelves/Early_Childhood_Education | T3 (CC-BY) | HTML | 15 chapters | Practical emphasis on observation, guidance techniques, and home environments. |
| 106 | CDC Developmental Milestones | https://www.cdc.gov/act-early/milestones/index.html | T1 (Public Domain) | HTML + PDF | 30+ guides | Most authoritative evidence-based developmental milestone reference (2 months to 5 years). |
| 107 | WHO/UNICEF Nurturing Care Framework | https://nurturing-care.org/ncf-for-ecd/ | T5 (CC-BY-NC-SA) | PDF | 44pp + supp | Five-component framework: health, nutrition, responsive caregiving, safety, early learning. |
| 108 | UNICEF Care for Child Development | https://www.unicef.org/documents/care-child-development | T5 (CC-BY-NC-SA) | PDF | 200+ pages | Age-specific (0-5) recommendations for play and communication activities. |
| 109 | NHS Start for Life | https://www.nhs.uk/start-for-life/ | T6 (OGL v3) | HTML | 100+ pages | Practical, plain-language guidance on pregnancy, birth, baby care, and toddler development. |
| 110 | Awesome Parenting | https://github.com/daugaard/awesome-parenting | T1 (CC0) | Markdown | 1 file | CC0-licensed curated link collection for parents. |
| 111 | Australian EYLF (Early Years Learning Framework) | https://www.acecqa.gov.au | T1 (AU Govt/PD) | PDF | 50-100pp | National framework -- Belonging, Being, Becoming. Includes Indigenous perspectives. |
| 112 | Montessori Method (1912 text) | https://digital.library.upenn.edu/women/montessori/method/method.html | T1 (Public Domain) | HTML | 1 text | Maria Montessori's foundational articulation of scientific pedagogy. |
| 113 | Harvard Center on the Developing Child | https://developingchild.harvard.edu | T7 (verify) | PDF + HTML | 30+ briefs | Brain architecture, toxic stress, serve-and-return interaction, executive function, resilience. |
| 114 | Zero to Three | https://www.zerotothree.org | T7 (verify) | Web + PDF | 40+ docs | Leading nonprofit for the birth-to-three developmental period. |
| 115 | Gottman Research Summaries | https://www.gottman.com/about/research/couples/ | T7 (no licence) | Web | 30+ summaries | Four Horsemen, repair attempts, Sound Relationship House. Reference only. |
| 116 | NVC Handouts | https://nonviolentcommunication.com/resources/handouts-and-learning-materials/ | T7 (verify) | PDF | 40+ worksheets | Nonviolent Communication four-part framework -- observation, feeling, need, request. |
| 117 | FIRE Framework (family finance) | https://github.com/YassinEldeeb/fire-framework | T4 (CC-BY-SA) | Markdown | 10+ docs | Spending frameworks, investment strategies, and financial freedom planning. |

### Priority Acquisition -- Domain 15

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | CDC Developmental Milestones | T1 (Public Domain) | Low |
| 2 | Awesome Parenting | T1 (CC0) | Low |
| 3 | Montessori Method | T1 (Public Domain) | Low |
| 4 | Australian EYLF | T1 (AU Govt) | Low -- PDF |
| 5 | OpenStax Lifespan Development | T3 (CC-BY) | Low |
| 6 | Child Growth & Development | T3 (CC-BY) | Low |

---

## Domain 16: Mental Wellbeing & Neurodiversity

*Stress management, mindfulness, ADHD, autism, dyslexia, psychological resilience.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 118 | Neurodiversity Journal (Sage Open Access) | https://journals.sagepub.com/home/ndy | T3 (CC-BY) | PDF + HTML | 50+ articles | Only dedicated peer-reviewed open-access journal on neurodiversity. |
| 119 | Awesome Neurodiversity | https://github.com/awesome-neurodiversity/awesome-neurodiversity | T7 (verify) | Markdown | 1 file | Curated directory of neurodiversity resources, events, and community organisations. |
| 120 | ASAN Publications | https://autisticadvocacy.org/resources/ | T7 (verify) | PDF | 15+ guides | Authentic autistic self-advocacy -- written by autistic people. |
| 121 | Russell Barkley EF Factsheet | https://www.russellbarkley.org/factsheets/ADHD_EF_and_SR.pdf | T7 (verify) | PDF | 1 doc | Executive function framework for ADHD -- the externalisation principle. |
| 122 | CHADD -- ADHD Resources | https://chadd.org/understanding-adhd/ | T7 (verify) | Web | 30+ guides | Diagnosis, treatment, parenting, workplace accommodation for ADHD. |
| 123 | Understood.org | https://www.understood.org | T7 (verify) | Web | 100+ articles | ADHD, dyslexia, processing differences -- practical guidance for families. |
| 124 | International Dyslexia Association | https://or.dyslexiaida.org/recommended-resources/ | T7 (verify) | PDF | 30+ factsheets | Phonological processing, reading science, intervention approaches. |
| 125 | 29ki/29k (Aware App) | https://github.com/29ki/29k | T7 (AGPL) | App content | Varies | Non-profit mental health app with mindfulness exercises and CBT-adjacent content. |
| 126 | Awesome Psychological Safety | https://github.com/psychological-safety-yogis/awesome-psych-safety | T7 (verify) | Markdown | 30+ resources | Psychological safety research and practical frameworks for team leaders. |

### Priority Acquisition -- Domain 16

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | Neurodiversity Journal | T3 (CC-BY) | Low |
| 2 | Awesome Mental Health | T1 (CC0) | Low |
| 3 | Mindful Programming | T1 (CC0) | Low |

---

## Domain 17: Education Philosophy & Learning Science

*Pedagogy, adult learning, constructivism, evidence-based learning strategies.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 127 | OpenStax Education Textbooks | https://openstax.org | T3 (CC-BY) | HTML/PDF | 40-80 chapters | Peer-reviewed textbooks on educational psychology, lifespan development. |
| 128 | Dewey -- Democracy and Education | https://www.gutenberg.org/ebooks/852 | T1 (Public Domain) | Text | 1 text | Philosophical foundation of progressive education since 1916. |
| 129 | Freire -- Pedagogy of the Oppressed | https://archive.org/details/PedagogyOfTheOppressed-English-PauloFriere | T7 (verify edition) | PDF | 1 text | Education as liberation -- the "banking model" critique and consciousness-raising. |
| 130 | Learning Scientists | https://www.learningscientists.org/downloadable-materials | T5 (CC-BY-NC-ND) | PDF | 25+ guides | Six evidence-based strategies: spaced practice, retrieval, interleaving, elaboration, concrete examples, dual coding. |
| 131 | MIT OCW -- Education Courses | https://ocw.mit.edu | T5 (CC-BY-NC-SA) | PDF | 30+ modules | Constructivism, Montessori theory, adult learning, curriculum design. |
| 132 | Open Spaced Repetition | https://github.com/open-spaced-repetition | T2 (MIT) | Markdown + code | 25+ docs | FSRS algorithm documentation for evidence-based learning scheduling. |
| 133 | Reggio Emilia Approach | https://www.reggiochildren.it/en/reggio-emilia-approach/ | T7 (verify) | Web | 25+ docs | Hundred languages of children, environment as third teacher, documentation as pedagogy. |

### Priority Acquisition -- Domain 17

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | Dewey -- Democracy and Education | T1 (Public Domain) | Low |
| 2 | Open Spaced Repetition | T2 (MIT) | Low |
| 3 | OpenStax Education Textbooks | T3 (CC-BY) | Low |

---

## Domain 18: Community & Place

*Placemaking, asset-based community development, community resilience, third places.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 134 | ABCD Institute | https://abcdinstitute.org | T7 (verify) | HTML + PDF | 60+ docs | Asset-Based Community Development -- mapping community strengths rather than deficits. |
| 135 | Transition Towns Handbook | https://www.cs.toronto.edu/~sme/CSC2600/transition-handbook.pdf | T4 (CC-BY-SA) | PDF | 1 handbook | Most structured open methodology for building community resilience around climate change. |
| 136 | Strong Towns | https://www.strongtowns.org | T7 (verify) | Web | 50+ articles | Neighbourhood-scale development, financial resilience, and the economics of place. |
| 137 | Project for Public Spaces | https://www.pps.org | T7 (verify) | Web | 40+ guides | Global authority on placemaking -- public space design, third places, civic participation. |

### Priority Acquisition -- Domain 18

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | Transition Towns Handbook | T4 (CC-BY-SA) | Low -- PDF |
| 2 | ABCD Institute | T7 (verify) | Low |

---

## Domain 19: Industry Framework Standards

*Cross-sector technical standards for banking, healthcare, manufacturing, telecom, utilities, retail.*

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 138 | BIAN Semantic APIs | https://github.com/bian-official/public | T2 (Apache) | YAML (OpenAPI) | 250+ APIs | Most comprehensive open technical standard for banking architecture. |
| 139 | HL7 FHIR | https://build.fhir.org/ | T1 (CC0) | HTML/JSON/XML | 50+ resources | International standard for healthcare information exchange. CC0 for maximum adoption. |
| 140 | MOSIP (Open Source Digital ID) | https://docs.mosip.io/1.2.0 | T2 (MPL/MIT) | Markdown | 100+ docs | Complete modular open-source national ID platform. Used by 11+ countries. |
| 141 | India Stack Documentation | https://ispirt.in/our-industry/indiastack/ | T7 (verify) | Web + PDF | Varies | Aadhaar, UPI, Account Aggregator, OCEN, ONDC documentation. |
| 142 | UNESCO ICH India Entries | https://ich.unesco.org/en/state/india-IN | T3 (CC-BY) | HTML | 16 entries | Dossiers on yoga, Kumbh Mela, Vedic chanting, and other inscribed cultural practices. |
| 143 | TKDL (CSIR India) | https://www.tkdl.res.in | T7 (verify) | Database | 500K+ formulations | Traditional Knowledge Digital Library -- Ayurveda, Siddha, Unani, Yoga across 5 languages. |
| 144 | Ministry of AYUSH Guidelines | https://ayush.gov.in | T7 (verify) | PDF | 50+ docs | Government framework for Ayurveda, Siddha, Yoga, Unani delivery in regulated health systems. |

**Note:** Major industry frameworks (APQC PCF, ISA-95, eTOM, IEC CIM, ARTS) are proprietary. See the [Proprietary & Commercial Equivalents](#proprietary--commercial-equivalents) section.

### Priority Acquisition -- Domain 19

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | HL7 FHIR | T1 (CC0) | Medium -- large spec |
| 2 | BIAN Semantic APIs | T2 (Apache) | Low -- GitHub YAML |
| 3 | MOSIP Documentation | T2 (MPL/MIT) | Low |
| 4 | UNESCO ICH India | T3 (CC-BY) | Low -- HTML |

---

## Domain 20: APAC Regulatory Frameworks

*Financial regulation, telecommunications, digital service standards across Asia-Pacific.*

### Australia (Confirmed CC-BY 4.0)

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 145 | APRA Prudential Standards | https://www.apra.gov.au | T3 (CC-BY 4.0) | HTML + PDF | 100+ standards | Prudential standards for banking, insurance, superannuation. Confirmed CC-BY 4.0. |
| 146 | ASIC Regulatory Guides | https://www.asic.gov.au | T3 (CC-BY 4.0) | HTML + PDF | 300+ guides | Financial services licensing, credit, disclosure, digital assets. Confirmed CC-BY 4.0. |
| 147 | AER Guidelines | https://www.aer.gov.au | T6 (AU Govt, verify) | HTML + PDF | Varies | Electricity and gas network operations, pricing, retailer obligations. |
| 148 | Australian Digital Service Standard | https://www.digital.gov.au | T6 (AU Govt, verify) | HTML | Varies | 13 criteria for designing digital government services. |

### New Zealand

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 149 | NZ Treasury | https://www.treasury.govt.nz | T3 (CC-BY 4.0) | HTML + PDF | 100+ papers | Fiscal policy, economic frameworks, public sector financial management. Confirmed CC-BY 4.0. |
| 150 | RBNZ | https://www.rbnz.govt.nz | T7 (verify) | HTML + PDF | Varies | Prudential standards, monetary policy, financial stability reports. |

### India

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 151 | RBI Master Circulars | https://www.rbi.org.in | T7 (verify) | HTML + PDF | 100+ circulars | Consolidated instructions on banking topics -- KYC/AML, payments, forex, digital banking. |
| 152 | SEBI Regulations | https://www.sebi.gov.in | T7 (verify) | HTML + PDF | Varies | Mutual funds, stock exchange rules, listing obligations, AIF regulations. |

### Japan

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 153 | FSA Japan | https://www.fsa.go.jp/en/laws_regulations/index.html | T7 (verify) | HTML + PDF | Varies | Supervision guidelines for banks, insurance, securities. English translations available. |
| 154 | METI (including AI Governance) | https://www.meti.go.jp/english/ | T7 (verify) | HTML + PDF | Varies | Industrial standards, energy policy, AI governance guidelines. |

### Singapore

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 155 | MAS Regulations | https://www.mas.gov.sg | T7 (verify) | HTML + PDF | Varies | Banking, insurance, AML/CFT, technology risk management (TRM) guidelines. |

### Malaysia

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 156 | BNM Open Finance | https://www.bnm.gov.my | T7 (verify) | PDF | Varies | Open finance regulatory framework -- API architecture, consent management, phased implementation. |

### Indonesia

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 157 | OJK Regulations | https://ojk.go.id/en/regulasi/ | T7 (verify) | HTML + PDF | 100+ regs | Integrated financial regulator -- banking, capital markets, insurance. |

### APAC-Wide Multilateral

| # | Name | URL | Licence | Format | Volume | Description |
|---|------|-----|---------|--------|--------|-------------|
| 158 | APG Mutual Evaluations | https://www.apgml.org | T7 (verify) | PDF | Varies | AML/CFT assessments for each APAC jurisdiction. |
| 159 | BIS Basel Papers | https://www.bis.org | T7 (verify) | PDF | Varies | Basel III, FRTB, financial stability analysis. Global banking standards origin. |
| 160 | ADB Operations Manual | https://www.adb.org | T7 (verify) | PDF | Varies | Complete operational policies -- safeguards, financial management, procurement. |

### Priority Acquisition -- Domain 20

| Priority | Source | Licence | Effort |
|:--------:|--------|---------|--------|
| 1 | APRA Prudential Standards | T3 (CC-BY 4.0) | Low |
| 2 | ASIC Regulatory Guides | T3 (CC-BY 4.0) | Low |
| 3 | NZ Treasury | T3 (CC-BY 4.0) | Low |
| 4 | RBI Master Circulars | T7 (verify) | Low |
| 5 | SEBI Regulations | T7 (verify) | Low |
| 6 | MAS Regulations | T7 (verify) | Low |
| 7 | FSA Japan | T7 (verify) | Low |

---

## Gap Analysis

What cannot be found under open licences.

### Professional Knowledge Gaps

| Domain | Gap | Why | Best Workaround |
|--------|-----|-----|-----------------|
| Sales methodology | SPIN, Challenger, Solution Selling | All proprietary (Gartner, Huthwaite, Richardson) | GitLab sales handbook (CC-BY-SA) |
| Marketing depth | Positioning, GTM, growth frameworks | Reforge, Maven, HubSpot Academy are paywalled | GitLab marketing handbook + PostHog growth |
| Pricing strategy | Dynamic pricing, revenue management | PROS, Vendavo, Simon-Kucher are all proprietary | GitLab pricing strategy sections |
| Agent evaluation | LLM evaluation frameworks | Emerging field, nothing stable under open licence | OpenAI Cookbook + DAIR.AI |
| Leadership methodology | Radical Candor, SBI, coaching frameworks | Commercially published | Manager READMEs + GitLab people handbook |
| Economics frameworks | Porter, Blue Ocean, BCG matrix | Proprietary/academic | Business Model Canvas (CC0) |
| PKM methodology | BASB, How to Take Smart Notes | Commercially published books | Zettelkasten.de + Matuschak notes + Learning Scientists |

### Life & Development Gaps

| Domain | Gap | Why | Best Workaround |
|--------|-----|-----|-----------------|
| Practical nutrition | Meal planning, dietary frameworks | Proprietary apps dominate | Open Food Facts (data) + USDA guidelines (science) |
| CBT workbooks | Structured therapeutic exercises | Clinical publishers hold rights | 29ki app content (AGPL, needs extraction) |
| NVC communication | Full Rosenberg methodology | Book-based (estate controls) | GitHub transcripts (unlicensed, reference only) |
| Montessori/unschooling guides | Structured implementation guides | Scattered across commercial materials | 1912 Montessori Method (PD) + Reggio Emilia |
| Gottman relationships | Four Horsemen, Sound Relationship House | Proprietary clinical training | Purdue Extension summaries + Harvard Center |
| Gifted/2e children | Twice-exceptional educational planning | Very thin open collection | Neurodiversity Journal covers 2e research |
| ADHD lived experience | Coaching protocols, accommodation guides | ADDitude Magazine is subscription-based | CHADD + ASAN + Neurodiversity Journal |
| Golf instruction | Coaching methodology | No federation publishes open-licensed instruction | R&A, USGA publications (verify per item) |
| Running/cycling periodisation | Training programme design | TrainingPeaks, Joe Friel -- no explicit open licence | Use for personal reference only |
| TCM / Ayurveda practical guides | Clinical practice overviews | GitHub repos are pharmacological databases, not philosophy | Sacred Texts Archive + Charaka Samhita (PD) |

### Industry & Regulatory Gaps

| Domain | Gap | Why | Best Workaround |
|--------|-----|-----|-----------------|
| Retail/CPG operations | ARTS data model, category management | OMG proprietary | GS1 standards (partially free) |
| Telecom operations | eTOM process framework | TM Forum membership required | ITU-T M.3050 summary + Wikipedia (CC-BY-SA) |
| Manufacturing operations | ISA-95, MES methodology | ISA commercial distribution | Wikipedia ISA-95 article (CC-BY-SA) |
| Utilities CIM | IEC 61968/61970 | IEC commercial distribution | EPRI CIM Primer (free access) |
| Supply chain operations | SCOR model | ASCM membership required | No open equivalent at practitioner depth |
| Pharmaceutical regulatory | TGA, PMDA, CDSCO methodology | No open practitioner collection | Individual regulator websites |
| Insurance operations | Claims adjudication, underwriting | No open methodology collection | APRA covers Australian standards only |

---

## Proprietary & Commercial Equivalents

Where open collections do not exist, these are the proprietary/commercial sources that cover the gap. Listed by name and engagement model only -- no URLs to paywalled content.

### Cross-Industry Frameworks

| Source | Coverage | Engagement Model |
|--------|----------|------------------|
| APQC Process Classification Framework | Cross-industry business process taxonomy (65+ categories) | Membership ($5,000+/year) |
| Gartner Research | Industry analysis, magic quadrants, hype cycles | Subscription (~$30K+/year) |
| Forrester Research | CX, digital transformation, technology vendor analysis | Subscription |
| IDC | Market sizing, technology investment trends | Subscription |
| McKinsey Global Institute | Strategic management research (some papers free) | Engagement or MGI papers |
| BCG Henderson Institute | Business model innovation, transformation (some papers free) | Engagement or BCG papers |
| Harvard Business Review | Management practice, leadership, strategy | $99-$299/year digital |

### Innovation & Pricing

| Source | Coverage | Engagement Model |
|--------|----------|------------------|
| Stage-Gate International | Product development governance methodology | Licensing + consulting |
| PROS Pricing | AI-driven dynamic pricing methodology | Software licensing |
| Simon-Kucher & Partners | Pricing strategy consulting | Consulting engagement |
| Vendavo | B2B margin and price management | Software licensing |

### Sales & Customer Experience

| Source | Coverage | Engagement Model |
|--------|----------|------------------|
| Miller Heiman / Korn Ferry | Strategic Selling, LAMP, Conceptual Selling | Training licence + certification |
| SPIN Selling (Huthwaite) | Situation-Problem-Implication-Need framework | Books + training |
| The Challenger Sale (Gartner) | Teach, tailor, take control methodology | Licensing + training |
| Bain & Company -- NPS | Net Promoter Score methodology | NPS methodology freely described; advisory engagement |
| Forrester CX Index | Annual CX quality measurement | Subscription |

### Supply Chain & Operations

| Source | Coverage | Engagement Model |
|--------|----------|------------------|
| SCOR / ASCM | Supply chain process standard (Plan, Source, Make, Deliver, Return) | Membership + standard purchase |
| Six Sigma (ASQ/IASSC) | DMAIC process improvement methodology | Certification |
| Shingo Institute | Manufacturing excellence based on Toyota principles | Workshops + assessment |

### Technology & Governance

| Source | Coverage | Engagement Model |
|--------|----------|------------------|
| TOGAF (The Open Group) | Enterprise architecture methodology | $195 standard + certification |
| COBIT (ISACA) | IT governance and management framework | Membership + framework purchase |
| ITIL 4 (Axelos/PeopleCert) | IT service management (34 management practices) | Publication purchase + certification |
| ISO 27001 | Information security management | Standard purchase + certification |
| DAMA DMBOK | Data management body of knowledge | Book purchase |

### Risk & Compliance

| Source | Coverage | Engagement Model |
|--------|----------|------------------|
| COSO ERM Framework | Enterprise risk management (freely readable, not CC) | Membership + guidance documents |
| ISO 31000 | International risk management standard | Standard purchase |
| ISO 9001 | Quality management systems | Standard purchase + certification |

### Industry-Specific

| Source | Coverage | Engagement Model |
|--------|----------|------------------|
| TM Forum / eTOM | Telecom business process framework | Membership |
| GS1 Standards | Retail product identification (barcodes, RFID, data standards) | Membership |
| HIMSS | Healthcare digital adoption, EMRAM model | Membership |
| JCI | Hospital accreditation standards | Accreditation + standards purchase |
| SWIFT / ISO 20022 | Financial messaging standards | Membership/access |
| IEC 61968/61970 | Utilities Common Information Model | Standard purchase |

### Consulting Firm Knowledge Bases

| Firm | Most Relevant Methodology | Access |
|------|---------------------------|--------|
| McKinsey | Business transformation, operating model design | Engagement (MGI papers partially free) |
| BCG | Business model innovation, digital transformation | Engagement (Henderson Institute partially free) |
| Bain | Operating model, NPS/loyalty, operational improvement | Engagement |
| Deloitte | Finance transformation, HR transformation, risk | Engagement |
| Accenture | Technology-led transformation, industry playbooks | Engagement |
| EY | Risk, regulatory, finance transformation | Engagement |
| KPMG | Risk management, financial services, tax transformation | Engagement |
| PwC | Risk, tax, workforce transformation | Engagement |
| Thoughtworks | Technology modernisation, agile at scale (select publications free) | Engagement |

---

## How to Use This Directory

### Building a Knowledge Base from Scratch

1. **Start with T1-T3 sources.** These have zero or minimal licence friction. You can redistribute, modify, and build on them freely.
2. **Prioritise native markdown sources.** Repositories on GitHub that are already in markdown require no conversion and are ready for immediate indexing.
3. **Clone, don't scrape.** Where a GitHub repository exists, use `git clone` rather than web scraping. You get the full history and can pin to a specific commit for reproducibility.
4. **Verify licences at source.** This directory documents what was found at the time of research. Licences can change. Always check the `LICENSE` file in the repository before using a collection.

### Licence Verification Checklist

Before ingesting any source marked with a caution flag:

- [ ] Check the repo `LICENSE` file or `LICENSE.md` -- do not rely on README claims
- [ ] For CC variants, confirm: BY only / SA / NC / ND
- [ ] CC-BY-SA: confirm whether your use constitutes a "derivative work" requiring SA carry-through
- [ ] "Public repo, no license" = copyright reserved by default -- do not redistribute without explicit permission
- [ ] Log confirmed licence, version, and date checked in your catalogue

### Suggested Collection Structure

```
reference-library/
  agentic-ai/              # OpenAI Cookbook, DAIR.AI, Panaversity, System Prompts
  engineering/              # ADRs, MADR, SOC docs, 12-Factor, 18F, AOSA
  data-and-analysis/        # dbt, MLOps, PostHog analytics, EconML/CausalML
  product-and-design/       # Gong, PostHog, USDS playbook
  leadership/               # Awesome Open Company, Manager READMEs
  operating-models/         # CNCF platform model, Ways of Working
  personal-effectiveness/   # OKRs, spaced repetition, Zettelkasten
  health-and-fitness/       # Exercise DBs, QS, biomarkers, circadian
  eastern-philosophy/       # Core canon (Tao Te Ching, Art of War, Dhammapada)
  family-and-development/   # OpenStax Lifespan, CDC milestones, UNICEF
  mental-wellbeing/         # Awesome Mental Health, mindfulness, psych safety
  industry-standards/       # BIAN APIs, HL7 FHIR, MOSIP
  regulatory/               # APRA, ASIC, NZ Treasury, RBI, SEBI
  CATALOGUE.md              # Licence + provenance for every ingested source
```

### Summary Statistics

| Category | Sources | T1-T3 (freely usable) | T4 (share-alike) | T5-T8 (restricted/reference) |
|----------|:-------:|:---------------------:|:-----------------:|:----------------------------:|
| Professional Knowledge (Domains 1-12) | 58 | 38 | 5 | 15 |
| Life & Human Development (Domains 13-18) | 72 | 32 | 5 | 35 |
| Industry & Regulatory (Domains 19-20) | 30 | 9 | 0 | 21 |
| **Total** | **160** | **79** | **10** | **71** |

---

## Research Provenance

This directory was compiled from:

- 300+ web search queries via deep research API across six research runs
- Parallel GitHub repository verification for all code-hosted sources
- Licence claims verified against primary source URLs (not assumed)

All repository activity and licensing can change. Verify before use. This document is a point-in-time snapshot.

---

*This document is released under CC0 (Public Domain). No attribution required. Use it however you like.*
