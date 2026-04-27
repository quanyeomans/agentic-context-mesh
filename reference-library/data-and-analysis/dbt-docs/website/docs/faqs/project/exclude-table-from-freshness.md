---
title: "How do I exclude a table from a freshness snapshot?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

Some tables in a data source may be updated infrequently. If you've set a `freshness` property at the source level, this <Term id="table" /> is likely to fail checks.

To work around this, you can set the table's freshness to null (`freshness: null`) to "unset" the freshness for a particular table:

<File name='models/<filename>.yml'>

```yaml
sources:
  - name: jaffle_shop
    database: raw
    schema: jaffle_shop
    config: 
      freshness:
        warn_after: {count: 12, period: hour}
        error_after: {count: 24, period: hour}
      loaded_at_field: _etl_loaded_at

    tables:
      - name: orders
      - name: product_skus
        config:
          freshness: null # do not check freshness for this table
```

</File>
