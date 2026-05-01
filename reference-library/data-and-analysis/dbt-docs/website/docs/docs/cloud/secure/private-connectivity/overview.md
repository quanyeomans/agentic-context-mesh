---
title: "About private connectivity"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

import SetUpPages from '/snippets/_available-tiers-private-connection.md';
import CloudProviders from '/snippets/_private-connection-across-providers.md';
import PrivateLinkHostnameWarning from '/snippets/_private-connection-hostname-restriction.md';

<SetUpPages />

<PrivateLinkHostnameWarning />

Private connections enables secure communication from any <Constant name="dbt" /> environment to your data platform hosted on a cloud provider, such as [AWS](https://aws.amazon.com/privatelink/), [Azure](https://azure.microsoft.com/en-us/products/private-link), or [GCP](https://cloud.google.com/vpc/docs/private-service-connect), using that provider's private connection technology. Private connections allow <Constant name="dbt" /> customers to meet security and compliance controls as it allows connectivity between <Constant name="dbt" /> and your data platform without traversing the public internet. This feature is supported in most regions across North America, Europe, and Asia, but [contact us](https://www.getdbt.com/contact/) if you have questions about availability.

<CloudProviders />


## Available platforms

Select your cloud platform to view private connectivity options, support matrix, and configuration guides.


<Card
    title="AWS"
    body="Amazon Web Services PrivateLink"
    link="/docs/cloud/secure/private-connectivity/aws/aws-overview"
    icon="dbt-bit"
/>

<Card
    title="Azure"
    body="Microsoft Azure Private Link"
    link="/docs/cloud/secure/private-connectivity/azure/azure-overview"
    icon="dbt-bit"
/>

<Card
    title="GCP"
    body="Google Cloud Platform Private Service Connect"
    link="/docs/cloud/secure/private-connectivity/gcp/gcp-overview"
    icon="dbt-bit"
/>
