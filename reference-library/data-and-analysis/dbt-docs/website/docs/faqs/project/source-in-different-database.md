---
title: "What if my source is in a different database to my target database?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

Use the [`database` property](/reference/resource-properties/database) to define the database that the source is in.

<File name='models/<filename>.yml'>

```yml
sources:
  - name: jaffle_shop
    database: raw
    schema: jaffle_shop
    tables:
      - name: orders
      - name: customers
```

</File>
