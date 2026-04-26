---
title: "About toyaml context method"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

The `toyaml` context method can be used to serialize a Python object primitive, eg. a `dict` or `list` to a YAML string.

__Args__:
 * `value`: The value to serialize to YAML (required)
 * `default`: A default value to return if the `value` argument cannot be serialized (optional)

### Usage:
```
{% set my_dict = {"abc": 123} %}
{% set my_yaml_string = toyaml(my_dict) %}

{% do log(my_yaml_string) %}
```
