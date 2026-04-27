---
title: "Deploy your metrics"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

# Deploy your metrics <Lifecycle status="self_service,managed,managed_plus" />


import RunProdJob from '/snippets/_sl-run-prod-job.md';

<RunProdJob/>

## Next steps
After you've executed a job and deployed your <Constant name="semantic_layer" />:
- [Set up your <Constant name="semantic_layer" />](/docs/use-dbt-semantic-layer/setup-sl) in <Constant name="dbt" />.
- Discover the [available integrations](/docs/cloud-integrations/avail-sl-integrations), such as Tableau, Google Sheets, Microsoft Excel, and more.
- Start querying your metrics with the [API query syntax](/docs/dbt-cloud-apis/sl-jdbc#querying-the-api-for-metric-metadata).


## Related docs
- [Optimize querying performance](/docs/use-dbt-semantic-layer/sl-cache) using declarative caching.
- [Validate semantic nodes in CI](/docs/deploy/ci-jobs#semantic-validations-in-ci) to ensure code changes made to dbt models don't break these metrics.
- If you haven't already, learn how to [build your metrics and semantic models](/docs/build/build-metrics-intro) in your development tool of choice.
