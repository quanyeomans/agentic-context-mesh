---
title: "One of my tests failed, how can I debug it?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

To debug a failing test, find the SQL that dbt ran by:

* <Constant name="dbt" />:
  * Within the test output, click on the failed test, and then select "Details".

* <Constant name="core" />:
  * Open the file path returned as part of the error message.
  * Navigate to the `target/compiled/schema_tests` directory for all compiled test queries.

Copy the SQL into a query editor (in <Constant name="dbt" />, you can paste it into a new `Statement`), and run the query to find the records that failed.
