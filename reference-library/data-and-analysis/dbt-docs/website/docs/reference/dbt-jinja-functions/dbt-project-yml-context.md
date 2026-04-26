---
title: " About dbt_project.yml context"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

The following context methods and variables are available when configuring
resources in the `dbt_project.yml` file. This applies to the `models:`, `seeds:`,
and `snapshots:` keys in the `dbt_project.yml` file.

**Available context methods:**
- [env_var](/reference/dbt-jinja-functions/env_var)
- [var](/reference/dbt-jinja-functions/var) <VersionBlock lastVersion="1.11">(_Note: Only variables defined with `--vars` are available_)</VersionBlock><VersionBlock firstVersion="1.12">(_Note: Variables defined in `vars.yml` or with `--vars` are available_)</VersionBlock>

**Available context variables:**
- [target](/reference/dbt-jinja-functions/target)
- [builtins](/reference/dbt-jinja-functions/builtins)
- [dbt_version](/reference/dbt-jinja-functions/dbt_version)

### Example configuration

<File name='dbt_project.yml'>

```yml
name: my_project
version: 1.0.0

# Configure the models in models/facts/ to be materialized as views
# in development and tables in production/CI contexts

models:
  my_project:
    facts:
      +materialized: "{{ 'view' if target.name == 'dev' else 'table' }}"
```

</File>
