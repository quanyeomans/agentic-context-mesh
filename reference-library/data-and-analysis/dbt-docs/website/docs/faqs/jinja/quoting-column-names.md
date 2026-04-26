---
title: "Why do I need to quote column names in Jinja?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

In the [macro example](/docs/build/jinja-macros#macros) we passed the column name `amount` quotes:

```sql
{{ cents_to_dollars('amount') }} as amount_usd
```

We have to use quotes to pass the _string_ `'amount'` to the macro.

Without the quotes, the Jinja parser will look for a variable named `amount`. Since this doesn't exist, it will compile to nothing.

Quoting in Jinja can take a while to get used to! The rule is that you're within a Jinja expression or statement (i.e. within `{% ... %}` or `{{ ... }}`), you'll need to use quotes for any arguments that are strings.

Single and double quotes are equivalent in Jinja – just make sure you match them appropriately.

And if you do need to pass a variable as an argument, make sure you [don't nest your curlies](/best-practices/dont-nest-your-curlies).
