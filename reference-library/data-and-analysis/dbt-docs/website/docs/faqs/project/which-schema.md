---
title: "How did dbt choose which schema to build my models in?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

By default, dbt builds models in your target schema. To change your target schema:
* If you're developing in **<Constant name="dbt" />**, these are set for each user when you first use a development environment.
* If you're developing with **dbt Core**, this is the `schema:` parameter in your `profiles.yml` file.

If you wish to split your models across multiple schemas, check out the docs on [using custom schemas](/docs/build/custom-schemas).

Note: on BigQuery, `dataset` is used interchangeably with `schema`.
