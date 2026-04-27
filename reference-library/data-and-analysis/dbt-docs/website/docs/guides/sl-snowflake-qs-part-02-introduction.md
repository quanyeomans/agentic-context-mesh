## Introduction

The [<Constant name="semantic_layer" />](/docs/use-dbt-semantic-layer/dbt-sl), powered by [MetricFlow](/docs/build/about-metricflow), simplifies the setup of key business metrics. It centralizes definitions, avoids duplicate code, and ensures easy access to metrics in downstream tools. MetricFlow helps manage company metrics easier, allowing you to define metrics in your dbt project and query them in <Constant name="dbt" /> with [MetricFlow commands](/docs/build/metricflow-commands).

import SLCourses from '/snippets/_sl-course.md';

<SLCourses/>

This quickstart guide is designed for <Constant name="dbt" /> users using Snowflake as their data platform. It focuses on building and defining metrics, setting up the <Constant name="semantic_layer" /> in a <Constant name="dbt" /> project, and querying metrics in Google Sheets. 

If you're on different data platforms, you can also follow this guide and will need to modify the setup for the specific platform. See the [users on different platforms](#for-users-on-different-data-platforms) section for more information.

### Prerequisites

- You need a [<Constant name="dbt" />](https://www.getdbt.com/signup/) Trial, Starter, or Enterprise-tier account for all deployments. 
- Have the correct [<Constant name="dbt" /> license](/docs/cloud/manage-access/seats-and-users) and [permissions](/docs/cloud/manage-access/enterprise-permissions) based on your plan:
  <DetailsToggle alt_header="More info on license and permissions">  
  
  - Enterprise-tier &mdash; Developer license with Account Admin permissions. Or "Owner" with a Developer license, assigned Project Creator, Database Admin, or Admin permissions.
  - Starter &mdash; "Owner" access with a Developer license.
  - Trial &mdash; Automatic "Owner" access under a Starter plan trial.
  
  </DetailsToggle>

- Create a [trial Snowflake account](https://signup.snowflake.com/):
  - Select the Enterprise Snowflake edition with ACCOUNTADMIN access. Consider organizational questions when choosing a cloud provider, and refer to Snowflake's [Introduction to Cloud Platforms](https://docs.snowflake.com/en/user-guide/intro-cloud-platforms).
  - Select a cloud provider and region. All cloud providers and regions will work so choose whichever you prefer.
- Complete the [Quickstart for <Constant name="dbt" /> and Snowflake](snowflake-qs.md) guide. 
- Basic understanding of SQL and dbt. For example, you've used dbt before or have completed the [dbt Fundamentals](https://learn.getdbt.com/courses/dbt-fundamentals) course.


### For users on different data platforms

If you're using a data platform other than Snowflake, this guide is also applicable to you. You can adapt the setup for your specific platform by following the account setup and data loading instructions detailed in the following tabs for each respective platform.

The rest of this guide applies universally across all supported platforms, ensuring you can fully leverage the <Constant name="semantic_layer" />.

<Tabs>

<TabItem value="bq" label="BigQuery">

Open a new tab and follow these quick steps for account setup and data loading instructions:

- [Step 2: Create a new GCP project](/guides/bigquery?step=2)
- [Step 3: Create BigQuery dataset](/guides/bigquery?step=3)
- [Step 4: Generate BigQuery credentials](/guides/bigquery?step=4)
- [Step 5: Connect <Constant name="dbt" /> to BigQuery](/guides/bigquery?step=5)

</TabItem>

<TabItem value="databricks" label="Databricks">

Open a new tab and follow these quick steps for account setup and data loading instructions:

- [Step 2: Create a Databricks workspace](/guides/databricks?step=2)
- [Step 3: Load data](/guides/databricks?step=3)
- [Step 4: Connect <Constant name="dbt" /> to Databricks](/guides/databricks?step=4)

</TabItem>

<TabItem value="msfabric" label="Microsoft Fabric">

Open a new tab and follow these quick steps for account setup and data loading instructions:

- [Step 2: Load data into your Microsoft Fabric warehouse](/guides/microsoft-fabric?step=2)
- [Step 3: Connect <Constant name="dbt" /> to Microsoft Fabric](/guides/microsoft-fabric?step=3)

</TabItem>

<TabItem value="redshift" label="Redshift">

Open a new tab and follow these quick steps for account setup and data loading instructions:

- [Step 2: Create a Redshift cluster](/guides/redshift?step=2)
- [Step 3: Load data](/guides/redshift?step=3)
- [Step 4: Connect <Constant name="dbt" /> to Redshift](/guides/redshift?step=3)

</TabItem>

<TabItem value="starburst" label="Starburst Galaxy">

Open a new tab and follow these quick steps for account setup and data loading instructions:

- [Step 2: Load data to an Amazon S3 bucket](/guides/starburst-galaxy?step=2)
- [Step 3: Connect Starburst Galaxy to Amazon S3 bucket data](/guides/starburst-galaxy?step=3)
- [Step 4: Create tables with Starburst Galaxy](/guides/starburst-galaxy?step=4)
- [Step 5: Connect <Constant name="dbt" /> to Starburst Galaxy](/guides/starburst-galaxy?step=5)

</TabItem>

</Tabs>