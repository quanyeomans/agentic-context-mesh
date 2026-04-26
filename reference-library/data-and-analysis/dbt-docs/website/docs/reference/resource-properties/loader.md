---
title: "Definition"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<File name='models/<filename>.yml'>

```yml

sources:
  - name: <source_name>
    database: <database_name>
    loader: <string>
    tables:
      - ...

```

</File>

## Definition
Describe the tool that loads this source into your warehouse. Note that this property is for documentation purposes only — dbt does not meaningfully use this.

## Examples
### Indicate which EL tool loaded data

<File name='models/<filename>.yml'>

```yml

sources:
  - name: jaffle_shop
    loader: fivetran
    tables:
      - name: orders
      - name: customers

  - name: stripe
    loader: stitch
    tables:
      - name: payments
```

</File>
