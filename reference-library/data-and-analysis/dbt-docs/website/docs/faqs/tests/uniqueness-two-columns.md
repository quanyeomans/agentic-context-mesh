---
title: "Can I test the uniqueness of two columns?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

Yes, there's a few different options for testing the uniqueness of two columns.


Consider an orders <Term id="table" /> that contains records from multiple countries, and the combination of ID and country code is unique:

| order_id | country_code |
|----------|--------------|
| 1        | AU           |
| 2        | AU           |
| ...      | ...          |
| 1        | US           |
| 2        | US           |
| ...      | ...          |


Here are some approaches:

#### 1. Create a unique key in the model and test that

<File name='models/orders.sql'>

```sql

select
  country_code || '-' || order_id as surrogate_key,
  ...

```

</File>

<File name='models/orders.yml'>

```yml
models:
  - name: orders
    columns:
      - name: surrogate_key
        data_tests:
          - unique
```

</File>


#### 2. Test an expression

<File name='models/orders.yml'>

```yml
models:
  - name: orders
    data_tests:
      - unique:
          arguments: # available in v1.10.5 and higher. Older versions can set the <argument_name> as the top-level property.
            column_name: "(country_code || '-' || order_id)"
```

</File>


#### 3. Use the `dbt_utils.unique_combination_of_columns` test

This is especially useful for large datasets since it is more performant. Check out the docs on [packages](/docs/build/packages) for more information.

<File name='models/orders.yml'>

```yml
models:
  - name: orders
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments: # available in v1.10.5 and higher. Older versions can set the <argument_name> as the top-level property.
            combination_of_columns:
              - country_code
              - order_id
```

</File>
