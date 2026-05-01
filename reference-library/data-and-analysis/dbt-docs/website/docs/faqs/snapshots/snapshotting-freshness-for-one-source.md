---
title: "How do I snapshot freshness for one source only?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

Use the `--select` flag to snapshot freshness for specific sources. Eg:

```shell
# Snapshot freshness for all Jaffle Shop tables:
$ dbt source freshness --select source:jaffle_shop

# Snapshot freshness for a particular source <Term id="table" />:
$ dbt source freshness --select source:jaffle_shop.orders

# Snapshot freshness for multiple particular source tables:
$ dbt source freshness --select source:jaffle_shop.orders source:jaffle_shop.customers
```

See the [`source freshness` command reference](/reference/commands/source) for more information.
