---
title: "On Configuration Change"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

import CreationPrecedence from '/snippets/_creation-precedence.md';

:::info
This functionality is currently only supported for [materialized views](/docs/build/materializations#materialized-view) on a subset of adapters.
:::

The `on_configuration_change` config has three settings:
- `apply` (default) &mdash; Attempt to update the existing database object if possible, avoiding a complete rebuild.
  - *Note:* If any individual configuration change requires a full refresh, a full refresh is performed in lieu of individual alter statements.
- `continue` &mdash; Allow runs to continue while also providing a warning that the object was left untouched.
  - *Note:* This could result in downstream failures as those models may depend on these unimplemented changes.
- `fail` &mdash; Force the entire run to fail if a change is detected.

<Tabs
  groupId="config-languages"
  defaultValue="project-yaml"
  values={[
    { label: 'Project YAML file', value: 'project-yaml', },
    { label: 'Properties YAML file', value: 'property-yaml', },
    { label: 'SQL file config', value: 'config', },
  ]
}>


<TabItem value="project-yaml">

<File name='dbt_project.yml'>

```yaml
models:
  [<resource-path>](/reference/resource-configs/resource-path):
    [+](/reference/resource-configs/plus-prefix)[materialized](/reference/resource-configs/materialized): <materialization_name>
    [+](/reference/resource-configs/plus-prefix)on_configuration_change: apply | continue | fail
```

</File>

</TabItem>


<TabItem value="property-yaml">

<File name='models/properties.yml'>

```yaml

models:
  - name: [<model-name>]
    config:
      [materialized](/reference/resource-configs/materialized): <materialization_name>
      on_configuration_change: apply | continue | fail
```

</File>

</TabItem>


<TabItem value="config">

<File name='models/<model_name>.sql'>

```jinja
{{ config(
    [materialized](/reference/resource-configs/materialized)="<materialization_name>",
    on_configuration_change="apply" | "continue" | "fail"
) }}
```

</File>

</TabItem>

</Tabs>

<CreationPrecedence />
