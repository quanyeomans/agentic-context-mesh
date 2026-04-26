---
title: "Bigquery Permissions"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

dbt user accounts need the following permissions to read from and create tables and <Term id="view">views</Term> in a BigQuery project:
- BigQuery Data Editor
- BigQuery User

For BigQuery with <Constant name="fusion_engine" />, users also need:
- BigQuery Read Session User (for Storage Read API access)

To use the [Query History](/docs/explore/model-query-history#bigquery-model-query-history) feature, add:
- BigQuery Resource Viewer

For BigQuery DataFrames, users need these additional permissions:
- BigQuery Job User
- BigQuery Read Session User
- Notebook Runtime User
- Code Creator
- colabEnterpriseUser
