---
title: "Can I set test failure thresholds?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

You can use the `error_if` and `warn_if` configs to set custom failure thresholds in your tests. For more details, see [reference](/reference/resource-configs/severity) for more information.

You can also try the following solutions:

* Setting the [severity](/reference/resource-configs/severity) to `warn` or `error`
* Writing a [custom generic test](/best-practices/writing-custom-generic-tests) that accepts a threshold argument ([example](https://discourse.getdbt.com/t/creating-an-error-threshold-for-schema-tests/966))
