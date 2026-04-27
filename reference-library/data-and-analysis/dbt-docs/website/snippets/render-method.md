---
title: "Render Method"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

#### The render method

The `.render()` method is generally used to resolve or evaluate Jinja expressions (such as `{{ source(...) }}`) during runtime. 

When using the `--empty flag`, dbt may skip processing `ref()` or `source()` for optimization. To avoid compilation errors and to explicitly tell dbt to process a specific relation (`ref()` or `source()`), use the `.render()` method in your model file. For example:


<File name='models.sql'>

```Jinja
{{ config(
    pre_hook = [
        "alter external table {{ source('sys', 'customers').render() }} refresh"
    ]
) }}

select ...
```

</File>
