---
title: "Data health signals"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

# Data health signals <Lifecycle status="preview,self_service,managed,managed_plus" /> 

Data health signals offer a quick, at-a-glance view of data health when browsing your resources in <Constant name="catalog" />. They keep you informed on the status of your resource's health using the indicators **Healthy**, **Caution**, **Degraded**, or **Unknown**.

Note,  we don’t calculate data health for non-dbt resources.

- Supported resources are [models](/docs/build/models), [sources](/docs/build/sources), and [exposures](/docs/build/exposures).
- For accurate health data, ensure the resource is up-to-date and had a recent job run.
- Each data health signal reflects key data health components, such as test success status, missing resource descriptions, missing tests, absence of builds in 30-day windows, [and more](#data-health-signal-criteria).


<Lightbox src="/img/docs/collaborate/dbt-explorer/data-health-signal.jpg" width="55%" title="View data health signals for your models."/> 

## Access data health signals

Access data health signals in the following places:
- In the [search function](/docs/explore/explore-projects#search-resources) or under **Models**, **Sources**, or **Exposures** in the **Resource** tab.  
  - For sources, the data health signal also indicates the [source freshness](/docs/deploy/source-freshness) status.
- In the **Health** column on [each resource's details page](/docs/explore/explore-projects#view-resource-details). Hover over or click the signal to view detailed information.
- In the **Health** column of public models tables.
- In the [DAG lineage graph](/docs/explore/explore-projects#project-lineage). Click any node to open the node details panel where you can view it and its details.
- In [Data health tiles](/docs/explore/data-tile) through an embeddable iFrame and visible in your BI dashboard.

<Lightbox src="/img/docs/collaborate/dbt-explorer/data-health-signal.gif" width="95%" title="Access data health signals in multiple places in dbt Catalog."/> 

## Data health signal criteria

Each resource has a health state that is determined by specific set of criteria. Select the following tabs to view the criteria for that resource type.
<Tabs>
<TabItem value="models" label="Models">

The health state of a model is determined by the following criteria:

| **Health state** | **Criteria**   |
|-------------------|---------------|
| ✅ **Healthy**    | All of the following must be true: - Built successfully in the last run- Built in the last 30 days- Model has tests configured- All tests passed- All upstream [sources are fresh](/docs/build/sources#source-data-freshness) or freshness is not applicable (set to `null`)- Has a description |
| 🟡 **Caution**   | One of the following must be true: - Not built in the last 30 days- Tests are not configured- Tests return warnings- One or more upstream sources are stale:&nbsp;&nbsp;&nbsp;&nbsp;- Has a freshness check configured&nbsp;&nbsp;&nbsp;&nbsp;- Freshness check ran in the past 30 days&nbsp;&nbsp;&nbsp;&nbsp;- Freshness check returned a warning- Missing a description |
| 🔴 **Degraded**  | One of the following must be true: - Model failed to build- Model has failing tests- One or more upstream sources are stale:&nbsp;&nbsp;&nbsp;&nbsp;- Freshness check hasn’t run in the past 30 days&nbsp;&nbsp;&nbsp;&nbsp;- Freshness check returned an error |
| ⚪ **Unknown**    | - Unable to determine health of resource; no job runs have processed the resource.         |

</TabItem>

<TabItem value="sources" label="Sources">

The health state of a source is determined by the following criteria:

| **Health state** | **Criteria**   |
|-------------------|---------------|
| ✅ Healthy	| All of the following must be true: - Freshness check configured- Freshness check passed- Freshness check ran in the past 30 days- Has a description |
| 🟡 Caution	| One of the following must be true: - Freshness check returned a warning- Freshness check not configured- Freshness check not run in the past 30 days- Missing a description |
| 🔴 Degraded	| - Freshness check returned an error |
| ⚪ Unknown	| Unable to determine health of resource; no job runs have processed the resource.     |

</TabItem>

<TabItem value="exposures" label="Exposures">

The health state of an exposure is determined by the following criteria:

| **Health state** | **Criteria**   |
|-------------------|---------------|
| ✅ Healthy	| All of the following must be true: - Underlying sources are fresh- Underlying models built successfully- Underlying models’ tests passing |
| 🟡 Caution	| One of the following must be true: - At least one underlying source’s freshness checks returned a warning- At least one underlying model was skipped- At least one underlying model’s tests returned a warning |   
| 🔴 Degraded	| One of the following must be true: - At least one underlying source’s freshness checks returned an error- At least one underlying model did not build successfully- At least one model’s tests returned an error |

</TabItem>


</Tabs>
