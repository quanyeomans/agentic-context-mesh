---
title: "[Error] Could not find my_project package"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

If a package name is included in the `search_order` of a project-level `dispatch` config, dbt expects that package to contain macros which are viable candidates for dispatching. If an included package does not contain _any_ macros, dbt will raise an error like:

```shell
Compilation Error
  In dispatch: Could not find package 'my_project'
```

This does not mean the package or root project is missing—it means that any macros from it are missing, and so it is missing from the search spaces available to `dispatch`.

If you've tried the step above and are still experiencing this behavior - reach out to the Support team at support@getdbt.com and we'll be happy to help!
