---
title: "batch_size"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<VersionCallout version="1.9" />

## Definition

The `batch_size` config determines how large batches are when running a [microbatch incremental model](/docs/build/incremental-microbatch). Accepted values are `hour`, `day`, `month`, or `year`. You can configure `batch_size` for a [model](/docs/build/models) in your project YAML file (`dbt_project.yml`), properties YAML file, or config block.

## Examples

The following examples set `day` as the `batch_size` for the `user_sessions` model.

Example of the `batch_size` config in the `dbt_project.yml` file:

<File name='dbt_project.yml'>

```yml
models:
  my_project:
    user_sessions:
      +batch_size: day
```
</File>

Example in a property file:

<File name='models/properties.yml'>

```yml
models:
  - name: user_sessions
    config:
      batch_size: day
```

</File>

Example in a config block for a model:

<File name="models/user_sessions.sql">

```sql
{{ config(
    materialized='incremental',
    batch_size='day'
) }}
```

</File>
