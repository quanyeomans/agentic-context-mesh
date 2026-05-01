---
title: "I need to use quotes to select from my source, what should I do?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

This is reasonably common on Snowflake in particular.

By default, dbt will not quote the database, schema, or identifier for the source tables that you've specified.

To force dbt to quote one of these values, use the [`quoting` property](/reference/resource-properties/quoting):

<File name='models/<filename>.yml'>

```yaml
sources:
  - name: jaffle_shop
    database: raw
    schema: jaffle_shop
    quoting:
      database: true
      schema: true
      identifier: true

    tables:
      - name: order_items
      - name: orders
        # This overrides the `jaffle_shop` quoting config
        quoting:
          identifier: false
```

</File>
