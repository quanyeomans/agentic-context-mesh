---
title: "lookback"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<VersionCallout version="1.9" />
## Definition

Configure a `lookback` window to reprocess additional batches during [microbatch incremental model](/docs/build/incremental-microbatch) runs. It processes X batches up to the latest bookmark (the last successfully processed data point) to capture late-arriving records.  

Set the `lookback` to an integer greater than or equal to zero. The default value is `1`.  You can configure `lookback` for a [microbatch incremental model](/docs/build/incremental-microbatch) in your project YAML file (`dbt_project.yml`), properties YAML file (`models/properties.yml`), or SQL file config.

## Examples

The following examples set `2` as the `lookback` config for the `user_sessions` model.

Example in the `dbt_project.yml` file:

<File name='dbt_project.yml'>

```yml
models:
  my_project:
    user_sessions:
      +lookback: 2
```
</File>

Example in a property file:

<File name='models/properties.yml'>

```yml
models:
  - name: user_sessions
    config:
      lookback: 2
```

</File>

Example in SQL config block:

<File name="models/user_sessions.sql">

```sql
{{ config(
    lookback=2
) }}
```

</File>
