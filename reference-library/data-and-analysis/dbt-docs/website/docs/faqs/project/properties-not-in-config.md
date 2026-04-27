---
title: "Can I add tests and descriptions in a SQL config block?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

dbt has the ability to define node configs in YAML files, in addition to `config()` blocks and `dbt_project.yml`. But the reverse isn't always true: there are some things in `.yml` files that can _only_ be defined there.

Certain properties are special, because:
- They have a unique Jinja rendering context
- They create new project resources
- They don't make sense as hierarchical configuration
- They're older properties that haven't yet been redefined as configs

These properties are:
- [`description`](/reference/resource-properties/description)
- [`tests`](/reference/resource-properties/data-tests)
- [`docs`](/reference/resource-configs/docs)
- `columns`
- [`quote`](/reference/resource-properties/columns#quote)
- [`source` properties](/reference/source-properties) (e.g. `loaded_at_field`, `freshness`)
- [`exposure` properties](/reference/exposure-properties) (e.g. `type`, `maturity`)
- [`macro` properties](/reference/resource-properties/arguments) (e.g. `arguments`)
