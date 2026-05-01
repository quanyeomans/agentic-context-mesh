---
title: "How do I run data tests on just my sources?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

To run data tests on all sources, use the following command:

```shell
  dbt test --select "source:*"
```

(You can also use the `-s` shorthand here instead of `--select`)

To run data tests on one source (and all of its tables):

```shell
$ dbt test --select source:jaffle_shop
```

And, to run data tests on one source <Term id="table" /> only:

```shell
$ dbt test --select source:jaffle_shop.orders
```
