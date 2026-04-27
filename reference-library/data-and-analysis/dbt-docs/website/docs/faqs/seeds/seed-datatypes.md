---
title: "How do I set a datatype for a column in my seed?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

dbt will infer the datatype for each column based on the data in your CSV.

You can also explicitly set a datatype using the `column_types` [configuration](/reference/resource-configs/column_types) like so:

<File name='dbt_project.yml'>

```yml
seeds:
  jaffle_shop: # you must include the project name
    warehouse_locations:
      +column_types:
        zipcode: varchar(5)
```

</File>
