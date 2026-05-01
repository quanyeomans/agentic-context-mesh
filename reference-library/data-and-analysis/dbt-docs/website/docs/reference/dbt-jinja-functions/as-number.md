---
title: "About as_number filter"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

The `as_number` Jinja filter will coerce Jinja-compiled output into a numeric
value (integer or float), or return an error if it cannot be represented as
a number.

### Usage

In the example below, the `as_number` filter is used to coerce an environment
variables into a numeric value to dynamically control the connection port.

<File name='profiles.yml'>

```yml
my_profile:
  outputs:
    dev:
      type: postgres
      port: "{{ env_var('PGPORT') | as_number }}"
```

</File>
