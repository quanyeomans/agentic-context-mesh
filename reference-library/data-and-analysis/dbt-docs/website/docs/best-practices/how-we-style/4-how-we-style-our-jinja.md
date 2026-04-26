---
title: "How we style our Jinja"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

## Jinja style guide

- 🫧 When using Jinja delimiters, use spaces on the inside of your delimiter, like `{{ this }}` instead of `{{this}}`
- 🆕 Use newlines to visually indicate logical blocks of Jinja.
- 4️⃣ Indent 4 spaces into a Jinja block to indicate visually that the code inside is wrapped by that block.
- ❌ Don't worry (too much) about Jinja whitespace control, focus on your project code being readable. The time you save by not worrying about whitespace control will far outweigh the time you spend in your compiled code where it might not be perfect.

## Examples of Jinja style

```jinja
{% macro make_cool(uncool_id) %}

    do_cool_thing({{ uncool_id }})

{% endmacro %}
```

```sql
select
    entity_id,
    entity_type,
    {% if this %}

        {{ that }},

    {% else %}

        {{ the_other_thing }},

    {% endif %}
    {{ make_cool('uncool_id') }} as cool_id
```
