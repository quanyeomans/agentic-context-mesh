---
title: "Dbt Connection Support Fusion"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

| Integration   | User credentials/token| Service account credentials | Warehouse OAuth for users | External OAuth for users | Service-to-service OAuth | Key/Pair | MFA | SSH | Private connectivity support** |
| ------------- |:---------------------:|:---------------------------:|:-------------------------:|:------------------------:|:------------------------:|:--------:|:---:|:---:|:------------------------------:|
| Snowflake     | ✅                    | ✅                          | ✅                         | ✅                       | ❌                        |    ✅    |  ✅ | ❌  | ✅              |
| BigQuery      | ✅                    | ✅                          | ✅                         | ✅                       | ❌                        |    ❌    |  ❌ | ❌  | ✅              |
| Databricks    | ✅                    | ✅                          | ✅                         | ❌                       | ❌                        |    ❌    |  ❌ | ❌  | ✅              |    
| Redshift      | ✅                    | ❌                          | ❌                         | ❌                       | ❌                        |    ❌    |  ❌ | ❌  | ✅              |

** Private connectivity is only supported for certain cloud providers and deployment types. See [Private connectivity documentation](/docs/cloud/secure/private-connectivity/private-connectivity) for details.
