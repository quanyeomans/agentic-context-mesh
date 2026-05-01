---
title: "Compare Changes Table"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<SimpleTable>

| Aspect | In development (compare changes) | In deployment (Advanced CI) |
|---|---|---|
| **Affects** | Development for one modified model at a time | Deployment for all modified models in a project |
| **Trigger** | On-demand in editor | PR open/update and CI job |
| **Scope** | Your working copy and local target | Branch head versus prod state in CI |
| **Output** location | Compare panel in VS Code/Cursor. Does not create a PR comment in Git provider | Deployment job compare tab and PR summary comment in Git provider |
| **Data caching** | Editor-side | dbt platform [caches](/docs/deploy/advanced-ci#about-the-cached-data) limited samples |
| **Governance** | Local development credentials | Production credentials |

</SimpleTable>
