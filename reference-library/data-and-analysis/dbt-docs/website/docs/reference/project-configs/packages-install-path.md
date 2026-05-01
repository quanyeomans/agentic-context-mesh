---
title: "Definition"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

<File name='dbt_project.yml'>

```yml
packages-install-path: directorypath
```

</File>

## Definition
Optionally specify a custom directory where [packages](/docs/build/packages) are installed when you run the `dbt deps` [command](/reference/commands/deps). Note that this directory is usually git-ignored.

## Default
By default, dbt will install packages in the `dbt_packages` directory, i.e. `packages-install-path: dbt_packages`

## Examples
### Install packages in a subdirectory named `packages` instead of `dbt_packages`

<File name='dbt_project.yml'>

```yml
packages-install-path: packages
```

</File>
