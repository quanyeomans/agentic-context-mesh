---
title: "What if my source is in a poorly named schema or table?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

By default, dbt will use the `name:` parameters to construct the source reference.

If these names are a little less-than-perfect, use the [schema](/reference/resource-properties/schema) and [identifier](/reference/resource-properties/identifier) properties to define the names as per the database, and use your `name:` property for the name that makes sense!

<File name='models/<filename>.yml'>

```yml
sources:
  - name: jaffle_shop
    database: raw
    schema: postgres_backend_public_schema
    tables:
      - name: orders
        identifier: api_orders
```

</File>


In a downstream model:
```sql
select * from {{ source('jaffle_shop', 'orders') }}
```

Will get compiled to:
```sql
select * from raw.postgres_backend_public_schema.api_orders
```
