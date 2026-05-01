---
title: "Administer the Semantic Layer"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

# Administer the Semantic Layer <Lifecycle status="self_service,managed,managed_plus" />

With the dbt <Constant name="semantic_layer" />, you can centrally define business metrics, reduce code duplication and inconsistency, create self-service in downstream tools, and more. This topic shows you how to set up credentials and tokens so that other tools can query the <Constant name="semantic_layer" />.

## Prerequisites

import SetUp from '/snippets/_v2-sl-prerequisites.md';

<SetUp/>

import SLCourses from '/snippets/_sl-course.md';

<SLCourses/>

## Administer the Semantic Layer

import SlSetUp from '/snippets/_new-sl-setup.md';  

<SlSetUp/>


## Next steps

- Now that you've set up your credentials and tokens, start querying your metrics with the [available integrations](/docs/cloud-integrations/avail-sl-integrations).
- [Optimize querying performance](/docs/use-dbt-semantic-layer/sl-cache) using declarative caching.
- [Validate semantic nodes in CI](/docs/deploy/ci-jobs#semantic-validations-in-ci) to ensure code changes made to dbt models don't break these metrics.
- If you haven't already, learn how to [build you metrics and semantic models](/docs/build/build-metrics-intro) in your development tool of choice.
- Learn about commonly asked [<Constant name="semantic_layer" /> FAQs](/docs/use-dbt-semantic-layer/sl-faqs).

## FAQs

<DetailsToggle alt_header="How does caching interact with access controls?">

Cached data is stored separately from the underlying models. If metrics are pulled from the cache, we don’t have the security context applied to those tables at query time.

In the future, we plan to clone credentials, identify the minimum access level needed, and apply those permissions to cached tables.

</DetailsToggle>
