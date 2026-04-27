---
title: "Visualize downstream exposures"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

# Visualize downstream exposures <Lifecycle status="managed,managed_plus" />


Downstream exposures integrate natively with Tableau (Power BI coming soon) and auto-generate downstream lineage in <Constant name="catalog" /> for a richer experience.


As a data team, it’s critical that you have context into the downstream use cases and users of your data products. By leveraging downstream [exposures](/docs/build/exposures) automatically, data teams can:

- Gain a better understanding of how models are used in downstream analytics, improving governance and decision-making.
- Reduce incidents and optimize workflows by linking upstream models to downstream dependencies.
- Automate exposure tracking for supported BI tools, ensuring lineage is always up to date.
- [Orchestrate exposures](/docs/cloud-integrations/orchestrate-exposures) to refresh the underlying data sources during scheduled dbt jobs, improving timeliness and reducing costs. Orchestrating exposures is essentially a way to ensure that your BI tools are updated regularly by using the [<Constant name="dbt" /> job scheduler](/docs/deploy/deployments).
  - For more info on the differences between visualizing and orchestrating exposures, see [Visualize and orchestrate downstream exposures](/docs/cloud-integrations/downstream-exposures).

To configure downstream exposures automatically from dashboards in Tableau, prerequisites, and more &mdash; refer to [Configure downstream exposures](/docs/cloud-integrations/downstream-exposures-tableau).

### Supported plans

Downstream exposures is available on all <Constant name="dbt" /> [Enterprise-tier plans](https://www.getdbt.com/pricing/). Currently, you can only connect to a single Tableau site on the same server.

:::info Tableau Server
If you're using Tableau Server, you need to [allowlist <Constant name="dbt" />'s IP addresses](/docs/cloud/about-cloud/access-regions-ip-addresses) for your <Constant name="dbt" /> region.
:::

import ViewExposures from '/snippets/_auto-exposures-view.md';

<ViewExposures/>
