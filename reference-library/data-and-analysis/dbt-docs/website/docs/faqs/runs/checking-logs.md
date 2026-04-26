---
title: "How can I see the SQL that dbt is running?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

To check out the SQL that dbt is running, you can look in:

* <Constant name="dbt" />:
  * Within the run output, click on a model name, and then select "Details"
* <Constant name="core" />:
  * The `target/compiled/` directory for compiled `select` statements
  * The `target/run/` directory for compiled `create` statements
  * The `logs/dbt.log` file for verbose logging.
