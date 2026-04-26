---
title: "What data tests are available for me to use in dbt?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

Out of the box, dbt ships with the following data tests:

* `unique`
* `not_null`
* `accepted_values`
* `relationships` (for example, referential integrity)

You can also write your own [custom generic tests](/docs/build/data-tests#generic-data-tests).

Some additional generic tests have been open-sourced in the [dbt-utils package](https://github.com/dbt-labs/dbt-utils#generic-tests). Check out the docs on [packages](/docs/build/packages) to learn how to make these tests available in your project.
