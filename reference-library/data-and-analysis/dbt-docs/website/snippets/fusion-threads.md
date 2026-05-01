---
title: "Fusion Threads"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

| Adapter | Behavior |
|---------|----------|
| **Snowflake** | Fusion ignores user-set threads and automatically optimizes parallelism for maximum performance.  The only supported override is `threads: 1`, which can also help resolve timeout issues if set. 
| **Databricks** | Fusion ignores user-set threads and automatically optimizes parallelism for maximum performance.  The only supported override is `threads: 1`, which can also help resolve timeout issues if set. |
| **BigQuery** | Fusion respects user-set threads to manage API rate limits.  Setting `--threads 0` (or omitting the setting) allows <Constant name="fusion"/> to dynamically optimize parallelism. |
| **Redshift** | Fusion respects user-set threads to manage concurrency limits. Setting `--threads 0` (or omitting the setting) allows <Constant name="fusion"/> to dynamically optimize parallelism. |
