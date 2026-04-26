---
title: "How do I test and document seeds?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

To test and document seeds, use a [properties file](/reference/configs-and-properties) and nest the configurations under a `seeds:` key

## Example

<File name='seeds/properties.yml'>

```yml
seeds:
  - name: country_codes
    description: A mapping of two letter country codes to country names
    columns:
      - name: country_code
        data_tests:
          - unique
          - not_null
      - name: country_name
        data_tests:
          - unique
          - not_null
```

</File>
