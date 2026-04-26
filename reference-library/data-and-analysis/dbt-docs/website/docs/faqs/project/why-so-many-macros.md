---
title: "Why does my dbt output have so many macros in it?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

The output of a dbt run counts over 100 macros in your project!

```shell
$ dbt run
Running with dbt=1.7.0
Found 1 model, 0 tests, 0 snapshots, 0 analyses, 138 macros, 0 operations, 0 seed files, 0 sources
```

This is because dbt ships with its own project, which also includes macros! You can learn more about this [here](https://discourse.getdbt.com/t/did-you-know-dbt-ships-with-its-own-project/764).
