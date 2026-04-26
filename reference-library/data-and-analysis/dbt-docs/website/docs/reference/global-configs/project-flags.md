---
title: "Project flags"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<File name='dbt_project.yml'>

```yaml

flags:
  <global_config>: <value>

```

</File>

Reference the [table of all flags](/reference/global-configs/about-global-configs#available-flags) to see which global configs are available for setting in [`dbt_project.yml`](/reference/dbt_project.yml).

The `flags` dictionary is the _only_ place you can opt out of [behavior changes](/reference/global-configs/behavior-changes), while the legacy behavior is still supported.

## Config precedence

import SettingFlags from '/snippets/_setting-flags.md';

<SettingFlags />
