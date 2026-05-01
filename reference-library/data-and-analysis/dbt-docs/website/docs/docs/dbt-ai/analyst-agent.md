---
title: "Analyst agent"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

# Analyst agent  <Lifecycle status="beta,managed,managed_plus"/>

The Analyst agent lets you chat with your data and get accurate answers powered by the [dbt Semantic Layer](/docs/use-dbt-semantic-layer/dbt-sl). Unlike generic AI chat interfaces, the Analyst agent provides consistent, explainable results with transparent SQL, lineage, and data policies.

## Prerequisites 

- Enable beta features under **Account settings** > **Personal profile** > **Experimental features**. See [Preview new dbt platform features](/docs/dbt-versions/experimental-features) for steps.
- Have access to [dbt Insights](/docs/explore/dbt-insights) and meet those prerequisites.
- Be on a <Constant name="dbt_platform" /> [Enterprise-tier](https://www.getdbt.com/pricing) plan &mdash; [book a demo](https://www.getdbt.com/contact) to learn more about <Constant name="insights" />.
- Available on all [tenant](/docs/cloud/about-cloud/tenancy) configurations. 
- Have a <Constant name="dbt" /> [developer license](/docs/cloud/manage-access/seats-and-users) with access to <Constant name="insights" />.
- Configured [developer credentials](/docs/cloud/studio-ide/develop-in-studio#get-started-with-the-cloud-ide).

## Using the Analyst agent

import AnalystAgentsCopilot from '/snippets/_analyst_agents-copilot.md';

<AnalystAgentsCopilot/>
