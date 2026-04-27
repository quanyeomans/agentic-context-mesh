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
macro-paths: [directorypath]
```

</File>

## Definition
Optionally specify a custom list of directories where [macros](/docs/build/jinja-macros#macros) are located. Note that you cannot co-locate models and macros.

## Default
By default, dbt will search for macros in a directory named `macros`. For example, `macro-paths: ["macros"]`. 

import RelativePath from '/snippets/_relative-path.md';

<RelativePath 
path="macro-paths"
absolute="/Users/username/project/macros"
/>

- ✅ **Do**
  - Use relative path:
    ```yml
    macro-paths: ["macros"]
    ```

- ❌ **Don't:**
  - Avoid absolute paths:
    ```yml
    macro-paths: ["/Users/username/project/macros"]
    ```

## Examples
### Use a subdirectory named `custom_macros` instead of `macros`

<File name='dbt_project.yml'>

```yml
macro-paths: ["custom_macros"]
```

</File>
