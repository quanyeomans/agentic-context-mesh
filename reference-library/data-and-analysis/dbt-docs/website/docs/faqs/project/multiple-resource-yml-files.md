---
title: "Should I use separate files to declare resource properties, or one large file?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

It's up to you:
- Some folks find it useful to have one file per model (or source / snapshot / seed etc)
- Some find it useful to have one per directory, documenting and testing multiple models in one file

Choose what works for your team. We have more recommendations in our guide on [structuring dbt projects](/best-practices/how-we-structure/1-guide-overview).
