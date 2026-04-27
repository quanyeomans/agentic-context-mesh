---
title: "Can I build my seeds in a schema other than my target schema or can I split my seeds across multiple schemas?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

Yes! Use the [schema](/reference/resource-configs/schema) configuration in your `dbt_project.yml` file.

<File name='dbt_project.yml'>

```yml
name: jaffle_shop
...

seeds:
  jaffle_shop:
    +schema: mappings # all seeds in this project will use the schema "mappings" by default
    marketing:
      +schema: marketing # seeds in the "seeds/marketing/" subdirectory will use the schema "marketing"
```

</File>
