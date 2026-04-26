---
title: "about this"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

`this` is the database representation of the current model. It is useful when:
- Defining a `where` statement within [incremental models](/docs/build/incremental-models)
- Using [pre or post hooks](/reference/resource-configs/pre-hook-post-hook)

`this` is a [Relation](/reference/dbt-classes#relation), and as such, properties such as `{{ this.database }}` and `{{ this.schema }}` compile as expected. 
  - Note &mdash; Prior to dbt v1.6, <Constant name="clou_ided" /> returns `request` as the result of `{{ ref.identifier }}`.

`this` can be thought of as equivalent to `ref('<the_current_model>')`, and is a neat way to avoid circular dependencies.

## Examples

### Configuring incremental models

<File name='models/stg_events.sql'>

```sql
{{ config(materialized='incremental') }}

select
    *,
    my_slow_function(my_column)

from raw_app_data.events

{% if is_incremental() %}
  where event_time > (select max(event_time) from {{ this }})
{% endif %}
```

</File>
