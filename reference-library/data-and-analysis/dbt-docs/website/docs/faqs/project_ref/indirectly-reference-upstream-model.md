---
title: "Why doesn’t an indirectly referenced upstream public model appear in Explorer?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

For [project dependencies](/docs/mesh/govern/project-dependencies) in <Constant name="mesh" />, [<Constant name="catalog" />](/docs/explore/explore-multiple-projects) only displays directly referenced [public models](/docs/mesh/govern/model-access) from upstream projects, even if an upstream model indirectly depends on another public model.

So for example, if:

- `project_b` adds `project_a` as a dependency
- `project_b`'s model `downstream_c` references `project_a.upstream_b`
- `project_a.upstream_b` references another public model, `project_a.upstream_a`

Then:

- In Explorer, only directly referenced public models (`upstream_b` in this case) appear.
- In the [<Constant name="studio_ide" />](/docs/cloud/studio-ide/develop-in-studio) lineage view, however, `upstream_a` (the indirect dependency) _will_ appear because <Constant name="dbt" /> dynamically resolves the full dependency graph.

This behavior makes sure that <Constant name="catalog" /> only shows the immediate dependencies available to that specific project.
