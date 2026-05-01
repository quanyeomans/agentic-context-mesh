---
title: "Advanced configuration usage"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

## Alternative SQL file config syntax

Some configurations may contain characters (e.g. dashes) that cannot be parsed as a Jinja argument. For example, the following would return an error:

```sql
{{ config(
    post-hook="grant select on {{ this }} to role reporter",
    materialized='table'
) }}

select ...
```

While dbt provides an alias for any core configurations (for example, you should use `pre_hook` instead of `pre-hook` in a config block), your dbt project may contain custom configurations without aliases.

If you want to specify these configurations inside of a model, use the alternative config block syntax:


<File name='models/events/base/base_events.sql'>

```sql
{{
  config({
    "post-hook": "grant select on {{ this }} to role reporter",
    "materialized": "table"
  })
}}


select ...
```

</File>
