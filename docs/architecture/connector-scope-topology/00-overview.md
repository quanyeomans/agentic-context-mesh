# Connector + collection + scope topology — analysis package

A working design package for the layered architecture proposed in the
2026-05-23 IM-6 conversation: decouple connector / source path /
collection / scope profile / search strategy. Lands as an ADR once the
analysis converges; until then this is **active design in progress**.

## Why this exists

The current connector framework hardwires `name = entry-point key = SQL
collection = cursor scope = source identity`. That works for one credential
covering one logical store (obsidian-style). It does not model:

- Tenant-credential connectors with many internal containers
  (SharePoint, Notion, M365, Slack, GitHub).
- Aggregation: multiple sources contributing to one retrieval bucket
  (e.g. "client-x-engagement" pulling from obsidian/sharepoint/dex).
- Decomposition: one connector / credential split into multiple
  collections (e.g. one SharePoint tenant → per-site collections).
- Per-agent or per-skill scope profiles that bundle collections.
- Cross-source queries driven by skill or task, not by connector name.

The design needs to balance:
1. Source-side reality (each plugin's auth / hierarchy / freshness model).
2. Operator-config simplicity (mental model an operator can hold).
3. Use-case effectiveness (memory tiers, team scoping, cross-source SoW).
4. Non-functionals (storage, freshness, latency, document conversion cost).

## Package contents

| File | Purpose |
|---|---|
| `00-overview.md` | This file — nav + scope of the analysis. |
| `01-source-analysis.md` | Per-connector auth / scope / hierarchy / change-detection / sensitivity / freshness / storage profile. Identifies external-research gaps. |
| `02-use-cases.md` | Memory tiers, retrieval patterns, graph-relationship modalities, cross-source query shapes. |
| `03-bdd-scenarios.md` | Per-connector + per-use-case Gherkin scenarios that pin the topology decisions. New `tests/bdd/features/connector_*.feature` rows + `use_case_*.feature` files; gaps for connectors not yet implemented. |
| `04-simulation.md` | Mental simulations of the layered model against each connector + use case. Identifies where the model breaks. |
| `05-non-functionals.md` | Storage growth, indexing latency, freshness SLOs, document-conversion cost, rate-limit + quota budgets per source. |
| `06-onyx-comparative-analysis.md` | Onyx (open-source enterprise search, ~50 connectors) framework patterns mapped against our 5-layer model — adopt / reject / adapt. |
| `07-research-closeout.md` | Closes the per-connector open research items 01-source-analysis flagged (Dex webhooks, M365 body shape, Notion teamspace policy, GitHub scope semantics, M365 calendar sensitivity). |
| `ADR.md` | Canonical architectural decision once analysis converges. |

## Layers (proposed; refined throughout)

| Layer | Identity | Responsibility |
|---|---|---|
| **Connector instance** | `obsidian-personal`, `sharepoint-corp-tenant` | One credential boundary. Enumerates everything reachable. |
| **Source path** | `vault:personal/01-Projects/file.md`, `site:client-x/library/folder/file.docx` | Hierarchical address inside one connector instance. Framework treats it as opaque string; per-kind parsers exist if needed. |
| **Collection** | `client-x-engagement`, `team-shared-memory`, `agent-builder-private` | Retrieval bucket — hybrid search ranks within / over these. Decoupled from connector. |
| **Collection mapping** | `(connector_instance, source_path_filter) → collection` | Many-to-one (aggregation) AND one-to-many (decomposition) both valid shapes. |
| **Scope profile** | per-agent or per-team | Bundle of collections + read/write rights + retrieval weights. |
| **Search strategy** | per-skill or per-task | Runtime resolver: `{agent, skill, task} → ordered collection set + ranking strategy`. |

## Non-goals (kept narrow on purpose)

- **NOT redesigning the Bronze / Silver / chunk-writer layer.** Those are
  inside one connector run and stay as designed in
  `connector-ingestion-architecture.md`.
- **NOT redesigning the retrieval pipeline.** Hybrid BM25 + vector + RRF
  + intent + boosts stays; we change WHAT collections it scopes over,
  not HOW it ranks.
- **NOT redesigning Neo4j graph semantics.** Graph stays global per the
  identity-resolution model in `fact-layer.md`; access control happens
  at the chunk-back-reference layer.
- **NOT shipping third-party connector plugins.** Wave 5+ stays
  first-party only; this design constrains the topology so third-party
  plugins land cleanly later.

## Status

| Section | Status |
|---|---|
| `00-overview.md` | drafted 2026-05-23 |
| `01-source-analysis.md` | drafted 2026-05-23 (8 connector kinds; 3 closed via external research) |
| `02-use-cases.md` | drafted 2026-05-23 (14 use cases across 5 modalities) |
| `03-bdd-scenarios.md` | drafted 2026-05-23 (30+ Gherkin scenarios) |
| `04-simulation.md` | drafted 2026-05-23 (12 break points + resolutions) |
| `05-non-functionals.md` | drafted 2026-05-23 (storage / freshness / latency / cost per source) |
| `06-onyx-comparative-analysis.md` | in progress (Onyx framework patterns + gaps vs our 5-layer model) |
| `07-research-closeout.md` | in progress (closing Dex webhook + M365 body + Notion teamspace + GitHub scope + M365 calendar.sensitivity open questions) |
| `ADR.md` | drafted 2026-05-23 — **PROPOSED**, pending review (will revise after 06+07 land) |

External research gaps tracked in `01-source-analysis.md` §"Open
questions" per connector — being closed in `07-research-closeout.md`.
