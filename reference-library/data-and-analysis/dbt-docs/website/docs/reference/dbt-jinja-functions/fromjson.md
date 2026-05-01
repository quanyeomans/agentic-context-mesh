---
title: "About fromjson context method"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

The `fromjson` context method can be used to deserialize a JSON string into a Python object primitive, eg. a `dict` or `list`.

__Args__:
 * `string`: The JSON string to deserialize (required)
 * `default`: A default value to return if the `string` argument cannot be deserialized (optional)

### Usage:
```
{% set my_json_str = '{"abc": 123}' %}
{% set my_dict = fromjson(my_json_str) %}

{% do log(my_dict['abc']) %}
```
