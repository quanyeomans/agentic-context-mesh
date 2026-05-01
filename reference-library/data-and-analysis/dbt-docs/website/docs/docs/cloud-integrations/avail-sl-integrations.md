---
title: "Available integrations"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

# Available integrations <Lifecycle status="self_service,managed,managed_plus" />

There are a number of data applications that seamlessly integrate with the <Constant name="semantic_layer" />, powered by MetricFlow, from business intelligence tools to notebooks, spreadsheets, data catalogs, and more. These integrations allow you to query and unlock valuable insights from your data ecosystem.

Use the [<Constant name="semantic_layer" /> APIs](/docs/dbt-cloud-apis/sl-api-overview) to simplify metric queries, optimize your development workflow, and reduce coding. This approach also ensures data governance and consistency for data consumers.

import AvailIntegrations from '/snippets/_sl-partner-links.md';

<AvailIntegrations/>

### Custom integration

- All BI tools can use [exports](/docs/use-dbt-semantic-layer/exports) with the <Constant name="semantic_layer" />, even if they don’t have a native integration.
- [Consume metrics](/docs/use-dbt-semantic-layer/consume-metrics) and develop custom integrations using different languages and tools, supported through [JDBC](/docs/dbt-cloud-apis/sl-jdbc), ADBC, and [GraphQL](/docs/dbt-cloud-apis/sl-graphql) APIs, and [Python SDK library](/docs/dbt-cloud-apis/sl-python). For more info, check out [our examples on GitHub](https://github.com/dbt-labs/example-semantic-layer-clients/).
- Connect to any tool that supports SQL queries. These tools must meet one of the two criteria:
    - Offers a generic JDBC driver option (such as DataGrip) or
    - Is compatible Arrow Flight SQL JDBC driver version 12.0.0 or higher.

## Related docs

- [{frontMatter.meta.api_name}](https://docs.getdbt.com/docs/dbt-cloud-apis/sl-api-overview) to learn how to integrate and query your metrics in downstream tools.
- [<Constant name="semantic_layer" /> API query syntax](/docs/dbt-cloud-apis/sl-jdbc#querying-the-api-for-metric-metadata) 
- [Hex <Constant name="semantic_layer" /> cells](https://learn.hex.tech/docs/explore-data/cells/data-cells/dbt-metrics-cells) to set up SQL cells in Hex.
- [Resolve 'Failed APN'](/faqs/Troubleshooting/sl-alpn-error) error when connecting to the <Constant name="semantic_layer" />.
- [<Constant name="semantic_layer" /> on-demand course](https://learn.getdbt.com/courses/semantic-layer)
- [<Constant name="semantic_layer" /> FAQs](/docs/use-dbt-semantic-layer/sl-faqs)
