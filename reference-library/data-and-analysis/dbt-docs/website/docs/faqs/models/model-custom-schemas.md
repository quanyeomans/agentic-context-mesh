---
title: "Can I build my models in a schema other than my target schema or split my models across multiple schemas?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

Yes! Use the [schema](/reference/resource-configs/schema) configuration in your `dbt_project.yml` file, or using a `config` block:

<File name='dbt_project.yml'>

```yml
name: jaffle_shop
...

models:
  jaffle_shop:
    marketing:
      +schema: marketing # models in the `models/marketing/` subdirectory will use the marketing schema
```

</File>

<File name='models/customers.sql'>

```sql
{{
  config(
    schema='core'
  )
}}
```

</File>
