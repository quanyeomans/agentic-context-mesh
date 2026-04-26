---
title: "About packages.yml context"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

The following context methods and variables are available when configuring a `packages.yml` file. 

**Available context methods:**
- [env_var](/reference/dbt-jinja-functions/env_var)
    - Use `env_var()` in any dbt YAML file that supports Jinja. Only `packages.yml` and `profiles.yml` support environment variables for [secure values](/docs/build/dbt-tips#yaml-tips) (using the `DBT_ENV_SECRET_` prefix).
- [var](/reference/dbt-jinja-functions/var) (Note: only variables defined with `--vars` are available. Refer to [YAML tips](/docs/build/dbt-tips#yaml-tips) for more information)

**Available context variables:**
- [builtins](/reference/dbt-jinja-functions/builtins)
- [dbt_version](/reference/dbt-jinja-functions/dbt_version)
- [target](/reference/dbt-jinja-functions/target)

## Example usage

The following examples show how to use the different context methods and variables in your `packages.yml`.

Use `builtins` in your `packages.yml`:

```
packages:
  - package: dbt-labs/dbt_utils
    version: "{% if builtins is defined %}0.14.0{% else %}0.13.1{% endif %}"

```

Use `env_var` in your `packages.yml`:

```
packages:
  - package: dbt-labs/dbt_utils
    version: "{{ env_var('DBT_UTILS_VERSION') }}"
```

Use `dbt_version` in your `packages.yml`:

```
packages:
  - package: dbt-labs/dbt_utils
    version: "{% if dbt_version is defined %}0.14.0{% else %}0.13.1{% endif %}"

```

Use `target` in your `packages.yml`:

```

packages:
  - package: dbt-labs/dbt_utils
    version: "{% if target.name == 'prod' %}0.14.0{% else %}0.13.1{% endif %}"

```

## Related docs

- [Packages](/docs/build/packages)
