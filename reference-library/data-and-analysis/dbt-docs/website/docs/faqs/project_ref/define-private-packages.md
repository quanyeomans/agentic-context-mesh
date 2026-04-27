---
title: "Can I define private packages in the dependencies.yml file?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

It depends on how you're accessing your private packages:

- If you're using [native private packages](/docs/build/packages#native-private-packages), you can define them in the `dependencies.yml` file.
- If you're using the [git token method](/docs/build/packages#git-token-method), you must define them in the `packages.yml` file instead of the `dependencies.yml` file. This is because conditional rendering (like Jinja-in-yaml) is not supported in `dependencies.yml`.
