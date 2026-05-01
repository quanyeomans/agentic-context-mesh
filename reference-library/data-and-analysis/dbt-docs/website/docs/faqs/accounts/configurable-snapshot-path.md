---
title: "Can I store my snapshots in a directory other than the `snapshot` directory in my project?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

By default, dbt expects your snapshot files to be located in the `snapshots` subdirectory of your project.

To change this, update the [snapshot-paths](/reference/project-configs/snapshot-paths) configuration in your `dbt_project.yml`
file, like so:

<File name='dbt_project.yml'>

```yml
snapshot-paths: ["snapshots"]
```

</File>

Note that you cannot co-locate snapshots and models in the same directory.
