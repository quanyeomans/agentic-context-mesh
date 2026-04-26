---
title: "How often should I run the snapshot command?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

Snapshots are a batch-based approach to [change data capture](https://en.wikipedia.org/wiki/Change_data_capture). The `dbt snapshot` command must be run on a schedule to ensure that changes to tables are actually recorded! While individual use-cases may vary, snapshots are intended to be run between hourly and daily. If you find yourself snapshotting more frequently than that, consider if there isn't a more appropriate way to capture changes in your source data tables.
