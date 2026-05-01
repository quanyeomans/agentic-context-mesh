---
title: "How do I test one model at a time?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

Running tests on one model looks very similar to running a model: use the `--select` flag (or `-s` flag), followed by the name of the model:

```shell
dbt test --select customers
```

Check out the [model selection syntax documentation](/reference/node-selection/syntax) for full syntax, and [test selection examples](/reference/node-selection/test-selection-examples) in particular.
