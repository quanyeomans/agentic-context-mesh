---
title: "Can I store my models in a directory other than the `models` directory in my project?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

By default, dbt expects the files defining your models to be located in the `models` subdirectory of your project.

To change this, update the [model-paths](/reference/project-configs/model-paths) configuration in your `dbt_project.yml`
file, like so:

<File name='dbt_project.yml'>

```yml
model-paths: ["transformations"]
```

</File>
