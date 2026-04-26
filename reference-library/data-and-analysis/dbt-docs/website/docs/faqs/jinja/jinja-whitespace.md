---
title: "My compiled SQL has a lot of spaces and new lines, how can I get rid of it?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

This is known as "whitespace control".

Use a minus sign (`-`, e.g. `{{- ... -}}`, `{%- ... %}`, `{#- ... -#}`) at the start or end of a block to strip whitespace before or after the block (more docs [here](https://jinja.palletsprojects.com/page/templates/#whitespace-control)). Check out the [tutorial on using Jinja](/guides/using-jinja#use-whitespace-control-to-tidy-up-compiled-code) for an example.

Take caution: it's easy to fall down a rabbit hole when it comes to whitespace control!
