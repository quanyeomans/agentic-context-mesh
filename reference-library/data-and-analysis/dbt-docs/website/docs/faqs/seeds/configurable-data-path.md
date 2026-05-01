---
title: "Can I store my seeds in a directory other than the `seeds` directory in my project?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

By default, dbt expects your seed files to be located in the `seeds` subdirectory
of your project.

To change this, update the [seed-paths](/reference/project-configs/seed-paths) configuration in your `dbt_project.yml`
file, like so:

<File name='dbt_project.yml'>

```yml
seed-paths: ["custom_seeds"]
```

</File>
