---
title: "Snowflake adapter behavior changes"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

## The `snowflake_default_transient_dynamic_tables` flag

Available starting `dbt-snowflake` v1.12. The `snowflake_default_transient_dynamic_tables` flag controls whether Snowflake dynamic tables are created as transient when the model config does not explicitly set the [`transient`](/reference/resource-configs/snowflake-configs#transient-dynamic-tables) config.

- When set to `False` (default): Dynamic tables are created as permanent tables with a [Fail-safe period](https://docs.snowflake.com/en/user-guide/data-failsafe) unless you set `transient: true` for a specific model.
- When set to `True`: Dynamic tables are created as transient (no Fail-safe period) when `transient` is not specified in the model config. Transient dynamic tables can reduce storage costs.

Set the `snowflake_default_transient_dynamic_tables` flag in your `dbt_project.yml` under the `flags` key. You can override the default setting using the [`transient`](/reference/resource-configs/snowflake-configs#transient-dynamic-tables) config on dynamic table models.
