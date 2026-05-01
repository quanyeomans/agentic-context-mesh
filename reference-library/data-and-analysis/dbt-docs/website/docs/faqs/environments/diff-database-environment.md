---
title: "Can I set a different connection at the environment level?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<Constant name="dbt" /> supports [Connections](/docs/cloud/connect-data-platform/about-connections#connection-management), available to all <Constant name="dbt" /> users. Connections allows different data platform connections per environment, eliminating the need to duplicate projects. Projects can only use multiple connections of the same warehouse type. Connections are reusable across projects and environments.

In dbt Core, you can maintain separate production and development environments through the use of [`targets`](/reference/dbt-jinja-functions/target) within a [profile](/docs/local/profiles.yml). dbt Core users can define different targets in their profiles.yml, which means you can have targets for different data warehouses for the same profile.
