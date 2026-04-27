---
title: " About doc function"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

The `doc` function is used to reference docs blocks in the description field of schema.yml files. It is analogous to the `ref` function. For more information, consult the [Documentation guide](/docs/explore/build-and-view-your-docs).

Usage:

<File name='orders.md'>

```jinja2

{% docs orders %}

# docs
- go
- here
 
{% enddocs %}
```

</File>


<File name='schema.yml'>

```yaml

models:
  - name: orders
    description: "{{ doc('orders') }}"
```

</File>
